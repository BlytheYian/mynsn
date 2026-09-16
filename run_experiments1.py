"""
run_experiments.py

四個 fixture × 三組對照實驗（依企畫書第四階段實驗設計）：
  對照組A：傳統純隨機測試（max_iterations=0）
  對照組B：純 LLM 生成（無 SMT 導引，b_iters 略多於 IFL 平均迭代次數）
  實驗組C：本系統 IFL（SMT 導引 + LLM 生成）

三項核心評估指標：
  ① MC/DC 覆蓋率（目標 100%）
  ② 迭代次數 N_iteration
  ③ 有效測試生成比 = 通過 DomainValidator 的案例數 / 總生成次數

三項多樣性指標：
  D1 唯一性率       unique/total ≥ 0.70
  D2 Shannon Entropy H ≥ 0.72（布林欄位，Shannon 1948）
  D3 Wasserstein     W ≤ 0.15（整數欄位，Wasserstein 1969）

執行方式：
  # 此腳本已設定為使用 Ollama。請確保 Ollama 服務正在運行。
  # 模型可透過環境變數設定，預設為 'llama3'。
  # $env:IFL_LLM_MODEL = "llama3"

  # 全部四個 fixture（RUNS=30）
  python run_experiments1.py

  # 單一 fixture 驗證
  python run_experiments1.py --fixture loan_approval --runs 3

  # 多個 fixture
  python run_experiments1.py --fixture vaccine_eligibility,loan_approval --runs 10
"""
from __future__ import annotations

import argparse
import json
import random as _random
import re
import statistics
import sys
import time
import types
import uuid
from pathlib import Path

import ifl_mcdc.layer1.probe_injector as pi
from ifl_mcdc.config import IFLConfig
from ifl_mcdc.layer1.ast_parser import ASTParser
from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.layer1.probe_injector import ProbeInjector
from ifl_mcdc.layer3.domain_validator import DomainValidator
from ifl_mcdc.models.probe_record import ProbeLog
from ifl_mcdc.models.validation import DomainRule
from ifl_mcdc.orchestrator import IFLOrchestrator
from diversity_reporter import compute_diversity_metrics

# ──────────────────────────────────────────────────────────
# Fixture 設定
# b_iters：略多於 IFL 平均迭代次數，確保 B 組有同等生成機會（公平比較基準）
# ──────────────────────────────────────────────────────────

FIXTURES: list[dict] = [
    {
        "name": "vaccine_eligibility",
        "k": 5,
        "path": "tests/fixtures/vaccine_eligibility.py",
        "func_name": "check_vaccine_eligibility",
        "func_signature": "check_vaccine_eligibility(age, high_risk, days_since_last, egg_allergy)",
        "domain_context": "流感疫苗施打資格篩選系統",
        "domain_types": {
            "age": "int", "high_risk": "bool",
            "days_since_last": "int", "egg_allergy": "bool",
        },
        "domain_bounds": {"age": [0, 130], "days_since_last": [0, 3650]},
        "max_iter_ifl": 25,
        "b_iters": 10,
    },
    {
        "name": "loan_approval",
        "k": 6,
        "path": "tests/fixtures/loan_approval.py",
        "func_name": "check_loan_approval",
        "func_signature": "check_loan_approval(credit_score, annual_income, loan_amount, employed, has_collateral, bankruptcy_history)",
        "domain_context": "銀行貸款審核系統",
        "domain_types": {
            "credit_score": "int", "annual_income": "int",
            "loan_amount": "int", "employed": "bool",
            "has_collateral": "bool", "bankruptcy_history": "bool",
        },
        "domain_bounds": {
            "credit_score": [300, 850],
            "annual_income": [0, 2000000],
            "loan_amount": [10000, 10000000],
        },
        "max_iter_ifl": 30,
        "b_iters": 12,
    },
    {
        "name": "surgery_risk",
        "k": 9,
        "path": "tests/fixtures/surgery_risk.py",
        "func_name": "check_surgery_risk",
        "func_signature": "check_surgery_risk(age, obese, has_diabetes, has_hypertension, is_smoker, low_hemoglobin, low_platelets, cardiac_history, has_copd)",
        "domain_context": "外科手術術前風險評估系統",
        "domain_types": {
            "age": "int", "obese": "bool", "has_diabetes": "bool",
            "has_hypertension": "bool", "is_smoker": "bool",
            "low_hemoglobin": "bool", "low_platelets": "bool",
            "cardiac_history": "bool", "has_copd": "bool",
        },
        "domain_bounds": {"age": [0, 120]},
        "max_iter_ifl": 45,
        "b_iters": 25,
    },
    {
        "name": "icu_admission",
        "k": 10,
        "path": "tests/fixtures/icu_admission.py",
        "func_name": "check_icu_admission",
        "func_signature": "check_icu_admission(age, low_bp, high_heart_rate, high_resp_rate, high_temp, low_gcs, low_oxygen, low_urine, high_creatinine, sepsis)",
        "domain_context": "ICU 入住評估系統",
        "domain_types": {
            "age": "int", "low_bp": "bool", "high_heart_rate": "bool",
            "high_resp_rate": "bool", "high_temp": "bool", "low_gcs": "bool",
            "low_oxygen": "bool", "low_urine": "bool",
            "high_creatinine": "bool", "sepsis": "bool",
        },
        "domain_bounds": {"age": [0, 130]},
        "max_iter_ifl": 55,
        "b_iters": 30,
    },
]

