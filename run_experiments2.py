"""
run_experiments2.py — 六個 fixture × 十種方法的 MC/DC 對照實驗

Fixtures：
  vaccine_eligibility, loan_approval, surgery_risk, icu_admission, gpca_alarm, tcas

Methods：
  random         — 傳統純隨機測試
  llm_only_gpt   — 純 GPT-4.1-mini（無 SMT 導引）
  llm_only_gemma — 純 Gemma3（Ollama）
  llm_only_claude— 純 Claude（Anthropic）
  ifl_gpt        — IFL（SMT + GPT-4.1-mini）
  ifl_gemma      — IFL（SMT + Gemma3）
  ifl_claude     — IFL（SMT + Claude）
  pynguin        — Pynguin 自動測試生成
  crosshair      — CrossHair 符號執行（呼叫 crosshair_runner.py）
  coverup        — CoverUp（呼叫 coverup_runner.py）

執行方式：
  # Smoke test（vaccine_eligibility, random + ifl_gpt, 各 3 runs）
  python run_experiments2.py --smoke

  # 單一 fixture + method（自動 --resume 斷點續跑）
  python run_experiments2.py --fixture vaccine_eligibility --method random --runs 5

  # 斷點續跑（跳過已寫入 JSONL 的 run_id）
  python run_experiments2.py --fixture tcas_sir --runs 30 --resume

  # 全部
  python run_experiments2.py --runs 30

輸出格式（每 run 一行 JSONL）：
  {
    "fixture": str, "method": str, "run_id": int,
    "converged": bool, "coverage": float,
    "iterations": int, "tokens": int, "time_sec": float,
    "total_cases": int, "valid_cases": int, "valid_ratio": float,
    "infeasible_pairs": int
  }
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
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
from ifl_mcdc.layer3.llm_sampler import AnthropicBackend, OllamaBackend, OpenAIBackend
from ifl_mcdc.models.probe_record import ProbeLog
from ifl_mcdc.models.validation import DomainRule
from ifl_mcdc.orchestrator import IFLOrchestrator

try:
    from diversity_reporter import compute_diversity_metrics
    _HAS_DIVERSITY = True
except ImportError:
    _HAS_DIVERSITY = False

from ifl_mcdc.layer2.smt_synthesizer import SMTConstraintSynthesizer
from ifl_mcdc.models.coverage_matrix import GapEntry as _SMTGapEntry

# ──────────────────────────────────────────────────────────────────────
# 預計算 infeasible pairs（每 fixture 執行一次，結果快取）
# ──────────────────────────────────────────────────────────────────────

_infeasible_cache: dict[str, set[tuple[str, str]]] = {}


def _precompute_infeasible(cfg: dict) -> set[tuple[str, str]]:
    """用 Z3 掃描所有條件，回傳 infeasible (cond_id, flip_direction) 集合。
    結果快取在 _infeasible_cache，同 fixture 只跑一次。
    """
    key = cfg["name"]
    if key in _infeasible_cache:
        return _infeasible_cache[key]

    nodes = ASTParser().parse_file(cfg["path"])
    synth = SMTConstraintSynthesizer(domain_bounds=cfg.get("domain_bounds", {}))
    domain_types = cfg.get("domain_types", {})

    infeasible: set[tuple[str, str]] = set()
    marked_cond_ids: set[str] = set()

    for n in nodes:
        for cond in n.condition_set.conditions:
            cid = cond.cond_id
            if cid in marked_cond_ids:
                continue
            for flip in ("T2F", "F2T"):
                try:
                    gap = _SMTGapEntry(
                        condition_id=cid, flip_direction=flip,
                        missing_pair_type=flip, estimated_difficulty=1.0,
                    )
                    result = synth.synthesize(n, gap, domain_types=domain_types)
                    if not result.satisfiable:
                        infeasible.add((cid, "T2F"))
                        infeasible.add((cid, "F2T"))
                        marked_cond_ids.add(cid)
                        break
                except Exception:
                    infeasible.add((cid, "T2F"))
                    infeasible.add((cid, "F2T"))
                    marked_cond_ids.add(cid)
                    break

    _infeasible_cache[key] = infeasible
    return infeasible


# ──────────────────────────────────────────────────────────────────────
# Fixture 設定
# ──────────────────────────────────────────────────────────────────────

FIXTURE_CONFIGS: dict[str, dict] = {
    "vaccine_eligibility": {
        "name": "vaccine_eligibility",
        "k": 5,
        "feasible_pairs": 10,
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
    "loan_approval": {
        "name": "loan_approval",
        "k": 6,
        "feasible_pairs": 12,
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
    "surgery_risk": {
        "name": "surgery_risk",
        "k": 9,
        "feasible_pairs": 18,
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
    "icu_admission": {
        "name": "icu_admission",
        "k": 10,
        "feasible_pairs": 20,
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
    "gpca_alarm": {
        "name": "gpca_alarm",
        "k": 10,
        "feasible_pairs": 20,
        "path": "tests/fixtures/gpca_alarm.py",
        "func_name": "gpca_alarm_decision",
        "func_signature": "gpca_alarm_decision(flow_sensor_equipped, flow_rate_ml_hr, programmed_rate_ml_hr, over_duration_min, free_flow, under_duration_min, reservoir_volume_ml, infusion_in_progress, upstream_occlusion, downstream_occlusion)",
        "domain_context": "GPCA 輸液幫浦安全告警系統",
        "domain_types": {
            "flow_sensor_equipped": "bool",
            "flow_rate_ml_hr": "int",
            "programmed_rate_ml_hr": "int",  # 實際函數參數（非 high/low_flow_threshold）
            "over_duration_min": "int",
            "free_flow": "bool",
            "under_duration_min": "int",
            "reservoir_volume_ml": "int",
            "infusion_in_progress": "bool",
            "upstream_occlusion": "bool",
            "downstream_occlusion": "bool",
        },
        "domain_bounds": {
            "flow_rate_ml_hr": [0, 1000],
            "programmed_rate_ml_hr": [1, 1000],  # 1-1000 ml/hr（規格文件）
            "over_duration_min": [0, 60],
            "under_duration_min": [0, 60],
            "reservoir_volume_ml": [0, 500],
        },
        "max_iter_ifl": 55,
        "b_iters": 30,
    },
    "tcas_sir": {
        "name": "tcas_sir",
        "k": 7,
        "feasible_pairs": 16,  # 80 total pairs, 64 infeasible (Own_Below/Above_Threat 互斥 + Other_Capability 互斥)
        "path": "tests/fixtures/tcas_sir.py",
        "func_name": "alt_sep_test",
        "func_signature": "alt_sep_test(Cur_Vertical_Sep, High_Confidence, Two_of_Three_Reports_Valid, Own_Tracked_Alt, Own_Tracked_Alt_Rate, Other_Tracked_Alt, Positive_RA_Alt_Thresh, Up_Separation, Down_Separation, Other_RAC, Other_Capability, Climb_Inhibit)",
        "domain_context": "空中交通防撞系統 (TCAS SIR) 核心決策邏輯",
        "domain_types": {
            "Cur_Vertical_Sep": "int",
            "High_Confidence": "bool",
            "Two_of_Three_Reports_Valid": "bool",
            "Own_Tracked_Alt": "int",
            "Own_Tracked_Alt_Rate": "int",
            "Other_Tracked_Alt": "int",
            "Positive_RA_Alt_Thresh": "int",
            "Up_Separation": "int",
            "Down_Separation": "int",
            "Other_RAC": "int",
            "Other_Capability": "int",
            "Climb_Inhibit": "int",
        },
        "domain_bounds": {
            "Cur_Vertical_Sep": [0, 10000],
            "Own_Tracked_Alt": [0, 50000],
            "Own_Tracked_Alt_Rate": [0, 9999],
            "Other_Tracked_Alt": [0, 50000],
            "Positive_RA_Alt_Thresh": [400, 740],
            "Up_Separation": [0, 10000],
            "Down_Separation": [0, 10000],
            "Other_RAC": [0, 2],
            "Other_Capability": [0, 1],
            "Climb_Inhibit": [0, 1],
        },
        "max_iter_ifl": 150,
        "b_iters": 35,
        "preceding_direction": "none",  # 巢狀 if 結構，前置節點不加路徑約束
    },
    "tcas_sir_copy": {
        "name": "tcas_sir_copy",
        "k": 7,
        "feasible_pairs": 16,  # 與 tcas_sir 相同的結構性不可行（c4/c7 互補、Own_Below/Above 互斥）
        "path": "tests/fixtures/tcas_sir copy.py",
        "func_name": "alt_sep_test",
        "func_signature": "alt_sep_test(Cur_Vertical_Sep, High_Confidence, Two_of_Three_Reports_Valid, Own_Tracked_Alt, Own_Tracked_Alt_Rate, Other_Tracked_Alt, Alt_Layer_Value, Up_Separation, Down_Separation, Other_RAC, Other_Capability, Climb_Inhibit)",
        "domain_context": "空中交通防撞系統 (TCAS SIR)—Alt_Layer_Value 版本（含 IfExp ALIM 查表）",
        "domain_types": {
            "Cur_Vertical_Sep": "int",
            "High_Confidence": "bool",
            "Two_of_Three_Reports_Valid": "bool",
            "Own_Tracked_Alt": "int",
            "Own_Tracked_Alt_Rate": "int",
            "Other_Tracked_Alt": "int",
            "Alt_Layer_Value": "int",       # 0-3，對應 ALIM = {400, 500, 640, 740}
            "Up_Separation": "int",
            "Down_Separation": "int",
            "Other_RAC": "int",
            "Other_Capability": "int",
            "Climb_Inhibit": "int",
        },
        "domain_bounds": {
            "Cur_Vertical_Sep": [0, 10000],
            "Own_Tracked_Alt": [0, 50000],
            "Own_Tracked_Alt_Rate": [0, 9999],
            "Other_Tracked_Alt": [0, 50000],
            "Alt_Layer_Value": [0, 3],           # 離散 4 值：0→400, 1→500, 2→640, 3→740
            "Up_Separation": [0, 10000],
            "Down_Separation": [0, 10000],
            "Other_RAC": [0, 2],
            "Other_Capability": [0, 1],
            "Climb_Inhibit": [0, 1],
        },
        "max_iter_ifl": 150,
        "b_iters": 35,
        "preceding_direction": "none",
    },
}

ALL_FIXTURES = list(FIXTURE_CONFIGS.keys())
ALL_METHODS = [
    "random",
    "llm_only_gpt", "llm_only_gemma", "llm_only_claude",
    "ifl_gpt", "ifl_gemma", "ifl_claude",
    "pynguin", "crosshair", "coverup",
]


# ────────────────────────────────────────────────────────────────────��─
# Output record builder — enforces JSON schema
# ──────────────────────────────────────────────────────────────────────

def _make_record(
    run_id: int,
    converged: bool,
    coverage: float,
    iterations: int,
    tokens: int,
    time_sec: float,
    total_cases: int,
    valid_cases: int,
    infeasible_pairs: int = 0,
    skipped: bool = False,
    error: str = "",
) -> dict:
    """Return a record conforming to the experiment JSON schema."""
    rec: dict = {
        "run_id": run_id,
        "converged": converged,
        "coverage": round(coverage, 6),
        "iterations": iterations,
        "tokens": tokens,
        "time_sec": round(time_sec, 3),
        "total_cases": total_cases,
        "valid_cases": valid_cases,
        "valid_ratio": round(valid_cases / total_cases, 6) if total_cases > 0 else 0.0,
        "infeasible_pairs": infeasible_pairs,
    }
    if skipped:
        rec["skipped"] = True
    if error:
        rec["error"] = error
    return rec


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _is_valid_int(v: object, lo: int, hi: int) -> bool:
    try:
        val = int(float(v)) if not isinstance(v, bool) else None
        return val is not None and lo <= val <= hi
    except (ValueError, TypeError):
        return False


def _make_validator(cfg: dict) -> DomainValidator:
    rules: list[DomainRule] = []
    for field, ftype in cfg["domain_types"].items():
        bounds = cfg.get("domain_bounds", {})
        if ftype == "bool":
            rules.append(DomainRule(
                field=field, description=f"{field} must be bool",
                validator=lambda v: isinstance(v, bool),
            ))
        elif ftype == "int":
            if field in bounds:
                lo, hi = bounds[field]
                rules.append(DomainRule(
                    field=field, description=f"{field} int [{lo},{hi}]",
                    validator=lambda v, lo=lo, hi=hi: _is_valid_int(v, lo, hi),
                ))
            else:
                rules.append(DomainRule(
                    field=field, description=f"{field} non-neg int",
                    validator=lambda v: _is_valid_int(v, 0, 9999999),
                ))
    return DomainValidator(rules)


def _bool_fields(cfg: dict) -> list[str]:
    return [k for k, v in cfg["domain_types"].items() if v == "bool"]


def _int_fields(cfg: dict) -> dict[str, tuple[int, int]]:
    bounds = cfg.get("domain_bounds", {})
    return {k: (bounds[k][0], bounds[k][1])
            for k, v in cfg["domain_types"].items()
            if v == "int" and k in bounds}


def _parse_json_text(text: str) -> dict | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    text = (text.replace("True", "true").replace("False", "false")
            .replace("None", "null"))
    text = re.sub(r",\s*(\}|\])", r"\1", text)
    if "'" in text and '"' not in text:
        text = text.replace("'", '"')
    for candidate in (text, re.search(r"\{[\s\S]*\}", text)):
        if candidate is None:
            continue
        s = candidate if isinstance(candidate, str) else candidate.group(0)
        s = (re.sub(r",\s*(\}|\])", r"\1", s)
             .replace("True", "true").replace("False", "false")
             .replace("'", '"'))
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _build_llm_prompt(cfg: dict, cases: list[dict], func_body: str) -> str:
    domain_types = cfg["domain_types"]
    domain_bounds = cfg.get("domain_bounds", {})
    hints = []
    for field, ftype in domain_types.items():
        if ftype == "int":
            if field in domain_bounds:
                lo, hi = domain_bounds[field]
                hints.append(f"- {field}: int, range [{lo}, {hi}]")
            else:
                hints.append(f"- {field}: int, non-negative")
        elif ftype == "bool":
            hints.append(f"- {field}: JSON bool (true/false)")
    example = "{" + ", ".join(f'"{k}": ...' for k in domain_types) + "}"
    prompt = (
        f"You are a software test engineer. Function under test:\n\n"
        f"{func_body}\n\nField types and constraints:\n" + "\n".join(hints)
    )
    if cases:
        lines = ["\n[Already generated — do NOT repeat]:"]
        for idx, c in enumerate(cases[-3:], 1):
            lines.append(f"#{idx}: {c}")
        lines.append("Generate ONE new test case different from all above.")
        prompt += "\n".join(lines) + "\n"
    prompt += f"\nOutput ONE JSON object only:\n{example}\nNo explanation text."
    return prompt


def _extract_func_body(src: str, func_name: str, max_lines: int = 40) -> str:
    for i, line in enumerate(src.split("\n")):
        if f"def {func_name}" in line:
            lines = src.split("\n")
            return "\n".join(lines[i: min(i + max_lines, len(lines))])
    return src[:1500]


def _compute_coverage(cfg: dict, cases: list[dict], nodes: list,
                      injected_src: str) -> float:
    if not cases:
        return 0.0
    mod_name = f"_exp_{uuid.uuid4().hex[:8]}"
    mod = types.ModuleType(mod_name)
    exec(compile(injected_src, mod_name, "exec"), mod.__dict__)  # noqa: S102
    sys.modules[mod_name] = mod
    log = ProbeLog()
    pi._GLOBAL_LOG = log
    mod.__dict__["_ifl_probe"] = pi._ifl_probe
    mod.__dict__["_ifl_record_decision"] = pi._ifl_record_decision
    func = getattr(mod, cfg["func_name"])
    for case in cases:
        setattr(pi._CURRENT_TEST_ID, "value", f"T{uuid.uuid4().hex[:8]}")
        try:
            func(**case)
        except Exception:
            pass
    # 預計算 infeasible pairs（Z3 掃描，快取），確保分母只計可行對
    infeasible_pairs = _precompute_infeasible(cfg)
    engine = MCDCCoverageEngine()
    total_covered = total_feasible = 0
    for dn in nodes:
        matrix = engine.build_matrix(dn.condition_set, log)
        for cond in dn.condition_set.conditions:
            for flip in ("T2F", "F2T"):
                if (cond.cond_id, flip) in infeasible_pairs:
                    matrix.mark_infeasible(cond.cond_id, flip)
        total_feasible += matrix.feasible_count
        total_covered += len(matrix._covered - matrix._infeasible)
    return total_covered / total_feasible if total_feasible > 0 else 0.0


def _diversity(cfg: dict, cases: list[dict]) -> dict:
    if not _HAS_DIVERSITY or not cases:
        return {}
    try:
        return compute_diversity_metrics(cases, _bool_fields(cfg), _int_fields(cfg))
    except Exception:
        return {}


def _get_backend(lm: str):
    if lm == "gpt":
        return OpenAIBackend("gpt-4.1-mini", os.environ.get("OPENAI_API_KEY", ""))
    if lm == "gemma":
        return OllamaBackend("gemma3:latest",
                             os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    if lm == "claude":
        return AnthropicBackend("claude-sonnet-4-6",
                                os.environ.get("ANTHROPIC_API_KEY", ""))
    raise ValueError(f"Unknown LM: {lm!r}")


def _ifl_kwargs(cfg: dict) -> dict:
    return dict(
        func_name=cfg["func_name"],
        func_signature=cfg["func_signature"],
        domain_context=cfg["domain_context"],
        domain_types=cfg["domain_types"],
        domain_bounds=cfg.get("domain_bounds", {}),
        preceding_direction=cfg.get("preceding_direction", "sequential"),
    )


# ──────────────────────────────────────────────────────────────────────
# Resume helper
# ──────────────────────────────────────────────────────────────────────

def _completed_run_ids(jsonl_path: Path, fixture: str, method: str) -> set[int]:
    """Return run_ids already recorded in jsonl_path for this (fixture, method)."""
    completed: set[int] = set()
    if not jsonl_path.exists():
        return completed
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("fixture") == fixture and rec.get("method") == method:
                run_id = rec.get("run_id")
                if isinstance(run_id, int):
                    completed.add(run_id)
        except json.JSONDecodeError:
            pass
    return completed


# ──────────────────────────────────────────────────────────────────────
# Method runners — each takes run_ids: list[int] and returns list[dict]
# ──────────────────────────────────────────────────────────────────────

def _run_random(cfg: dict, run_ids: list[int]) -> list[dict]:
    path = cfg["path"]
    src = Path(path).read_text(encoding="utf-8")
    nodes = ASTParser().parse_file(path)
    injected_src = ProbeInjector(nodes).inject(src)
    results = []
    for run_id in run_ids:
        config = IFLConfig(max_iterations=0, **_ifl_kwargs(cfg))
        orch = IFLOrchestrator(config=config)
        t0 = time.time()
        result = orch.run(cfg["path"])
        elapsed = time.time() - t0
        cases = [{k: v for k, v in tc.items() if not k.startswith("__")}
                 for tc in result.test_suite]
        n = len(cases)
        # 使用有效覆蓋率（分母排除 infeasible pairs）
        coverage = _compute_coverage(cfg, cases, nodes, injected_src)
        rec = _make_record(
            run_id=run_id,
            converged=coverage >= 1.0,
            coverage=coverage,
            iterations=result.iteration_count,
            tokens=result.total_tokens,
            time_sec=elapsed,
            total_cases=n,
            valid_cases=n,
        )
        rec["diversity"] = _diversity(cfg, cases)
        results.append(rec)
        print(f"  run_id={run_id}: coverage={coverage:.1%}  "
              f"iter={result.iteration_count}  t={elapsed:.1f}s")
    return results


def _run_llm_only(cfg: dict, run_ids: list[int], lm: str) -> list[dict]:
    try:
        backend = _get_backend(lm)
        backend.complete("ping", max_tokens=5)
    except Exception as exc:
        msg = str(exc)
        print(f"  [SKIP] {lm} backend unavailable: {msg}")
        return [_make_record(run_id=i, converged=False, coverage=0.0,
                             iterations=0, tokens=0, time_sec=0.0,
                             total_cases=0, valid_cases=0,
                             skipped=True, error=msg)
                for i in run_ids]

    path = cfg["path"]
    src = Path(path).read_text(encoding="utf-8")
    nodes = ASTParser().parse_file(path)
    injected_src = ProbeInjector(nodes).inject(src)
    func_body = _extract_func_body(src, cfg["func_name"])
    validator = _make_validator(cfg)
    b_iters = cfg["b_iters"]

    results = []
    for run_id in run_ids:
        t0 = time.time()
        cases: list[dict] = []
        total_gen = valid_count = total_tokens = 0
        print(f"\n  --- llm_only_{lm} run_id={run_id} ({b_iters} iters) ---")
        for i in range(b_iters):
            total_gen += 1
            try:
                prompt = _build_llm_prompt(cfg, cases, func_body)
                backend.last_usage = None  # 避免讀到上一次呼叫留下的舊值
                raw = backend.complete(prompt)
                usage = backend.last_usage
                if usage is not None:
                    # 後端回報的真實 token 數（與 IFL 側 llm_sampler.py 口徑一致）
                    total_tokens += usage["prompt_tokens"] + usage["completion_tokens"]
                else:
                    # 無法取得真實用量時的備援：prompt+completion 都算，字元數粗估
                    total_tokens += (len(prompt) + len(raw)) // 4
                parsed = _parse_json_text(raw)
                if parsed is None:
                    print(f"    [{i+1}/{b_iters}] JSON fail")
                    continue
                vr = validator.validate(json.dumps(parsed))
                if vr.passed:
                    valid_count += 1
                    cases.append(parsed)
                    print(f"    [{i+1}/{b_iters}] OK  {list(parsed.values())}")
                else:
                    print(f"    [{i+1}/{b_iters}] DOMAIN FAIL")
            except Exception as exc:
                print(f"    [{i+1}/{b_iters}] ERROR: {exc}")

        coverage = _compute_coverage(cfg, cases, nodes, injected_src)
        elapsed = time.time() - t0
        rec = _make_record(
            run_id=run_id,
            converged=coverage >= 1.0,
            coverage=coverage,
            iterations=b_iters,
            tokens=total_tokens,
            time_sec=elapsed,
            total_cases=total_gen,
            valid_cases=valid_count,
        )
        rec["diversity"] = _diversity(cfg, cases)
        results.append(rec)
        print(f"  run_id={run_id} coverage={coverage:.1%}  valid={valid_count}/{total_gen}")
    return results


def _run_ifl(cfg: dict, run_ids: list[int], lm: str) -> list[dict]:
    provider = {"gpt": "openai", "gemma": "ollama", "claude": "anthropic"}[lm]
    model = {"gpt": "gpt-4.1-mini", "gemma": "gemma3:latest",
             "claude": "claude-sonnet-4-6"}[lm]
    api_key_env = {"gpt": "OPENAI_API_KEY", "gemma": "", "claude": "ANTHROPIC_API_KEY"}[lm]
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""

    try:
        backend = _get_backend(lm)
        backend.complete("ping", max_tokens=5)
    except Exception as exc:
        msg = str(exc)
        print(f"  [SKIP] {lm} backend unavailable: {msg}")
        return [_make_record(run_id=i, converged=False, coverage=0.0,
                             iterations=0, tokens=0, time_sec=0.0,
                             total_cases=0, valid_cases=0,
                             skipped=True, error=msg)
                for i in run_ids]

    validator = _make_validator(cfg)
    results = []
    for run_id in run_ids:
        config = IFLConfig(
            llm_provider=provider, llm_model=model, llm_api_key=api_key,
            max_iterations=cfg["max_iter_ifl"], **_ifl_kwargs(cfg),
        )
        orch = IFLOrchestrator(config=config)
        t0 = time.time()
        try:
            result = orch.run(cfg["path"])
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"  [ERROR] run_id={run_id}: {type(exc).__name__}: {exc}")
            results.append(_make_record(
                run_id=run_id, converged=False, coverage=0.0,
                iterations=0, tokens=0, time_sec=elapsed,
                total_cases=0, valid_cases=0, error=str(exc)[:200],
            ))
            continue
        elapsed = time.time() - t0
        all_llm = [tc for tc in result.all_generated_cases
                   if tc.get("__source") == "llm"]
        valid_llm = sum(
            1 for tc in all_llm
            if validator.validate(
                json.dumps({k: v for k, v in tc.items() if not k.startswith("__")})
            ).passed
        )
        cases = [{k: v for k, v in tc.items() if not k.startswith("__")}
                 for tc in result.all_generated_cases]
        rec = _make_record(
            run_id=run_id,
            converged=result.converged,
            coverage=result.final_coverage,
            iterations=result.iteration_count,
            tokens=result.total_tokens,
            time_sec=elapsed,
            total_cases=len(result.all_generated_cases),
            valid_cases=valid_llm,
            infeasible_pairs=len(result.infeasible_paths),
        )
        rec["diversity"] = _diversity(cfg, cases)
        results.append(rec)
        print(f"  run_id={run_id}: converged={result.converged}  "
              f"coverage={result.final_coverage:.1%}  iter={result.iteration_count}  "
              f"t={elapsed:.1f}s  infeasible={len(result.infeasible_paths)}")
    return results


def _run_crosshair(cfg: dict, run_ids: list[int]) -> list[dict]:
    results = []
    for run_id in run_ids:
        try:
            proc = subprocess.run(
                [sys.executable, "crosshair_runner.py",
                 "--fixture", cfg["name"], "--run-id", str(run_id)],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[:200])
            raw = json.loads(proc.stdout.strip())
            rec = _make_record(
                run_id=raw.get("run_id", run_id),
                converged=raw.get("converged", False),
                coverage=raw.get("coverage", 0.0),
                iterations=raw.get("iterations", 0),
                tokens=raw.get("tokens", 0),
                time_sec=raw.get("time_sec", 0.0),
                total_cases=raw.get("total_cases", 0),
                valid_cases=raw.get("valid_cases", 0),
                infeasible_pairs=raw.get("infeasible_pairs", 0),
                skipped=raw.get("skipped", False),
                error=raw.get("error", ""),
            )
        except Exception as exc:
            rec = _make_record(run_id=run_id, converged=False, coverage=0.0,
                               iterations=0, tokens=0, time_sec=0.0,
                               total_cases=0, valid_cases=0,
                               skipped=True, error=str(exc))
        results.append(rec)
        print(f"  run_id={run_id}: coverage={rec['coverage']:.1%}  "
              f"cases={rec['total_cases']}  t={rec['time_sec']:.1f}s")
    return results


def _run_pynguin(cfg: dict, run_ids: list[int]) -> list[dict]:
    import ast as _ast, tempfile
    _pynguin_env = dict(os.environ)
    _pynguin_env["PYNGUIN_DANGER_AWARE"] = "1"
    try:
        subprocess.run([sys.executable, "-m", "pynguin", "--version"],
                       capture_output=True, timeout=5, check=True,
                       env=_pynguin_env)
    except Exception:
        msg = "pynguin not installed (pip install pynguin)"
        print(f"  [SKIP] {msg}")
        return [_make_record(run_id=i, converged=False, coverage=0.0,
                             iterations=0, tokens=0, time_sec=0.0,
                             total_cases=0, valid_cases=0,
                             skipped=True, error=msg)
                for i in run_ids]

    path = cfg["path"]
    func_name = cfg["func_name"]
    param_names = list(cfg["domain_types"].keys())
    src = Path(path).read_text(encoding="utf-8")
    nodes = ASTParser().parse_file(path)
    injected_src = ProbeInjector(nodes).inject(src)

    results = []
    for run_id in run_ids:
        t0 = time.time()
        collected: list[dict] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                subprocess.run(
                    [sys.executable, "-m", "pynguin",
                     "--project-path", str(Path(path).parent),
                     "--output-path", tmpdir,
                     "--module-name", Path(path).stem,
                     "--maximum_search_time", "30"],
                    capture_output=True, text=True, timeout=60,
                    env=_pynguin_env,
                )
                # 解析 pynguin 產生的測試檔案，提取函式呼叫的引數
                for test_file in Path(tmpdir).glob("test_*.py"):
                    try:
                        tree = _ast.parse(test_file.read_text(encoding="utf-8"))
                        for func_def in _ast.walk(tree):
                            if not isinstance(func_def, _ast.FunctionDef):
                                continue
                            # 先收集全部賦值（順序走訪 body）
                            local_vals: dict[str, object] = {}
                            for stmt in _ast.walk(func_def):
                                if isinstance(stmt, _ast.Assign):
                                    for tgt in stmt.targets:
                                        if isinstance(tgt, _ast.Name):
                                            try:
                                                local_vals[tgt.id] = _ast.literal_eval(stmt.value)
                                            except Exception:
                                                pass
                            # 再找函式呼叫（Expr 或 Assign 兩種形式）
                            for node in _ast.walk(func_def):
                                call = None
                                if isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Call):
                                    call = node.value
                                elif isinstance(node, _ast.Assign) and isinstance(node.value, _ast.Call):
                                    call = node.value
                                if call is None:
                                    continue
                                fname = ""
                                if isinstance(call.func, _ast.Attribute):
                                    fname = call.func.attr
                                elif isinstance(call.func, _ast.Name):
                                    fname = call.func.id
                                if fname != func_name:
                                    continue
                                args_vals = []
                                for a in call.args:
                                    try:
                                        args_vals.append(_ast.literal_eval(a))
                                    except Exception:
                                        if isinstance(a, _ast.Name) and a.id in local_vals:
                                            args_vals.append(local_vals[a.id])
                                if len(args_vals) == len(param_names):
                                    collected.append(dict(zip(param_names, args_vals)))
                    except Exception:
                        pass
            except Exception as exc:
                print(f"  [Pynguin run_id={run_id}] error: {exc}")
        coverage = _compute_coverage(cfg, collected, nodes, injected_src)
        elapsed = time.time() - t0
        rec = _make_record(
            run_id=run_id, converged=coverage >= 1.0,
            coverage=coverage, iterations=0, tokens=0,
            time_sec=elapsed, total_cases=0, valid_cases=0,
        )
        results.append(rec)
        print(f"  run_id={run_id}: coverage={coverage:.1%}  t={elapsed:.1f}s")
    return results


def _run_coverup(cfg: dict, run_ids: list[int]) -> list[dict]:
    results = []
    for run_id in run_ids:
        try:
            proc = subprocess.run(
                [sys.executable, "coverup_runner.py",
                 "--fixture", cfg["name"], "--run-id", str(run_id)],
                capture_output=True, text=True, timeout=180,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[:200])
            raw = json.loads(proc.stdout.strip())
            rec = _make_record(
                run_id=raw.get("run_id", run_id),
                converged=raw.get("converged", False),
                coverage=raw.get("coverage", 0.0),
                iterations=raw.get("iterations", 0),
                tokens=raw.get("tokens", 0),
                time_sec=raw.get("time_sec", 0.0),
                total_cases=raw.get("total_cases", 0),
                valid_cases=raw.get("valid_cases", 0),
                infeasible_pairs=raw.get("infeasible_pairs", 0),
                skipped=raw.get("skipped", False),
                error=raw.get("error", ""),
            )
        except Exception as exc:
            rec = _make_record(run_id=run_id, converged=False, coverage=0.0,
                               iterations=0, tokens=0, time_sec=0.0,
                               total_cases=0, valid_cases=0,
                               skipped=True, error=str(exc))
        results.append(rec)
        print(f"  run_id={run_id}: coverage={rec['coverage']:.1%}  "
              f"cases={rec['total_cases']}  t={rec['time_sec']:.1f}s")
    return results


def run_method(cfg: dict, method: str, run_ids: list[int]) -> list[dict]:
    """Dispatch to the right runner."""
    if method == "random":
        return _run_random(cfg, run_ids)
    if method.startswith("llm_only_"):
        return _run_llm_only(cfg, run_ids, method[len("llm_only_"):])
    if method.startswith("ifl_"):
        return _run_ifl(cfg, run_ids, method[len("ifl_"):])
    if method == "crosshair":
        return _run_crosshair(cfg, run_ids)
    if method == "pynguin":
        return _run_pynguin(cfg, run_ids)
    if method == "coverup":
        return _run_coverup(cfg, run_ids)
    raise ValueError(f"Unknown method: {method!r}")


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="IFL-MCDC 六 fixture × 十方法實驗")
    parser.add_argument("--smoke", action="store_true",
                        help="Smoke test: vaccine_eligibility × random+ifl_gpt × 3 runs")
    parser.add_argument("--fixture", default="all",
                        help="逗號分隔 or 'all'. 可用: " + ", ".join(ALL_FIXTURES))
    parser.add_argument("--method", default="all",
                        help="逗號分隔 or 'all'. 可用: " + ", ".join(ALL_METHODS))
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--skip-methods", default="",
                        help="跳過指定 method，逗號分隔，例如 ifl_gemma,llm_only_gemma")
    parser.add_argument("--resume", action="store_true",
                        help="(已預設開啟) 保留舊參數相容性")
    args = parser.parse_args()

    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)

    skip_methods = {m.strip() for m in args.skip_methods.split(",") if m.strip()}

    if args.smoke:
        fixtures_to_run = ["vaccine_eligibility"]
        methods_to_run = ["random", "ifl_gpt"]
        total_runs = 3
        raw_path = results_dir / "smoke_test.jsonl"
        print("\n" + "=" * 60)
        print("  SMOKE TEST")
        print(f"  fixture:  {fixtures_to_run}")
        print(f"  methods:  {methods_to_run}")
        print(f"  runs:     {total_runs}")
        print(f"  output:   {raw_path}")
        print("=" * 60)
    else:
        fixtures_to_run = ALL_FIXTURES if args.fixture == "all" else [
            f.strip() for f in args.fixture.split(",")]
        methods_to_run = ALL_METHODS if args.method == "all" else [
            m.strip() for m in args.method.split(",")]
        total_runs = args.runs
        raw_path = results_dir / "raw_results.jsonl"

    for fx_name in fixtures_to_run:
        if fx_name not in FIXTURE_CONFIGS:
            print(f"[ERROR] Unknown fixture: {fx_name!r}")
            sys.exit(1)
    for method in methods_to_run:
        if method not in ALL_METHODS:
            print(f"[ERROR] Unknown method: {method!r}")
            sys.exit(1)

    for fx_name in fixtures_to_run:
        cfg = FIXTURE_CONFIGS[fx_name]
        print(f"\n{'#' * 60}")
        print(f"  Fixture: {fx_name}  k={cfg['k']}  feasible_pairs={cfg['feasible_pairs']}")
        print(f"{'#' * 60}")

        for method in methods_to_run:
            if method in skip_methods:
                print(f"\n[{fx_name}/{method}] SKIPPED (--skip-methods)")
                continue

            # Always resume from raw_path
            all_run_ids = list(range(1, total_runs + 1))
            done = _completed_run_ids(raw_path, fx_name, method)
            run_ids = [r for r in all_run_ids if r not in done]
            if done:
                print(f"\n[{fx_name}/{method}] Resuming: {len(done)} already done, "
                      f"{len(run_ids)} remaining")

            if not run_ids:
                print(f"\n[{fx_name}/{method}] All {total_runs} runs already complete.")
                continue

            print(f"\n[{fx_name}] method={method}  run_ids={run_ids[0]}..{run_ids[-1]}")
            print("-" * 50)

            try:
                run_results = run_method(cfg, method, run_ids)
            except Exception as exc:
                import traceback
                print(f"[ERROR] {type(exc).__name__}: {exc}")
                traceback.print_exc()
                run_results = [
                    _make_record(run_id=r, converged=False, coverage=0.0,
                                 iterations=0, tokens=0, time_sec=0.0,
                                 total_cases=0, valid_cases=0,
                                 error=str(exc))
                    for r in run_ids
                ]

            # Print per-method summary
            coverages = [r["coverage"] for r in run_results if not r.get("skipped")]
            if coverages:
                n_conv = sum(1 for r in run_results if r.get("converged"))
                print(f"\n  Summary ({len(run_results)} runs): "
                      f"mean_cov={statistics.mean(coverages):.1%}  "
                      f"converged={n_conv}/{len(run_results)}")

            # Append to raw_path
            with raw_path.open("a", encoding="utf-8") as f:
                for rec in run_results:
                    line = json.dumps(
                        {"fixture": fx_name, "method": method, **rec},
                        ensure_ascii=False, default=str,
                    )
                    f.write(line + "\n")
            print(f"  Written {len(run_results)} records -> {raw_path}")

    print("\n[DONE]")


if __name__ == "__main__":
    main()
