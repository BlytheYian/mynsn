"""
run_experiments.py

三組對照實驗（依企畫書第四階段實驗設計）：
  對照組A：傳統純隨機測試（max_iterations=0）
  對照組B：純 LLM 生成（無 SMT 導引，直接給 LLM 疫苗描述，生成 10 個案例）
  實驗組：本系統 IFL（SMT 導引 + LLM 生成，max_iterations=50）

三項核心評估指標：
  ① MC/DC 覆蓋率（目標 100%）
  ② 迭代次數 N_iteration
  ③ 有效測試生成比 = Valid Cases / Total Generated Cases

三項多樣性指標：
  D1 唯一性率       unique/total ≥ 0.70
  D2 Shannon Entropy H ≥ 0.72（布林欄位，Shannon 1948）
  D3 Wasserstein     W ≤ 0.15（整數欄位，Wasserstein 1969）

執行方式：
  $env:IFL_LLM_API_KEY = "你的金鑰"
  $env:IFL_LLM_PROVIDER = "openai"
  $env:IFL_LLM_MODEL = "gpt-4.1-mini"
  python run_experiments.py 2>&1 | Tee-Object -FilePath experiment_results.txt
"""
from __future__ import annotations

import json
import random as _random
import statistics
import sys
import time
import types
import uuid
from pathlib import Path

import re

import ifl_mcdc.layer1.probe_injector as pi
from ifl_mcdc.config import IFLConfig
from ifl_mcdc.layer1.ast_parser import ASTParser
from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.layer1.probe_injector import ProbeInjector
from ifl_mcdc.layer3.domain_validator import DEFAULT_MEDICAL_RULES, DomainValidator
from ifl_mcdc.layer3.llm_sampler import LLMSampler
from ifl_mcdc.models.probe_record import ProbeLog
from ifl_mcdc.orchestrator import IFLOrchestrator
from diversity_reporter import compute_diversity_metrics

VACCINE_PATH = "tests/fixtures/vaccine_eligibility.py"
RUNS = 30
validator = DomainValidator(DEFAULT_MEDICAL_RULES)

# ── 多樣性計算用欄位設定（vaccine_eligibility fixture）────────────
_cfg = IFLConfig()
_BOOL_FIELDS: list[str] = [k for k, v in _cfg.domain_types.items() if v == "bool"]
_INT_FIELDS: dict[str, tuple[int, int]] = {
    k: (_cfg.domain_bounds[k][0], _cfg.domain_bounds[k][1])
    for k, v in _cfg.domain_types.items()
    if v == "int" and k in _cfg.domain_bounds
}

_LLM_SCENARIOS = [
    "年輕低風險個體（18-40歲，非高風險群，近期已接種）",
    "高風險邊界條件（65歲左右，或高風險且接種時間接近180天臨界點）",
    "與典型案例相反的極端情境（最大/最小年齡、過敏、超長間隔）",
]

_PURE_LLM_BASE = """\
你是一個軟體測試工程師。以下是一個疫苗施打資格篩選函式：

def check_vaccine_eligibility(age, high_risk, days_since_last, egg_allergy):
    if ((age >= 65 or (age >= 18 and high_risk))
        and (days_since_last > 180)
        and not egg_allergy):
        return True
    return False

欄位型別與值域（嚴格遵守）：
- age：整數，範圍 0 到 130
- high_risk：JSON 布林值 true 或 false（不得使用 1/0 或字串）
- days_since_last：整數，範圍 0 到 3650
- egg_allergy：JSON 布林值 true 或 false（不得使用 1/0 或字串）
"""


def _build_pure_llm_prompt(cases: list[dict]) -> str:
    """動態建構純 LLM 測試案例生成提示詞，注入已有案例摘要與隨機情境。"""
    prompt = _PURE_LLM_BASE
    if cases:
        recent = cases[-3:]
        lines = ["【已生成案例（請勿重複）】"]
        for idx, c in enumerate(recent, 1):
            vals = ", ".join(f"{k}={v}" for k, v in c.items())
            lines.append(f"#{idx}: {{{vals}}}")
        lines.append("請生成一個與以上所有案例不同的測試案例。")
        prompt += "\n" + "\n".join(lines) + "\n"
    scenario = _random.choice(_LLM_SCENARIOS)
    prompt += f"\n【情境提示】請以「{scenario}」為背景生成測試案例。\n"
    prompt += (
        "\n請生成一個測試案例，以 JSON 格式輸出：\n"
        '{"age": ..., "high_risk": ..., "days_since_last": ..., "egg_allergy": ...}\n'
        "只輸出 JSON，不要任何說明文字。"
    )
    return prompt


