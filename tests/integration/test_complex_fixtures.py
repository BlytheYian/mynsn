"""
複雜 Fixture 整合測試。

驗證系統對 k=6、k=9、k=10 的複雜邏輯可正確完成各層整合。

TC-I-08: loan_approval（k=6，無共用變數）——探針注入 → 覆蓋率矩陣
TC-I-09: dose_adjustment（k=5，共用 age）——SMT 對 c1 T2F 缺口的 pairwise 約束
TC-I-10: surgery_risk（k=9）——GapAnalyzer 產出正確缺口數量
TC-I-11: icu_admission（k=10）——ASTParser 正確解析並產出 BooleanDerivativeEngine 報告
"""
from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

import pytest

import ifl_mcdc.layer1.probe_injector as pi
from ifl_mcdc.layer1.ast_parser import ASTParser
from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.layer1.probe_injector import ProbeInjector
from ifl_mcdc.layer2.boolean_derivative import BooleanDerivativeEngine
from ifl_mcdc.layer2.gap_analyzer import GapAnalyzer
from ifl_mcdc.layer2.smt_synthesizer import SMTConstraintSynthesizer
from ifl_mcdc.models.coverage_matrix import GapEntry
from ifl_mcdc.models.probe_record import ProbeLog

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
LOAN_PATH     = FIXTURES_DIR / "loan_approval.py"
DOSE_PATH     = FIXTURES_DIR / "dose_adjustment.py"
SURGERY_PATH  = FIXTURES_DIR / "surgery_risk.py"
ICU_PATH      = FIXTURES_DIR / "icu_admission.py"


# ──────────────────────────────────────────
# 輔助函式
# ──────────────────────────────────────────


def _load_instrumented(
    source: str, module_name: str
) -> tuple[list, types.ModuleType, ProbeLog]:
    """動態注入探針並載入模組，回傳 (decision_nodes, module, log)。"""
    parser = ASTParser()
    decision_nodes = parser.parse_source(source)
    injector = ProbeInjector(decision_nodes)
    instrumented = injector.inject(source)

    mod = types.ModuleType(module_name)
    exec(compile(instrumented, module_name, "exec"), mod.__dict__)  # noqa: S102
    sys.modules[module_name] = mod

    log = ProbeLog()
    pi._GLOBAL_LOG = log
    setattr(mod, "_ifl_probe", pi._ifl_probe)
    setattr(mod, "_ifl_record_decision", pi._ifl_record_decision)

    return decision_nodes, mod, log


def _run_test(
    mod: types.ModuleType,
    func_name: str,
    test_case: dict[str, object],
) -> str:
    """執行單一測試案例，回傳 test_id。"""
    test_id = f"T{uuid.uuid4().hex[:8]}"
    setattr(pi._CURRENT_TEST_ID, "value", test_id)
    try:
        getattr(mod, func_name)(**test_case)
    except Exception:
        pass
    return test_id


# ─────────────────────────────────────────────
# TC-I-08：loan_approval（k=6）——探針注入 → 覆蓋率矩陣
# ─────────────────────────────────────────────


