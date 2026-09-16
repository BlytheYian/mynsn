"""
coverup_runner.py — CoverUp LLM-guided coverage runner for IFL-MCDC experiments.

Usage:
  python coverup_runner.py --fixture <name> --run-id <int>
  python coverup_runner.py --fixture vaccine_eligibility --run-id 1

Prints one JSON record to stdout.  Called by run_experiments2.py._run_coverup.

CoverUp (https://github.com/plasma-umass/coverup) uses LLMs to generate tests
that improve branch/statement coverage.  Requires: pip install coverup
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import uuid
from pathlib import Path

import ifl_mcdc.layer1.probe_injector as pi
from ifl_mcdc.layer1.ast_parser import ASTParser
from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.layer1.probe_injector import ProbeInjector
from ifl_mcdc.models.probe_record import ProbeLog

# ── Fixture configs (kept in sync with run_experiments2.py) ──────────
FIXTURE_CONFIGS: dict[str, dict] = {
    "vaccine_eligibility": {
        "path": "tests/fixtures/vaccine_eligibility.py",
        "func_name": "check_vaccine_eligibility",
        "domain_types": {
            "age": "int", "high_risk": "bool",
            "days_since_last": "int", "egg_allergy": "bool",
        },
    },
    "loan_approval": {
        "path": "tests/fixtures/loan_approval.py",
        "func_name": "check_loan_approval",
        "domain_types": {
            "credit_score": "int", "annual_income": "int",
            "loan_amount": "int", "employed": "bool",
            "has_collateral": "bool", "bankruptcy_history": "bool",
        },
    },
    "surgery_risk": {
        "path": "tests/fixtures/surgery_risk.py",
        "func_name": "check_surgery_risk",
        "domain_types": {
            "age": "int", "obese": "bool", "has_diabetes": "bool",
            "has_hypertension": "bool", "is_smoker": "bool",
            "low_hemoglobin": "bool", "low_platelets": "bool",
            "cardiac_history": "bool", "has_copd": "bool",
        },
    },
    "icu_admission": {
        "path": "tests/fixtures/icu_admission.py",
        "func_name": "check_icu_admission",
        "domain_types": {
            "age": "int", "low_bp": "bool", "high_heart_rate": "bool",
            "high_resp_rate": "bool", "high_temp": "bool", "low_gcs": "bool",
            "low_oxygen": "bool", "low_urine": "bool",
            "high_creatinine": "bool", "sepsis": "bool",
        },
    },
    "gpca_alarm": {
        "path": "tests/fixtures/gpca_alarm.py",
        "func_name": "gpca_alarm_decision",
        "domain_types": {
            "flow_sensor_equipped": "bool",
            "flow_rate_ml_hr": "int", "high_flow_threshold": "int",
            "over_duration_min": "int", "free_flow": "bool",
            "low_flow_threshold": "int", "under_duration_min": "int",
            "reservoir_volume_ml": "int", "infusion_in_progress": "bool",
            "upstream_occlusion": "bool", "downstream_occlusion": "bool",
        },
    },
    "tcas": {
        "path": "tests/fixtures/tcas.py",
        "func_name": "alt_sep_test",
        "domain_types": {
            "Cur_Vertical_Sep": "int", "High_Confidence": "bool",
            "Two_of_Three_Reports_Valid": "bool", "Own_Tracked_Alt": "int",
            "Own_Tracked_Alt_Rate": "int", "Other_Tracked_Alt": "int",
            "Alt_Layer_Value": "int", "Up_Separation": "int",
            "Down_Separation": "int", "Other_RAC": "int",
            "Other_Capability": "int", "Climb_Inhibit": "int",
        },
    },
}


def _check_coverup() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "coverup", "--version"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _compute_coverage(func_name: str, cases: list[dict],
                      nodes: list, injected_src: str) -> float:
    if not cases:
        return 0.0
    mod_name = f"_cu_{uuid.uuid4().hex[:8]}"
    mod = types.ModuleType(mod_name)
    exec(compile(injected_src, mod_name, "exec"), mod.__dict__)  # noqa: S102
    import sys as _sys
    _sys.modules[mod_name] = mod

    log = ProbeLog()
    pi._GLOBAL_LOG = log
    mod.__dict__["_ifl_probe"] = pi._ifl_probe
    mod.__dict__["_ifl_record_decision"] = pi._ifl_record_decision

    func = getattr(mod, func_name)
    for case in cases:
        tid = f"T{uuid.uuid4().hex[:8]}"
        setattr(pi._CURRENT_TEST_ID, "value", tid)
        try:
            func(**case)
        except Exception:
            pass

    engine = MCDCCoverageEngine()
    total_covered = 0
    total_pairs = 0
    for dn in nodes:
        matrix = engine.build_matrix(dn.condition_set, log)
        k = len(dn.condition_set.conditions)
        total_pairs += k * 2
        total_covered += k * 2 - matrix.compute_loss()
    return total_covered / total_pairs if total_pairs > 0 else 0.0


def run_one(fixture_name: str, run_id: int) -> dict:
    if not _check_coverup():
        return {
            "run_id": run_id, "skipped": True,
            "error": "coverup not installed (pip install coverup)",
            "converged": False, "coverage": 0.0,
            "iterations": 0, "tokens": 0, "time_sec": 0.0,
            "total_cases": 0, "valid_cases": 0,
            "valid_ratio": 0.0, "infeasible_pairs": 0,
        }

    cfg = FIXTURE_CONFIGS[fixture_name]
    path = cfg["path"]
    func_name = cfg["func_name"]

    src = Path(path).read_text(encoding="utf-8")
    nodes = ASTParser().parse_file(path)
    injected_src = ProbeInjector(nodes).inject(src)

    t0 = time.time()
    collected: list[dict] = []
    import ast as _ast

    with tempfile.TemporaryDirectory() as tmpdir:
        tests_dir = Path(tmpdir) / "tests"
        tests_dir.mkdir()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "coverup",
                 "--tests-dir", str(tests_dir),
                 "--package-dir", str(Path(path).parent),
                 "--model", "gpt-4o-mini",
                 "--max-attempts", "2",
                 str(path)],
                capture_output=True, text=True, timeout=120,
                env=dict(os.environ),
            )
            param_names = list(cfg["domain_types"].keys())
            for test_file in tests_dir.glob("test_*.py"):
                try:
                    tree = _ast.parse(test_file.read_text(encoding="utf-8"))
                    for func_def in _ast.walk(tree):
                        if not isinstance(func_def, _ast.FunctionDef):
                            continue
                        local_vals: dict[str, object] = {}
                        for stmt in _ast.walk(func_def):
                            if isinstance(stmt, _ast.Assign):
                                for tgt in stmt.targets:
                                    if isinstance(tgt, _ast.Name):
                                        try:
                                            local_vals[tgt.id] = _ast.literal_eval(stmt.value)
                                        except Exception:
                                            pass
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
        except Exception:
            pass

    coverage = _compute_coverage(func_name, collected, nodes, injected_src)
    elapsed = time.time() - t0
    n = len(collected)
    return {
        "run_id": run_id,
        "converged": coverage >= 1.0,
        "coverage": coverage,
        "iterations": 0,
        "tokens": 0,
        "time_sec": round(elapsed, 3),
        "total_cases": n,
        "valid_cases": n,
        "valid_ratio": 1.0 if n > 0 else 0.0,
        "infeasible_pairs": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="CoverUp runner for one fixture/run")
    parser.add_argument("--fixture", required=True, choices=list(FIXTURE_CONFIGS))
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()

    rec = run_one(args.fixture, args.run_id)
    print(json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    main()