# ──────────────────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────────────────

def count_valid(test_suite: list[dict]) -> tuple[int, int]:
    """統計 test_suite 中通過 DomainValidator 的案例數。回傳 (valid, total)。"""
    total = len(test_suite)
    valid = sum(
        1 for tc in test_suite
        if validator.validate(
            json.dumps({k: v for k, v in tc.items() if not k.startswith("__")})
        ).passed
    )
    return valid, total


def summarize(results: list[dict], label: str) -> None:
    """印出一組實驗的 RUNS 次平均摘要。"""
    cov_list   = [r["coverage"]    for r in results]
    iter_list  = [r["iterations"]  for r in results]
    ratio_list = [r["valid_ratio"] for r in results]
    tok_list   = [r["tokens"]      for r in results]
    conv_n     = sum(1 for r in results if r["converged"])

    print(f"\n  收斂率：      {conv_n}/{RUNS} = {conv_n/RUNS:.1%}")
    print(
        f"  平均覆蓋率：  {statistics.mean(cov_list):.1%}"
        f"  (std={statistics.stdev(cov_list):.1%})" if len(cov_list) > 1 else ""
    )
    print(f"  平均迭代次數：{statistics.mean(iter_list):.1f}")
    print(f"  有效生成比：  {statistics.mean(ratio_list):.1%}")
    print(f"  平均 Token：  {statistics.mean(tok_list):.0f}")


# ──────────────────────────────────────────────────────────
# 對照組A：純隨機測試
# ──────────────────────────────────────────────────────────

print("\n" + "=" * 50)
print("對照組A：純隨機測試")
print("=" * 50)

results_A: list[dict] = []
for run in range(RUNS):
    config = IFLConfig(max_iterations=0)
    orch = IFLOrchestrator(config=config)
    t0 = time.time()
    result = orch.run(VACCINE_PATH)
    elapsed = time.time() - t0

    valid, total = count_valid(result.test_suite)
    _cases_a = [{k: v for k, v in tc.items() if not k.startswith("__")}
                for tc in result.test_suite]
    diversity_a = compute_diversity_metrics(_cases_a, _BOOL_FIELDS, _INT_FIELDS)

    rec: dict = {
        "run":         run + 1,
        "converged":   result.converged,
        "coverage":    result.final_coverage,
        "iterations":  result.iteration_count,
        "tokens":      result.total_tokens,
        "time":        elapsed,
        "total_cases": total,
        "valid_cases": valid,
        "valid_ratio": valid / total if total > 0 else 0.0,
        "diversity":   diversity_a,
    }
    if len(_cases_a) < 5:
        rec["note"] = f"n={len(_cases_a)}，僅供參考"
    results_A.append(rec)
    print(f"Run {run+1}: 收斂={result.converged}, "
          f"覆蓋率={result.final_coverage:.1%}, "
          f"迭代={result.iteration_count}, "
          f"有效比={valid}/{total}={valid/total if total > 0 else 0:.1%}")

summarize(results_A, "對照組A")

# ──────────────────────────────────────────────────────────
# 對照組B：純 LLM 生成（無 SMT 導引）
# 直接給 LLM 疫苗邏輯描述，生成 10 個案例後計算 MC/DC 覆蓋率
# ──────────────────────────────────────────────────────────

print("\n" + "=" * 50)
print("對照組B：純 LLM 生成（無 SMT 導引）")
print("=" * 50)

# 預先解析並注入探針（各 run 共用同一份儀表板原始碼）
_parser = ASTParser()
_nodes = _parser.parse_file(VACCINE_PATH)
_src = Path(VACCINE_PATH).read_text(encoding="utf-8")
_injected_src = ProbeInjector(_nodes).inject(_src)