def test_loan_approval_k6_coverage_matrix() -> None:
    """TC-I-08：loan_approval（k=6）探針注入後執行 6 個測試案例，覆蓋率矩陣正常更新。

    loan_approval 具有 6 個原子條件，初始 compute_loss()=12（2*k）。
    執行 6 個覆蓋各 OR/AND 子群的測試案例後，損失應有所降低（loss < 12）。
    """
    source = LOAN_PATH.read_text(encoding="utf-8")
    decision_nodes, mod, log = _load_instrumented(
        source, f"_test_loan_i08_{uuid.uuid4().hex[:6]}"
    )
    assert len(decision_nodes) == 1, "loan_approval 應有 1 個決策節點"
    dn = decision_nodes[0]

    k = dn.condition_set.k
    assert k == 6, f"loan_approval 應有 k=6 個原子條件，實際 k={k}"

    engine = MCDCCoverageEngine()
    # 空 log 時初始損失 = 2*k
    initial_matrix = engine.build_matrix(dn.condition_set, ProbeLog())
    assert initial_matrix.compute_loss() == 2 * k, (
        f"初始損失應為 {2*k}，實際 {initial_matrix.compute_loss()}"
    )

    # 6 個測試案例：涵蓋各 OR 子群的 True/False 面
    test_cases = [
        # credit OK, income OK, amount OK, employed T, no collateral, no bankruptcy
        {"credit_score": 750, "annual_income": 60000, "loan_amount": 300000,
         "employed": True, "has_collateral": False, "bankruptcy_history": False},
        # credit NG, income NG → 兩 OR 都 False → decision=False
        {"credit_score": 600, "annual_income": 40000, "loan_amount": 300000,
         "employed": True, "has_collateral": False, "bankruptcy_history": False},
        # credit OK only (income NG), amount NG, collateral OK
        {"credit_score": 750, "annual_income": 40000, "loan_amount": 600000,
         "employed": True, "has_collateral": True, "bankruptcy_history": False},
        # credit NG, income OK only, amount OK, collateral NG
        {"credit_score": 600, "annual_income": 60000, "loan_amount": 400000,
         "employed": True, "has_collateral": False, "bankruptcy_history": False},
        # employed=False → decision=False（c5 翻轉）
        {"credit_score": 750, "annual_income": 60000, "loan_amount": 300000,
         "employed": False, "has_collateral": False, "bankruptcy_history": False},
        # bankruptcy=True → decision=False（c6 翻轉）
        {"credit_score": 750, "annual_income": 60000, "loan_amount": 300000,
         "employed": True, "has_collateral": False, "bankruptcy_history": True},
    ]

    for tc in test_cases:
        _run_test(mod, "check_loan_approval", tc)

    matrix = engine.build_matrix(dn.condition_set, log)
    loss = matrix.compute_loss()

    assert loss < 2 * k, (
        f"執行 6 個測試案例後損失應 < {2*k}，實際 {loss}（覆蓋率矩陣應已更新）"
    )
    assert 0 <= loss <= 2 * k, f"損失應在 [0, {2*k}] 範圍內，實際 {loss}"


# ─────────────────────────────────────────────
# TC-I-09：dose_adjustment——SMT 對 c1 T2F 的 pairwise 約束驗證
# ─────────────────────────────────────────────


def test_dose_adjustment_smt_pairwise_weight_constraint() -> None:
    """TC-I-09：dose_adjustment（k=5，共用 age），c1(age>=65) T2F SMT pairwise 強制 weight<=70。

    dose_adjustment 的邏輯：(age>=65 or (age>=18 and weight>70)) and not renal and not liver

    Pairwise 分析：
      c1=True(age>=65) → c2=True(age>=18)，補集中 c2 must 保持 True → comp_age>=18。
      NOT(decision_comp) = NOT(OR(False, comp_age>=18 AND weight>70) AND True AND True)
                         = NOT(weight>70) = weight<=70。
      因此 Z3 在 True 側選到的 weight 必須 <=70。

    斷言：
      - synthesize 成功（SAT）
      - weight 的 BoundSpec interval[0] <= 70
      - age 的 BoundSpec interval[0] >= 65（Z3 model 需 age>=65）
    """
    parser = ASTParser()
    nodes = parser.parse_file(str(DOSE_PATH))
    assert len(nodes) >= 1
    dn = nodes[0]

    cond_c1 = next(
        (c for c in dn.condition_set.conditions if c.expression == "age >= 65"),
        None,
    )
    assert cond_c1 is not None, (
        f"dose_adjustment 應有 age>=65 條件，實際：{[c.expression for c in dn.condition_set.conditions]}"
    )

    gap = GapEntry(
        condition_id=cond_c1.cond_id,
        flip_direction="T2F",
        missing_pair_type="unique_cause",
        estimated_difficulty=0.5,
    )

    domain_types = {
        "age": "int",
        "weight": "int",
        "renal_impaired": "bool",
        "liver_impaired": "bool",
    }
    domain_bounds = {"age": [0, 130], "weight": [0, 200]}

    synthesizer = SMTConstraintSynthesizer(domain_bounds=domain_bounds)
    result = synthesizer.synthesize(dn, gap, domain_types)

    assert result.satisfiable is True, (
        "c1(age>=65) T2F 應有可行解（pairwise 選 weight<=70）"
    )
    assert result.bound_specs is not None

    # age 的 Z3 model 值應 >= 65
    age_spec = next((s for s in result.bound_specs if s.var_name == "age"), None)
    assert age_spec is not None and age_spec.interval is not None
    assert age_spec.interval[0] >= 65, (
        f"T 側 age 應 >= 65（c1=True），實際 interval={age_spec.interval}"
    )

    # weight 的 Z3 model 值應 <= 70（pairwise 傳播約束）
    weight_spec = next((s for s in result.bound_specs if s.var_name == "weight"), None)
    assert weight_spec is not None and weight_spec.interval is not None
    assert weight_spec.interval[0] <= 70, (
        f"Pairwise 應強制 weight model 值 <=70，實際 interval={weight_spec.interval}"
    )