FIXTURES_BY_NAME: dict[str, dict] = {f["name"]: f for f in FIXTURES}

# ──────────────────────────────────────────────────────────
# 工具：DomainValidator 動態建立
# ──────────────────────────────────────────────────────────


def _make_validator(domain_types: dict, domain_bounds: dict) -> DomainValidator:
    """依 domain_types / domain_bounds 動態建立 DomainValidator。"""
    rules: list[DomainRule] = []
    for field, ftype in domain_types.items():
        if ftype == "bool":
            rules.append(DomainRule(
                field=field,
                description=f"{field} 必須為布林值",
                validator=lambda v: isinstance(v, bool),
            ))
        elif ftype == "int":
            if field in domain_bounds:
                lo, hi = domain_bounds[field][0], domain_bounds[field][1]
                rules.append(DomainRule(
                    field=field,
                    description=f"{field} 必須為整數（{lo}~{hi}）",
                    validator=lambda v, lo=lo, hi=hi: _is_valid_int(v, lo, hi),
                ))
            else:
                rules.append(DomainRule(
                    field=field,
                    description=f"{field} 必須為非負整數",
                    validator=lambda v: _is_valid_int(v, 0, 9999999),
                ))
    return DomainValidator(rules)

def _is_valid_int(v, lo, hi):
    """容忍 LLM 輸出的浮點數或字串格式的整數。"""
    try:
        val = int(float(v))
        return lo <= val <= hi
    except (ValueError, TypeError):
        return False


def _bool_fields(fixture_cfg: dict) -> list[str]:
    return [k for k, v in fixture_cfg["domain_types"].items() if v == "bool"]


def _int_fields(fixture_cfg: dict) -> dict[str, tuple[int, int]]:
    bounds = fixture_cfg.get("domain_bounds", {})
    return {
        k: (bounds[k][0], bounds[k][1])
        for k, v in fixture_cfg["domain_types"].items()
        if v == "int" and k in bounds
    }


# ──────────────────────────────────────────────────────────
# 工具：純 LLM prompt 動態建構
# ──────────────────────────────────────────────────────────

_GENERIC_SCENARIOS = [
    "符合所有條件的典型正例",
    "剛好在臨界點附近的邊界條件（可能通過也可能不通過）",
    "不符合條件的典型反例（與前幾個案例明顯不同）",
]


def _extract_func_body(src: str, func_name: str, max_lines: int = 40) -> str:
    """從原始碼中提取指定函式的主體，最多 max_lines 行。"""
    lines = src.split("\n")
    start = None
    for i, line in enumerate(lines):
        if f"def {func_name}" in line:
            start = i
            break
    if start is None:
        return src[:1500]  # 找不到就截取前 1500 字元
    end = min(start + max_lines, len(lines))
    return "\n".join(lines[start:end])


def _build_pure_llm_prompt(
    fixture_cfg: dict,
    cases: list[dict],
    func_body: str,
) -> str:
    """動態建構純 LLM 提示詞：使用預先提取的函式主體（避免完整原始碼超出 LLM 上下文）。"""
    domain_types = fixture_cfg["domain_types"]
    domain_bounds = fixture_cfg.get("domain_bounds", {})
    domain_context = fixture_cfg["domain_context"]

    field_hints: list[str] = []
    for field, ftype in domain_types.items():
        if ftype == "int":
            if field in domain_bounds:
                lo, hi = domain_bounds[field]
                field_hints.append(f"- {field}：整數，範圍 {lo} 到 {hi}")
            else:
                field_hints.append(f"- {field}：整數，必須為非負整數")
        elif ftype == "bool":
            field_hints.append(
                f"- {field}：JSON 布林值 true 或 false（不得使用 1/0 或字串）"
            )

    example_json = "{" + ", ".join(f'"{k}": ...' for k in domain_types) + "}"

    prompt = (
        f"你是一個軟體測試工程師。以下是一個「{domain_context}」函式：\n\n"
        f"{func_body}\n"
        "欄位型別與值域（嚴格遵守）：\n"
        + "\n".join(field_hints)
    )

    if cases:
        recent = cases[-3:]
        lines = ["\n【已生成案例（請勿重複）】"]
        for idx, c in enumerate(recent, 1):
            vals = ", ".join(f"{k}={v}" for k, v in c.items())
            lines.append(f"#{idx}: {{{vals}}}")
        lines.append("請生成一個與以上所有案例不同的測試案例。")
        prompt += "\n".join(lines) + "\n"

    scenario = _random.choice(_GENERIC_SCENARIOS)
    prompt += f"\n【情境提示】請以「{scenario}」為背景生成測試案例。\n"
    prompt += (
        f"\n請生成一個測試案例，以 JSON 格式輸出：\n"
        f"{example_json}\n"
        "只輸出 JSON，不要任何說明文字。"
    )
    return prompt