_B_ITERS = 10   # 純 LLM 組每 run 固定生成次數（與 IFLConfig.max_iterations 無關）
print(f"[B] 固定 {_B_ITERS} 次無導引 LLM 呼叫（max_iter={_B_ITERS}，固定值，非 IFLConfig）")


def _parse_json_from_text(text: str) -> dict | None:
    """從 LLM 原始文字中提取 JSON（相容 markdown 包裝）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()

    # 容錯處理：LLM 有時會回傳 Python 風格布林/None（True/False/None）。
    # 轉成合法 JSON（true/false/null）。
    text = text.replace("True", "true").replace("False", "false").replace("None", "null")

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # 再嘗試從最外層括號抓取 JSON（可能含亂碼前後文）
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            candidate = m.group(0)
            candidate = candidate.replace("True", "true").replace("False", "false").replace("None", "null")
            try:
                obj = json.loads(candidate)
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None


results_B: list[dict] = []
config_b = IFLConfig()
backend_b = config_b.llm_backend
sampler_b = LLMSampler(backend_b, validator, retry_delay=0.5)

for run in range(RUNS):
    print(f"\n--- Run {run+1}/{RUNS} ---")
    t0 = time.time()
    total_generated = 0
    valid_count = 0
    total_tokens_b = 0
    cases: list[dict] = []

    # 生成 _B_ITERS 個案例：直接呼叫 backend 以取得原始文字，方便 debug
    for i in range(_B_ITERS):
        total_generated += 1
        raw_text = ""
        try:
            _prompt_text = _build_pure_llm_prompt(cases)
            raw_text = backend_b.complete(_prompt_text)
            _tok = (len(_prompt_text) + len(raw_text)) // 4  # input + output 合計估算
            total_tokens_b += _tok
            # 印出原始回傳（截斷超過 300 字）與 token 估算
            display = raw_text.replace("\n", " ").strip()
            print(f"  [{i+1}/{_B_ITERS}] 原始回傳：{display[:300]}"
                  + ("..." if len(display) > 300 else ""))
            print(f"  [{i+1}/{_B_ITERS}] token 估算：{_tok}（累計={total_tokens_b}）")

            parsed = _parse_json_from_text(raw_text)
            if parsed is None:
                print(f"  [{i+1}/{_B_ITERS}] ✗ JSON 解析失敗（無法從回傳中提取有效 JSON）")
                continue

            vr = validator.validate(json.dumps(parsed))
            vals_str = ", ".join(f"{k}={v}" for k, v in parsed.items())
            if vr.passed:
                valid_count += 1
                cases.append(parsed)
                print(f"  [{i+1}/{_B_ITERS}] ✓ PASS  {vals_str}")
            else:
                reasons = "; ".join(vr.errors) if hasattr(vr, "errors") and vr.errors else "驗證失敗"
                print(f"  [{i+1}/{_B_ITERS}] ✗ FAIL  {vals_str}")
                print(f"            原因：{reasons}")

        except Exception as exc:
            print(f"  [{i+1}/{_B_ITERS}] ✗ 例外  原始回傳={repr(raw_text)[:100]}")
            print(f"            錯誤：{type(exc).__name__}: {exc}")

    # 印出本次 run 有效案例摘要
    print(f"\n  有效案例 {valid_count}/{total_generated}：")
    for idx, c in enumerate(cases, 1):
        vals_str = ", ".join(f"{k}={v}" for k, v in c.items())
        print(f"    T{idx:02d}: {vals_str}")

    # 動態載入儀表板模組並執行案例，計算 MC/DC 覆蓋率
    mod_name = f"_pure_llm_{uuid.uuid4().hex[:8]}"
    mod = types.ModuleType(mod_name)
    exec(compile(_injected_src, mod_name, "exec"), mod.__dict__)  # noqa: S102
    sys.modules[mod_name] = mod

    log = ProbeLog()
    pi._GLOBAL_LOG = log
    mod.__dict__["_ifl_probe"] = pi._ifl_probe
    mod.__dict__["_ifl_record_decision"] = pi._ifl_record_decision

    for case in cases:
        tid = f"T{uuid.uuid4().hex[:8]}"
        setattr(pi._CURRENT_TEST_ID, "value", tid)
        try:
            mod.check_vaccine_eligibility(**case)
        except Exception:
            pass

    matrix = MCDCCoverageEngine().build_matrix(_nodes[0].condition_set, log)
    elapsed = time.time() - t0

    # ── MC/DC 條件激活統計 ──────────────────────────────────
    print(f"\n  MC/DC 條件激活（{len(cases)} 個有效案例）：")
    print(f"    {'條件表達式':<30} {'True':>6} {'False':>6} {'True%':>7}")
    print(f"    {'─'*54}")
    for cond in _nodes[0].condition_set.conditions:
        true_n = false_n = 0
        for c in cases:
            try:
                val = bool(eval(cond.expression, {"__builtins__": {}}, dict(c)))  # noqa: S307
                if val:
                    true_n += 1
                else:
                    false_n += 1
            except Exception:
                pass
        total_n = true_n + false_n
        true_p  = true_n / total_n if total_n > 0 else 0.0
        covered = "✓" if true_n > 0 and false_n > 0 else "✗"
        expr_short = (cond.expression[:27] + "...") if len(cond.expression) > 30 else cond.expression
        print(f"    {covered} {expr_short:<30} {true_n:>6} {false_n:>6} {true_p:>6.1%}")

    loss = matrix.compute_loss()
    total_pairs = len(_nodes[0].condition_set.conditions) * 2
    print(f"\n  MC/DC 覆蓋率：{matrix.coverage_ratio:.1%}  "
          f"（covered={total_pairs - loss}/{total_pairs}，loss={loss}）")

    diversity_b = compute_diversity_metrics(cases, _BOOL_FIELDS, _INT_FIELDS)
    rec = {
        "run":         run + 1,
        "converged":   matrix.compute_loss() == 0,
        "coverage":    matrix.coverage_ratio,
        "iterations":  total_generated,
        "tokens":      total_tokens_b,
        "time":        elapsed,
        "total_cases": total_generated,
        "valid_cases": valid_count,
        "valid_ratio": valid_count / total_generated if total_generated > 0 else 0.0,
        "diversity":   diversity_b,
    }
    results_B.append(rec)
    print(f"\nRun {run+1} 小結：收斂={matrix.compute_loss()==0}, "
          f"覆蓋率={matrix.coverage_ratio:.1%}, "
          f"有效比={valid_count}/{total_generated}="
          f"{valid_count/total_generated:.1%}")

summarize(results_B, "對照組B")

# ──────────────────────────────────────────────────────────
# 實驗組：本系統 IFL（SMT 導引 + LLM 生成）
# ──────────────────────────────────────────────────────────

print("\n" + "=" * 50)
print("實驗組：本系統 IFL（SMT + LLM）")
print("=" * 50)

results_C: list[dict] = []
for run in range(RUNS):
    config = IFLConfig(max_iterations=50)
    orch = IFLOrchestrator(config=config)
    t0 = time.time()
    result = orch.run(VACCINE_PATH)
    elapsed = time.time() - t0

    # 有效比：統一定義 = 通過 DomainValidator 的 LLM 案例數 / 總 LLM 嘗試次數
    # 分母取 all_generated_cases 中 source==llm 的案例數（成功通過 LLMSampler 者）
    all_llm = [tc for tc in result.all_generated_cases if tc.get("__source") == "llm"]
    valid_llm = sum(
        1 for tc in all_llm
        if validator.validate(
            json.dumps({k: v for k, v in tc.items() if not k.startswith("__")})
        ).passed
    )

    # 多樣性：用 all_generated_cases 包含所有 LLM + SMT 補集案例
    _cases_c = [{k: v for k, v in tc.items() if not k.startswith("__")}
                for tc in result.all_generated_cases]
    diversity_c = compute_diversity_metrics(_cases_c, _BOOL_FIELDS, _INT_FIELDS)

    rec = {
        "run":         run + 1,
        "converged":   result.converged,
        "coverage":    result.final_coverage,
        "iterations":  result.iteration_count,
        "tokens":      result.total_tokens,
        "time":        elapsed,
        "total_cases": len(result.test_suite),
        "valid_cases": valid_llm,
        # 有效比 = 通過 DomainValidator 的 LLM 案例數 / 總 LLM 嘗試次數
        "valid_ratio": valid_llm / len(all_llm) if all_llm else 0.0,
        "diversity":   diversity_c,
    }
    results_C.append(rec)
    print(f"Run {run+1}: 收斂={result.converged}, "
          f"覆蓋率={result.final_coverage:.1%}, "
          f"迭代={result.iteration_count}, "
          f"有效比={valid_llm}/{len(all_llm)}={rec['valid_ratio']:.1%}")
    # 失敗原因統計
    _fl = result.failure_log
    _llm_fails = [f for f in _fl if f.startswith("LLM_FAIL")]
    _smt_fails = [f for f in _fl if f.startswith("SMT_")]
    _gate_miss = result.iteration_count - len(result.test_suite) - len(_llm_fails) - len(_smt_fails)
    if _fl or _gate_miss > 0:
        print(f"       失敗統計：LLM生成失敗={len(_llm_fails)}, "
              f"SMT不可行={len(_smt_fails)}, Gate未採納≈{max(0,_gate_miss)}")

summarize(results_C, "實驗組")

# ──────────────────────────────────────────────────────────
# 多樣性彙整輔助函式
# ──────────────────────────────────────────────────────────


def _mean_std(vals: list[float]) -> tuple[float, float] | None:
    """計算 mean ± std，忽略 NaN；無有效值時回傳 None。"""
    valid = [v for v in vals if v == v]   # NaN != NaN
    if not valid:
        return None
    m = statistics.mean(valid)
    s = statistics.stdev(valid) if len(valid) > 1 else 0.0
    return m, s


def _d1_rates(results: list[dict]) -> list[float]:
    return [r["diversity"]["D1"].get("uniqueness_rate", 0.0) for r in results]


def _d2_min_entropy(results: list[dict]) -> list[float]:
    """每次 run 中所有布林欄位的最低 entropy（最保守）。"""
    out: list[float] = []
    for r in results:
        d2 = r["diversity"]["D2"]
        if isinstance(d2, dict) and d2.get("skip"):
            out.append(float("nan"))
        else:
            ents = [v["entropy"] for v in d2.values()
                    if isinstance(v, dict) and "entropy" in v]
            out.append(min(ents) if ents else float("nan"))
    return out


def _d3_max_wass(results: list[dict]) -> list[float]:
    """每次 run 中所有整數欄位的最大 Wasserstein（最保守）。"""
    out: list[float] = []
    for r in results:
        d3 = r["diversity"]["D3"]
        if isinstance(d3, dict) and d3.get("skip"):
            out.append(float("nan"))
        else:
            ws = [v["wasserstein"] for v in d3.values()
                  if isinstance(v, dict) and "wasserstein" in v]
            out.append(max(ws) if ws else float("nan"))
    return out


def _fms_pct(vals: list[float]) -> str:
    r = _mean_std(vals)
    return f"{r[0]:.1%}±{r[1]:.1%}" if r else "    n/a    "


def _fms_f1(vals: list[float]) -> str:
    r = _mean_std(vals)
    return f"{r[0]:.1f}±{r[1]:.1f}" if r else "    n/a    "


def _fms_f2(vals: list[float]) -> str:
    r = _mean_std(vals)
    return f"{r[0]:.2f}±{r[1]:.2f}" if r else "    n/a    "


def _fms_i(vals: list[float]) -> str:
    r = _mean_std(vals)
    return f"{r[0]:.0f}±{r[1]:.0f}" if r else "    n/a    "


# ──────────────────────────────────────────────────────────
# Table 1：三組比較（mean ± std）
# ──────────────────────────────────────────────────────────

_W   = 80
_COL = 20

print("\n\n" + "=" * _W)
print(f"  Table 1：三組比較（mean ± std，n={RUNS}）")
print("=" * _W)
print(f"  {'指標':<26} {'隨機生成':>{_COL}} {'純LLM':>{_COL}} {'IFL':>{_COL}}")
print(f"  {'':─<{26 + _COL * 3 + 2}}  ← 核心指標")

print(f"  {'MC/DC 覆蓋率':<26}"
      f" {_fms_pct([r['coverage']    for r in results_A]):>{_COL}}"
      f" {_fms_pct([r['coverage']    for r in results_B]):>{_COL}}"
      f" {_fms_pct([r['coverage']    for r in results_C]):>{_COL}}")

_conv_A = sum(r["converged"] for r in results_A) / RUNS
_conv_B = sum(r["converged"] for r in results_B) / RUNS
_conv_C = sum(r["converged"] for r in results_C) / RUNS
print(f"  {'收斂率':<26}"
      f" {_conv_A:>{_COL}.1%}"
      f" {_conv_B:>{_COL}.1%}"
      f" {_conv_C:>{_COL}.1%}")

print(f"  {'迭代次數':<26}"
      f" {_fms_f1([r['iterations']  for r in results_A]):>{_COL}}"
      f" {_fms_f1([r['iterations']  for r in results_B]):>{_COL}}"
      f" {_fms_f1([r['iterations']  for r in results_C]):>{_COL}}")

print(f"  {'Token 消耗':<26}"
      f" {_fms_i([r['tokens']       for r in results_A]):>{_COL}}"
      f" {_fms_i([r['tokens']       for r in results_B]):>{_COL}}"
      f" {_fms_i([r['tokens']       for r in results_C]):>{_COL}}")

print(f"  {'有效生成比':<26}"
      f" {_fms_pct([r['valid_ratio'] for r in results_A]):>{_COL}}"
      f" {_fms_pct([r['valid_ratio'] for r in results_B]):>{_COL}}"
      f" {_fms_pct([r['valid_ratio'] for r in results_C]):>{_COL}}")

print(f"  {'':─<{26 + _COL * 3 + 2}}  ← 多樣性指標")

print(f"  {'D1 唯一性率':<26}"
      f" {_fms_pct(_d1_rates(results_A)):>{_COL}}"
      f" {_fms_pct(_d1_rates(results_B)):>{_COL}}"
      f" {_fms_pct(_d1_rates(results_C)):>{_COL}}")

print(f"  {'D2 最低 Entropy':<26}"
      f" {_fms_f2(_d2_min_entropy(results_A)):>{_COL}}"
      f" {_fms_f2(_d2_min_entropy(results_B)):>{_COL}}"
      f" {_fms_f2(_d2_min_entropy(results_C)):>{_COL}}")

print(f"  {'D3 最大 Wass 距離':<26}"
      f" {_fms_f2(_d3_max_wass(results_A)):>{_COL}}"
      f" {_fms_f2(_d3_max_wass(results_B)):>{_COL}}"
      f" {_fms_f2(_d3_max_wass(results_C)):>{_COL}}")

print(f"  {'':═<{26 + _COL * 3 + 2}}")
print("  * A組（純隨機）無 LLM 呼叫，有效比為隨機案例通過 DomainValidator 的比率")
print("  * 有效生成比（三組統一定義）：通過 DomainValidator 的案例數 / 總 LLM 生成次數")
print("    A組：3 個初始隨機案例均通過驗證 → 3/3")
print("    B組：LLM 呼叫成功且通過驗證的數量 / _B_ITERS")
print("    C組：all_generated_cases 中 source=llm 且通過驗證的數量 / 同上分母")
print("  * 隨機組 n≈3（初始隨機案例），D2/D3 樣本不足時顯示 n/a")
print("  * D2 最低 Entropy：所有布林欄位中最低值（最保守指標）")
print("  * D3 最大 Wass 距離：所有整數欄位中最大值（最保守指標）")

# ──────────────────────────────────────────────────────────
# JSON 匯出
# ──────────────────────────────────────────────────────────

_json_path = Path("experiment_results.json")
with _json_path.open("w", encoding="utf-8") as _f:
    json.dump(
        {
            "meta": {
                "fixture":    VACCINE_PATH,
                "runs":       RUNS,
                "bool_fields": _BOOL_FIELDS,
                "int_fields":  {k: list(v) for k, v in _INT_FIELDS.items()},
            },
            "group_A_random": results_A,
            "group_B_llm":    results_B,
            "group_C_ifl":    results_C,
        },
        _f,
        ensure_ascii=False,
        indent=2,
        default=str,
    )
print(f"\n[JSON] 已匯出 -> {_json_path.resolve()}")