# ─────────────────────────────────────────────
# TC-I-10：surgery_risk（k=9）——GapAnalyzer 產出正確缺口數量
# ─────────────────────────────────────────────


def test_surgery_risk_k9_gap_count() -> None:
    """TC-I-10：surgery_risk（k=9），空覆蓋矩陣下 GapAnalyzer 應產出 18 個缺口。

    手術風險邏輯：
      (age>=70 or obese)
      and (has_diabetes and has_hypertension)
      and (is_smoker or cardiac_history or has_copd)
      and (low_hemoglobin or low_platelets)

    k=9 個原子條件，初始損失 = 2*9 = 18，GapAnalyzer 應識別 18 個缺口。

    額外驗證：
      - BooleanDerivativeEngine 對所有 9 個條件均可計算 MaskingReport
      - 所有缺口的 estimated_difficulty 均非負
    """
    parser = ASTParser()
    nodes = parser.parse_file(str(SURGERY_PATH))
    assert len(nodes) >= 1
    dn = nodes[0]

    k = dn.condition_set.k
    assert k == 9, f"surgery_risk 應有 k=9 個原子條件，實際 k={k}"

    # 空覆蓋矩陣
    engine = MCDCCoverageEngine()
    matrix = engine.build_matrix(dn.condition_set, ProbeLog())
    assert matrix.compute_loss() == 18, (
        f"k=9 初始損失應為 18，實際 {matrix.compute_loss()}"
    )

    # GapAnalyzer 應產出 18 個缺口
    analyzer = GapAnalyzer()
    gaps = analyzer.analyze(matrix)
    assert len(gaps) == 18, (
        f"k=9 空矩陣應有 18 個缺口，實際 {len(gaps)}"
    )

    # 所有缺口的 estimated_difficulty 應非負
    for g in gaps:
        assert g.estimated_difficulty >= 0, (
            f"缺口 {g.condition_id} {g.flip_direction} 的難度應 >= 0，"
            f"實際 {g.estimated_difficulty}"
        )

    # BooleanDerivativeEngine 對所有 9 個條件均可計算
    deriv_engine = BooleanDerivativeEngine()
    reports = []
    for cond in dn.condition_set.conditions:
        report = deriv_engine.compute(dn, cond)
        reports.append(report)
    assert len(reports) == k, f"應有 {k} 個 MaskingReport，實際 {len(reports)}"


# ─────────────────────────────────────────────
# TC-I-11：icu_admission（k=10）——ASTParser + BooleanDerivativeEngine 完整處理
# ─────────────────────────────────────────────


def test_icu_admission_k10_derivative_reports() -> None:
    """TC-I-11：icu_admission（k=10），ASTParser 解析 + BooleanDerivativeEngine 對全部 10 個條件報告。

    ICU 入住邏輯：
      age >= 18
      and (low_bp or high_heart_rate)
      and (high_resp_rate or high_temp)
      and (low_gcs or low_oxygen)
      and (low_urine or high_creatinine or sepsis)

    k=10，BooleanDerivativeEngine 對每個條件計算遮蔽報告，驗證系統處理 k=10 無例外。
    """
    parser = ASTParser()
    nodes = parser.parse_file(str(ICU_PATH))
    assert len(nodes) >= 1, "icu_admission 應有至少 1 個決策節點"
    dn = nodes[0]

    k = dn.condition_set.k
    assert k == 10, f"icu_admission 應有 k=10 個原子條件，實際 k={k}"

    # 空覆蓋矩陣初始損失
    engine = MCDCCoverageEngine()
    matrix = engine.build_matrix(dn.condition_set, ProbeLog())
    assert matrix.compute_loss() == 20, (
        f"k=10 初始損失應為 20，實際 {matrix.compute_loss()}"
    )

    # BooleanDerivativeEngine 對全部 10 個條件計算
    deriv_engine = BooleanDerivativeEngine()
    for cond in dn.condition_set.conditions:
        report = deriv_engine.compute(dn, cond)
        # 每個 report 應合法（不是 None，也不拋出例外）
        assert report is not None, (
            f"條件 {cond.cond_id}（{cond.expression}）的 MaskingReport 不應為 None"
        )

    # GapAnalyzer 對 k=10 空矩陣應產出 20 個缺口
    analyzer = GapAnalyzer()
    gaps = analyzer.analyze(matrix)
    assert len(gaps) == 20, (
        f"k=10 空矩陣應有 20 個缺口，實際 {len(gaps)}"
    )