def _parse_json_from_text(text: str) -> dict | None:
    """從 LLM 原始文字中提取 JSON（相容 markdown 包裝）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    # 容錯處理：LLM 有時會回傳 Python 風格布林/None（True/False/None）。
    # 轉成合法 JSON（true/false/null）。
    text = text.replace("True", "true").replace("False", "false").replace("None", "null")
    # 移除 Gemma 可能產生的尾隨逗號 (Trailing Commas)
    text = re.sub(r",\s*(\}|\])", r"\1", text)

    # 本地模型常會輸出單引號 JSON，嘗試轉換為標準雙引號
    if "'" in text and '"' not in text:
        text = text.replace("'", '"')

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # 再嘗試從最外層括號抓取 JSON（可能含亂碼或說明文字，本地模型常見現象）
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            candidate = m.group(0)
            candidate = candidate.replace("True", "true").replace("False", "false").replace("None", "null")
            candidate = re.sub(r",\s*(\}|\])", r"\1", candidate)
            if "'" in candidate and '"' not in candidate:
                candidate = candidate.replace("'", '"')
            try:
                obj = json.loads(candidate)
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None


# ──────────────────────────────────────────────────────────
# 工具：統計格式化
# ──────────────────────────────────────────────────────────


def _mean_std(vals: list[float]) -> tuple[float, float] | None:
    valid = [v for v in vals if v == v]  # NaN != NaN
    if not valid:
        return None
    m = statistics.mean(valid)
    s = statistics.stdev(valid) if len(valid) > 1 else 0.0
    return m, s


def _d1_rates(results: list[dict]) -> list[float]:
    return [r["diversity"]["D1"].get("uniqueness_rate", 0.0) for r in results]


def _d2_min_entropy(results: list[dict]) -> list[float]:
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


def _summarize(results: list[dict], runs: int) -> None:
    conv_n = sum(1 for r in results if r["converged"])
    cov_list = [r["coverage"] for r in results]
    iter_list = [r["iterations"] for r in results]
    ratio_list = [r["valid_ratio"] for r in results]
    tok_list = [r["tokens"] for r in results]
    n = len(results)
    print(f"\n  收斂率：      {conv_n}/{n} = {conv_n/n:.1%}")
    if n > 1:
        print(f"  平均覆蓋率：  {statistics.mean(cov_list):.1%}  (std={statistics.stdev(cov_list):.1%})")
    else:
        print(f"  平均覆蓋率：  {statistics.mean(cov_list):.1%}")
    print(f"  平均迭代次數：{statistics.mean(iter_list):.1f}")
    print(f"  有效生成比：  {statistics.mean(ratio_list):.1%}")
    print(f"  平均 Token：  {statistics.mean(tok_list):.0f}")


# ──────────────────────────────────────────────────────────
# 核心：run_one_fixture
# ──────────────────────────────────────────────────────────


def run_one_fixture(
    fixture_cfg: dict,
    runs: int,
    backend,
) -> tuple[list[dict], list[dict], list[dict]]:
    """執行單一 fixture 的三組實驗（A/B/C），回傳 (results_A, results_B, results_C)。"""
    name = fixture_cfg["name"]
    path = fixture_cfg["path"]
    func_name = fixture_cfg["func_name"]
    b_iters = fixture_cfg["b_iters"]
    # b_iters 略多於 IFL 平均迭代次數，確保 B 組有同等生成機會（公平比較基準）

    validator = _make_validator(
        fixture_cfg["domain_types"], fixture_cfg.get("domain_bounds", {})
    )
    bool_fields = _bool_fields(fixture_cfg)
    int_fields = _int_fields(fixture_cfg)

    # 預先解析 fixture 原始碼（B 組共用）
    ast_parser = ASTParser()
    nodes = ast_parser.parse_file(path)
    src = Path(path).read_text(encoding="utf-8")
    injected_src = ProbeInjector(nodes).inject(src)
    # 提取函式主體（最多 40 行），避免完整原始碼超出 LLM 上下文
    func_body = _extract_func_body(src, func_name)

    ifl_kwargs = dict(
        func_name=fixture_cfg["func_name"],
        func_signature=fixture_cfg["func_signature"],
        domain_context=fixture_cfg["domain_context"],
        domain_types=fixture_cfg["domain_types"],
        domain_bounds=fixture_cfg.get("domain_bounds", {}),
    )

    # ── 對照組A：純隨機 ──────────────────────────────────
    print(f"\n{'='*54}")
    print(f"[{name}] 對照組A：純隨機測試")
    print("=" * 54)

    results_A: list[dict] = []
    for run in range(runs):
        config = IFLConfig(max_iterations=0, **ifl_kwargs)
        orch = IFLOrchestrator(config=config)
        t0 = time.time()
        result = orch.run(path)
        elapsed = time.time() - t0

        _cases_a = [{k: v for k, v in tc.items() if not k.startswith("__")}
                    for tc in result.test_suite]
        valid = sum(1 for tc in _cases_a if validator.validate(json.dumps(tc)).passed)
        total = len(_cases_a)
        diversity_a = compute_diversity_metrics(_cases_a, bool_fields, int_fields)

        rec: dict = {
            "run": run + 1, "converged": result.converged,
            "coverage": result.final_coverage, "iterations": result.iteration_count,
            "tokens": result.total_tokens, "time": elapsed,
            "total_cases": total, "valid_cases": valid,
            "valid_ratio": valid / total if total > 0 else 0.0,
            "diversity": diversity_a,
        }
        if total < 5:
            rec["note"] = f"n={total}，僅供參考"
        results_A.append(rec)
        print(f"Run {run+1}/{runs}: 覆蓋率={result.final_coverage:.1%}, "
              f"有效比={valid}/{total}={valid/total if total > 0 else 0:.1%}")

    _summarize(results_A, runs)

    # ── 對照組B：純 LLM ──────────────────────────────────
    print(f"\n{'='*54}")
    print(f"[{name}] 對照組B：純 LLM 生成（無 SMT 導引）")
    print(f"  固定 {b_iters} 次 LLM 呼叫（b_iters 略多於 IFL 平均迭代次數，確保比較公平）")
    print("=" * 54)

    results_B: list[dict] = []
    for run in range(runs):
        print(f"\n--- [{name}] B Run {run+1}/{runs} ---")
        t0 = time.time()
        total_generated = 0
        valid_count = 0
        total_tokens_b = 0
        cases: list[dict] = []

        for i in range(b_iters):
            total_generated += 1
            raw_text = ""
            try:
                _prompt_text = _build_pure_llm_prompt(fixture_cfg, cases, func_body)
                raw_text = backend.complete(_prompt_text)
                _tok = (len(_prompt_text) + len(raw_text)) // 4  # input + output 合計估算
                total_tokens_b += _tok
                display = raw_text.replace("\n", " ").strip()
                print(f"  [{i+1}/{b_iters}] 原始回傳：{display[:200]}"
                      + ("..." if len(display) > 200 else ""))
                print(f"  [{i+1}/{b_iters}] token 估算：{_tok}（累計={total_tokens_b}）")

                parsed = _parse_json_from_text(raw_text)
                if parsed is None:
                    print(f"  [{i+1}/{b_iters}] ✗ JSON 解析失敗")
                    continue

                vr = validator.validate(json.dumps(parsed))
                vals_str = ", ".join(f"{k}={v}" for k, v in parsed.items())
                if vr.passed:
                    valid_count += 1
                    cases.append(parsed)
                    print(f"  [{i+1}/{b_iters}] [OK] PASS  {vals_str}")
                else:
                    print(f"  [{i+1}/{b_iters}] [NO] FAIL  {vals_str}")

            except Exception as exc:
                print(f"  [{i+1}/{b_iters}] [NO] Exception {type(exc).__name__}: {exc}")

        # 有效案例摘要
        print(f"\n  有效案例 {valid_count}/{total_generated}：")
        for idx, c in enumerate(cases, 1):
            print(f"    T{idx:02d}: " + ", ".join(f"{k}={v}" for k, v in c.items()))

        # 動態載入儀表板模組，計算 MC/DC 覆蓋率
        mod_name = f"_pure_llm_{uuid.uuid4().hex[:8]}"
        mod = types.ModuleType(mod_name)
        exec(compile(injected_src, mod_name, "exec"), mod.__dict__)  # noqa: S102
        sys.modules[mod_name] = mod

        log = ProbeLog()
        pi._GLOBAL_LOG = log
        mod.__dict__["_ifl_probe"] = pi._ifl_probe
        mod.__dict__["_ifl_record_decision"] = pi._ifl_record_decision

        for case in cases:
            tid = f"T{uuid.uuid4().hex[:8]}"
            setattr(pi._CURRENT_TEST_ID, "value", tid)
            try:
                getattr(mod, func_name)(**case)
            except Exception:
                pass

        matrix = MCDCCoverageEngine().build_matrix(nodes[0].condition_set, log)
        elapsed = time.time() - t0

        # MC/DC 條件激活統計
        print(f"\n  MC/DC 條件激活（{len(cases)} 個有效案例）：")
        print(f"    {'條件表達式':<30} {'True':>6} {'False':>6} {'True%':>7}")
        print(f"    {'─'*54}")
        for cond in nodes[0].condition_set.conditions:
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
            true_p = true_n / total_n if total_n > 0 else 0.0
            covered = "✓" if true_n > 0 and false_n > 0 else "✗"
            expr_short = (cond.expression[:27] + "...") if len(cond.expression) > 30 else cond.expression
            try:
                print(f"    {covered} {expr_short:<30} {true_n:>6} {false_n:>6} {true_p:>6.1%}")
            except Exception:
                pass

        loss = matrix.compute_loss()
        total_pairs = len(nodes[0].condition_set.conditions) * 2
        print(f"\n  MC/DC 覆蓋率：{matrix.coverage_ratio:.1%}  "
              f"（covered={total_pairs - loss}/{total_pairs}，loss={loss}）")

        diversity_b = compute_diversity_metrics(cases, bool_fields, int_fields)
        rec = {
            "run": run + 1, "converged": matrix.compute_loss() == 0,
            "coverage": matrix.coverage_ratio, "iterations": total_generated,
            "tokens": total_tokens_b, "time": elapsed,
            "total_cases": total_generated, "valid_cases": valid_count,
            "valid_ratio": valid_count / total_generated if total_generated > 0 else 0.0,
            "diversity": diversity_b,
        }
        results_B.append(rec)
        print(f"\nRun {run+1} 小結：覆蓋率={matrix.coverage_ratio:.1%}, "
              f"有效比={valid_count}/{total_generated}={valid_count/total_generated:.1%}")

    _summarize(results_B, runs)

    # ── 實驗組C：IFL ─────────────────────────────────────
    max_iter_ifl = fixture_cfg["max_iter_ifl"]
    print(f"\n{'='*54}")
    print(f"[{name}] 實驗組：IFL（SMT + LLM，max_iter={max_iter_ifl}）")
    print("=" * 54)

    results_C: list[dict] = []
    for run in range(runs):
        config = IFLConfig(max_iterations=max_iter_ifl, **ifl_kwargs)
        # 🌟 [新增] 啟用初始測試調試
        config._debug_initial_tests = True
        orch = IFLOrchestrator(config=config)
        t0 = time.time()
        result = orch.run(path)
        elapsed = time.time() - t0

        # 🌟 [新增] 詳細失敗分析
        print(f"\n--- [{name}] Run {run+1}/{runs} 詳細失敗分析 ---")
        
        # 統計失敗類型
        gate_fail_conds = {}  # condition_id -> 失敗次數
        for detail in result.iteration_details:
            gap_id = detail["gap"]
            if gap_id not in gate_fail_conds:
                gate_fail_conds[gap_id] = 0
            for gate_result in detail["gate_results"]:
                if not gate_result["accepted"]:
                    gate_fail_conds[gap_id] += 1
        
        print(f"\n缺口失敗統計 (無法通過Gate):")
        for gap_id, fail_cnt in sorted(gate_fail_conds.items(), key=lambda x: -x[1]):
            print(f"  {gap_id}: {fail_cnt}次失敗")
        
        # 針對前幾個迭代做詳細分析
        print(f"\n前5個迭代的詳細分析:")
        for detail in result.iteration_details[:5]:
            iteration = detail["iteration"]
            gap_id = detail["gap"]
            print(f"\n  【迭代 {iteration}】{gap_id}")
            
            # 分析生成的案例
            print(f"    生成方式: ", end="")
            if len(detail["generated_cases"]) == 1:
                print("只有LLM (補集無法生成)")
            else:
                print("LLM + Z3補集")
            
            for gen_case in detail["generated_cases"]:
                case_type = gen_case["type"]
                # 分析參數
                values = gen_case["values"]
                bool_vals = {k: v for k, v in values.items() if isinstance(v, bool)}
                int_vals = {k: v for k, v in values.items() if isinstance(v, (int, float))}
                
                if bool_vals:
                    bool_str = ", ".join(f"{k}={'T' if v else 'F'}" for k, v in bool_vals.items())
                    print(f"      {case_type:15} | bool: {bool_str}")
                else:
                    int_str = ", ".join(f"{k}={int(v)}" for k, v in list(int_vals.items())[:2])
                    if len(int_vals) > 2:
                        print(f"      {case_type:15} | int: {int_str}... ({len(int_vals)}個)")
                    else:
                        print(f"      {case_type:15} | int: {int_str}")
            
            # 分析Gate結果
            accepted_cnt = sum(1 for gr in detail["gate_results"] if gr["accepted"])
            print(f"    Gate結果: {accepted_cnt}/{len(detail['gate_results'])} 通過", end="")
            if accepted_cnt == 0:
                # 分析為什麼全部失敗
                losses = [f"{gr['loss_before']}→{gr['loss_after']}" for gr in detail["gate_results"]]
                print(f" | 損失: {losses}")
            else:
                print()
        
        print(f"\n--- 實驗組C Run {run+1} 迭代詳細日誌 ---")
        for detail in result.iteration_details:
            iteration = detail["iteration"]
            gap_id = detail["gap"]
            print(f"\n【迭代 {iteration}】目標缺口: {gap_id}")
            
            # 打印生成的案例
            print(f"  生成結果:")
            for gen_case in detail["generated_cases"]:
                case_type = gen_case["type"]
                values_str = ", ".join(f"{k}={v}" for k, v in gen_case["values"].items())
                print(f"    - {case_type}: {values_str}")
            
            # 打印Gate判決與拒絕原因
            print(f"  Gate判決:")
            for gate_result in detail["gate_results"]:
                side = gate_result["side"]
                accepted = gate_result["accepted"]
                reason = gate_result["reason"]
                loss_before = gate_result["loss_before"]
                loss_after = gate_result["loss_after"]
                status = "[PASS]" if accepted else "[FAIL]"
                print(f"    {status} {side:5} | {reason}")
                print(f"           | loss: {loss_before} -> {loss_after}")
            
            # 如果有LLM錯誤
            if "error" in detail:
                print(f"  [ERROR] LLM: {detail['error']}")
        
        print(f"\n--- Run {run+1} 摘要 ---")

        # 有效比：統一定義 = 通過 DomainValidator 的 LLM 案例數 / 總 LLM 嘗試次數
        all_llm = [tc for tc in result.all_generated_cases if tc.get("__source") == "llm"]
        valid_llm = sum(
            1 for tc in all_llm
            if validator.validate(
                json.dumps({k: v for k, v in tc.items() if not k.startswith("__")})
            ).passed
        )
        _cases_c = [{k: v for k, v in tc.items() if not k.startswith("__")}
                    for tc in result.all_generated_cases]
        diversity_c = compute_diversity_metrics(_cases_c, bool_fields, int_fields)

        rec = {
            "run": run + 1, "converged": result.converged,
            "coverage": result.final_coverage, "iterations": result.iteration_count,
            "tokens": result.total_tokens, "time": elapsed,
            "total_cases": len(result.test_suite), "valid_cases": valid_llm,
            "valid_ratio": valid_llm / len(all_llm) if all_llm else 0.0,
            "diversity": diversity_c,
        }
        results_C.append(rec)
        print(f"Run {run+1}/{runs}: 收斂={result.converged}, "
              f"覆蓋率={result.final_coverage:.1%}, "
              f"迭代={result.iteration_count}, "
              f"有效比={valid_llm}/{len(all_llm)}={rec['valid_ratio']:.1%}")

        _fl = result.failure_log
        _llm_fails = [f for f in _fl if f.startswith("LLM_FAIL")]
        _smt_fails = [f for f in _fl if f.startswith("SMT_")]
        _gate_miss = result.iteration_count - len(result.test_suite) - len(_llm_fails) - len(_smt_fails)
        if _fl or _gate_miss > 0:
            print(f"       失敗統計：LLM生成失敗={len(_llm_fails)}, "
                  f"SMT不可行={len(_smt_fails)}, Gate未採納≈{max(0, _gate_miss)}")

    _summarize(results_C, runs)

    actual_avg_iter_C = statistics.mean([r["iterations"] for r in results_C])
    ratio = fixture_cfg["b_iters"] / actual_avg_iter_C if actual_avg_iter_C > 0 else 0
    print(f"\n  [公平性確認] {name}：")
    print(f"    IFL 實際平均迭代：{actual_avg_iter_C:.1f} 次")
    print(f"    B 組呼叫次數：{fixture_cfg['b_iters']} 次")
    print(f"    比例：{ratio:.1f}x（B 組給予 IFL 的 {ratio:.1f} 倍呼叫機會）")

    return results_A, results_B, results_C


# ──────────────────────────────────────────────────────────
# 輸出：Table 1（單一 fixture）
# ──────────────────────────────────────────────────────────


def print_table1(
    results_A: list[dict],
    results_B: list[dict],
    results_C: list[dict],
    runs: int,
    fixture_name: str,
) -> None:
    W = 86
    COL = 20
    print(f"\n\n{'='*W}")
    print(f"  [{fixture_name}] Table 1：三組比較（mean ± std，n={runs}）")
    print(f"{'='*W}")
    print(f"  {'指標':<26} {'隨機生成':>{COL}} {'純LLM':>{COL}} {'IFL':>{COL}}")
    print(f"  {'':─<{26 + COL * 3 + 2}}  ← 核心指標")

    print(f"  {'MC/DC 覆蓋率':<26}"
          f" {_fms_pct([r['coverage'] for r in results_A]):>{COL}}"
          f" {_fms_pct([r['coverage'] for r in results_B]):>{COL}}"
          f" {_fms_pct([r['coverage'] for r in results_C]):>{COL}}")

    conv_A = sum(r["converged"] for r in results_A) / len(results_A)
    conv_B = sum(r["converged"] for r in results_B) / len(results_B)
    conv_C = sum(r["converged"] for r in results_C) / len(results_C)
    print(f"  {'收斂率':<26} {conv_A:>{COL}.1%} {conv_B:>{COL}.1%} {conv_C:>{COL}.1%}")

    print(f"  {'迭代次數':<26}"
          f" {_fms_f1([r['iterations'] for r in results_A]):>{COL}}"
          f" {_fms_f1([r['iterations'] for r in results_B]):>{COL}}"
          f" {_fms_f1([r['iterations'] for r in results_C]):>{COL}}")

    print(f"  {'Token 消耗':<26}"
          f" {_fms_i([r['tokens'] for r in results_A]):>{COL}}"
          f" {_fms_i([r['tokens'] for r in results_B]):>{COL}}"
          f" {_fms_i([r['tokens'] for r in results_C]):>{COL}}")

    print(f"  {'有效生成比':<26}"
          f" {_fms_pct([r['valid_ratio'] for r in results_A]):>{COL}}"
          f" {_fms_pct([r['valid_ratio'] for r in results_B]):>{COL}}"
          f" {_fms_pct([r['valid_ratio'] for r in results_C]):>{COL}}")

    print(f"  {'':─<{26 + COL * 3 + 2}}  ← 多樣性指標")

    print(f"  {'D1 唯一性率':<26}"
          f" {_fms_pct(_d1_rates(results_A)):>{COL}}"
          f" {_fms_pct(_d1_rates(results_B)):>{COL}}"
          f" {_fms_pct(_d1_rates(results_C)):>{COL}}")

    print(f"  {'D2 最低 Entropy':<26}"
          f" {_fms_f2(_d2_min_entropy(results_A)):>{COL}}"
          f" {_fms_f2(_d2_min_entropy(results_B)):>{COL}}"
          f" {_fms_f2(_d2_min_entropy(results_C)):>{COL}}")

    print(f"  {'D3 最大 Wass 距離':<26}"
          f" {_fms_f2(_d3_max_wass(results_A)):>{COL}}"
          f" {_fms_f2(_d3_max_wass(results_B)):>{COL}}"
          f" {_fms_f2(_d3_max_wass(results_C)):>{COL}}")

    print(f"  {'':═<{26 + COL * 3 + 2}}")
    print("  * A組（純隨機）無 LLM 呼叫，有效比為隨機案例通過 DomainValidator 的比率")
    print("  * 有效生成比（三組統一定義）：通過 DomainValidator 的案例數 / 總生成次數")
    print("  * 隨機組 n≈3（初始隨機案例），D2/D3 樣本不足時顯示 n/a")
    print("  * D2 最低 Entropy：所有布林欄位中最低值（最保守指標）")
    print("  * D3 最大 Wass 距離：所有整數欄位中最大值（最保守指標）")


# ──────────────────────────────────────────────────────────
# 輸出：跨 Fixture 總覽
# ──────────────────────────────────────────────────────────


def print_cross_summary(all_summaries: list[dict]) -> None:
    W = 100
    print(f"\n\n{'═'*W}")
    print(f"  跨 Fixture 總覽（mean，n={all_summaries[0]['runs']} runs per group）")
    print(f"{'═'*W}")
    hdr = (f"  {'Fixture':<22} {'k':>3}  {'隨機覆蓋率':>10}  {'LLM覆蓋率':>10}"
           f"  {'IFL覆蓋率':>10}  {'IFL收斂':>8}  {'IFL迭代':>8}  {'IFL Token':>10}")
    print(hdr)
    print(f"  {'-'*96}")
    for s in all_summaries:
        r_A, r_B, r_C = s["results_A"], s["results_B"], s["results_C"]
        n = len(r_C)
        mean_cov_A = statistics.mean([r["coverage"] for r in r_A])
        mean_cov_B = statistics.mean([r["coverage"] for r in r_B])
        mean_cov_C = statistics.mean([r["coverage"] for r in r_C])
        conv_C = sum(r["converged"] for r in r_C) / n
        mean_iter_C = statistics.mean([r["iterations"] for r in r_C])
        mean_tok_C = statistics.mean([r["tokens"] for r in r_C])
        print(f"  {s['name']:<22} {s['k']:>3}  {mean_cov_A:>10.1%}  {mean_cov_B:>10.1%}"
              f"  {mean_cov_C:>10.1%}  {conv_C:>8.1%}  {mean_iter_C:>8.1f}  {mean_tok_C:>10.0f}")
    print(f"{'═'*W}\n")


# ──────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="IFL MC/DC 對照實驗（四個 fixture）")
    parser.add_argument(
        "--fixture",
        default="all",
        help="fixture 名稱（逗號分隔）或 'all'。可用：" + ", ".join(FIXTURES_BY_NAME),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=30,
        help="每組實驗次數（預設 30）",
    )
    args = parser.parse_args()

    if args.fixture == "all":
        fixtures_to_run = FIXTURES
    else:
        names = [n.strip() for n in args.fixture.split(",")]
        fixtures_to_run = []
        for n in names:
            if n not in FIXTURES_BY_NAME:
                print(f"[ERROR] 未知 fixture: {n!r}。可用：{list(FIXTURES_BY_NAME)}")
                sys.exit(1)
            fixtures_to_run.append(FIXTURES_BY_NAME[n])

    runs = args.runs
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)

    # 所有 fixture 共用同一個 LLM backend（節省初始化開銷）
    global_cfg = IFLConfig(llm_provider="ollama", llm_model="gemma3")
    backend = global_cfg.llm_backend

    # 快速檢查 Ollama 服務狀態
    print(f"\n[INFO] 正在檢查 Ollama 服務 (Model: {global_cfg.llm_model})...")
    try:
        # 嘗試一個極短的 completion 來確認連通性
        test_resp = backend.complete("ping")
        if not test_resp:
            print("[WARNING] Ollama 回傳空字串，請檢查模型是否已下載 (ollama pull llama3)")
        else:
            print("[SUCCESS] Ollama 服務連線正常")
    except Exception as e:
        print(f"[ERROR] 無法連線至 Ollama 服務：{e}")
        print("請確保已執行 'ollama serve' 且已下載對應模型。")
        sys.exit(1)

    all_summaries: list[dict] = []

    for fixture_cfg in fixtures_to_run:
        name = fixture_cfg["name"]
        cache_path = results_dir / f"experiment_{name}.json"

        # 若快取存在且 runs 數量足夠，直接載入（節省費用與時間）
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_runs = cached.get("meta", {}).get("runs", 0)
                if (
                    cached_runs >= runs
                    and len(cached.get("group_A_random", [])) >= runs
                    and len(cached.get("group_B_llm", [])) >= runs
                    and len(cached.get("group_C_ifl", [])) >= runs
                ):
                    print(f"\n[{name}] 快取存在（{cache_path}，runs={cached_runs}），直接載入")
                    results_A = cached["group_A_random"][:runs]
                    results_B = cached["group_B_llm"][:runs]
                    results_C = cached["group_C_ifl"][:runs]
                    print_table1(results_A, results_B, results_C, runs, name)
                    all_summaries.append({
                        "name": name, "k": fixture_cfg["k"], "runs": runs,
                        "results_A": results_A,
                        "results_B": results_B,
                        "results_C": results_C,
                    })
                    continue
            except Exception as e:
                print(f"[{name}] 快取載入失敗（{e}），重新執行")

        print(f"\n\n{'#'*60}")
        print(f"  Fixture：{name}  k={fixture_cfg['k']}  runs={runs}")
        print(f"{'#'*60}")

        try:
            results_A, results_B, results_C = run_one_fixture(fixture_cfg, runs, backend)
        except Exception as e:
            print(f"\n[ERROR] Fixture {name} 執行失敗:")
            print(f"  {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            continue  # 跳過這個 fixture，繼續下一個

        # 立即存檔（每個 fixture 跑完馬上儲存，避免後續 crash 損失數據）
        payload = {
            "meta": {
                "fixture": name,
                "k": fixture_cfg["k"],
                "runs": runs,
                "bool_fields": _bool_fields(fixture_cfg),
                "int_fields": {k: list(v) for k, v in _int_fields(fixture_cfg).items()},
            },
            "group_A_random": results_A,
            "group_B_llm": results_B,
            "group_C_ifl": results_C,
        }
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n[JSON] 已存檔 -> {cache_path.resolve()}")

        print_table1(results_A, results_B, results_C, runs, name)
        all_summaries.append({
            "name": name, "k": fixture_cfg["k"], "runs": runs,
            "results_A": results_A,
            "results_B": results_B,
            "results_C": results_C,
        })

    if len(all_summaries) > 1:
        print_cross_summary(all_summaries)


if __name__ == "__main__":
    main()
