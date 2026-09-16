"""
Layer 2 → Layer 3 整合測試。

TC-I-05: BoundSpecs 驅動 Prompt 建構 → LLM 採樣 → DomainValidator
TC-I-06: AcceptanceGate.evaluate 降低 L(X)
TC-I-07: 完整單次迭代 (GapAnalyzer → SMT → Prompt → LLMSampler → Gate)

說明：
  所有測試使用簡化 k=4 無否定表達式：
  (age >= 65 or high_risk) and (days_since_last > 180) and healthy
  避免否定條件探針值與耦合檢查不一致問題。
"""
from __future__ import annotations

import json
import sys
import types
import uuid

import ifl_mcdc.layer1.probe_injector as pi
from ifl_mcdc.layer1.ast_parser import ASTParser
from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.layer1.probe_injector import ProbeInjector
from ifl_mcdc.layer2.gap_analyzer import GapAnalyzer
from ifl_mcdc.layer2.smt_synthesizer import SMTConstraintSynthesizer
from ifl_mcdc.layer3.acceptance_gate import AcceptanceGate
from ifl_mcdc.layer3.domain_validator import DEFAULT_MEDICAL_RULES, DomainValidator
from ifl_mcdc.layer3.llm_sampler import LLMSampler, MockLLMBackend
from ifl_mcdc.layer3.prompt_builder import PromptConstructor
from ifl_mcdc.models.coverage_matrix import GapEntry
from ifl_mcdc.models.probe_record import ProbeLog

_SIMPLE_EXPR = "(age >= 65 or high_risk) and (days_since_last > 180) and healthy"
_SIMPLE_SRC = f"""\
def check(age: int, high_risk: bool, days_since_last: int, healthy: bool) -> bool:
    if {_SIMPLE_EXPR}:
        return True
    return False
"""
_SIMPLE_DOMAIN_TYPES: dict[str, str] = {
    "age": "int",
    "high_risk": "bool",
    "days_since_last": "int",
    "healthy": "bool",
}
_SIMPLE_FUNC_SIG = "check(age, high_risk, days_since_last, healthy)"


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
# TC-I-05：BoundSpecs 驅動 Prompt 建構 → LLM 採樣 → DomainValidator
# ─────────────────────────────────────────────


def test_bound_specs_feed_prompt_and_sampler() -> None:
    """TC-I-05：c2(high_risk) F2T → BoundSpecs → PromptConstructor → MockLLM → passed==True。"""
    parser = ASTParser()
    nodes = parser.parse_source(f"if {_SIMPLE_EXPR}: pass\n")
    dn = nodes[0]

    cond_c2 = next(c for c in dn.condition_set.conditions if c.expression == "high_risk")
    gap = GapEntry(
        condition_id=cond_c2.cond_id,
        flip_direction="F2T",
        missing_pair_type="unique_cause",
        estimated_difficulty=0.5,
    )

    smt = SMTConstraintSynthesizer()
    smt_result = smt.synthesize(dn, gap, _SIMPLE_DOMAIN_TYPES)
    assert smt_result.satisfiable is True, "c2(high_risk) F2T 應 SAT"
    assert smt_result.bound_specs is not None

    prompt = PromptConstructor().build(
        dn, gap, smt_result.bound_specs, _SIMPLE_FUNC_SIG, "測試情境"
    )
    assert isinstance(prompt, str) and len(prompt) > 0

    valid_json = json.dumps(
        {"age": 20, "high_risk": True, "days_since_last": 200, "healthy": True}
    )
    mock = MockLLMBackend([valid_json])
    sampler = LLMSampler(mock, DomainValidator(DEFAULT_MEDICAL_RULES))

    data, vr = sampler.sample(prompt)
    assert isinstance(data, dict), f"應回傳 dict，得到 {type(data)}"
    assert vr.passed, f"DomainValidator 應通過，實際 {vr.violations}"
    assert "age" in data or "high_risk" in data, "回傳 dict 應含預期欄位"


# ─────────────────────────────────────────────
# TC-I-06：AcceptanceGate.evaluate 降低 L(X)
# ─────────────────────────────────────────────


def test_acceptance_gate_reduces_loss() -> None:
    """TC-I-06：L(X)=2 → gate.evaluate(T_Y) → L(X)==1。

    T_X=(days_since_last=200, D=True) 已在 log；pre-mark 6 對（含 c3 F2T）。
    T_Y=(days_since_last=100, D=False) 配對 T_X，僅新增 c3 T2F → L:2→1。
    """
    decision_nodes, mod, log = _load_instrumented(
        _SIMPLE_SRC, f"_test_i06_{uuid.uuid4().hex[:6]}"
    )
    dn = decision_nodes[0]

    # T_X：c3=True，D=True
    _run_test(
        mod, "check",
        {"age": 70, "high_risk": True, "days_since_last": 200, "healthy": True},
    )

    engine = MCDCCoverageEngine()
    matrix = engine.build_matrix(dn.condition_set, log)

    # 條件 ID（依解析順序：c1=age>=65, c2=high_risk, c3=days>180, c4=healthy）
    conds = dn.condition_set.conditions
    c1_id, c2_id, c3_id, c4_id = [c.cond_id for c in conds]

    # Pre-mark 6 對：c1 F2T/T2F, c2 F2T/T2F, c3 F2T, c4 F2T → L = 8-6 = 2
    for cid in (c1_id, c2_id):
        matrix.mark_covered(cid, "F2T")
        matrix.mark_covered(cid, "T2F")
    matrix.mark_covered(c3_id, "F2T")
    matrix.mark_covered(c4_id, "F2T")

    assert matrix.compute_loss() == 2, (
        f"預標記後 L 應為 2，實際 {matrix.compute_loss()}"
    )

    # T_Y：c3=False，D=False
    t_y_id = _run_test(
        mod, "check",
        {"age": 70, "high_risk": True, "days_since_last": 100, "healthy": True},
    )

    gate = AcceptanceGate(engine)
    accepted = gate.evaluate(matrix, log, t_y_id)

    assert accepted is True, "gate 應接受 T_Y（L 降低）"
    assert matrix.compute_loss() == 1, (
        f"gate 後 L 應為 1，實際 {matrix.compute_loss()}"
    )


# ─────────────────────────────────────────────
# TC-I-07：完整單次迭代 (GapAnalyzer → SMT → Prompt → LLMSampler → Gate)
# ─────────────────────────────────────────────


def test_one_full_iteration_reduces_loss() -> None:
    """TC-I-07：L(X)=4 → 完整單次迭代 → L(X)≤3，loss_history 長度==2。

    T_W=(days_since_last=100, D=False) 已在 log；pre-mark c1/c2 對 → L=4。
    第一個缺口 = c3 F2T → SMT SAT → MockLLM 回傳 T_X(days_since_last=200, D=True)
    → gate 接受 → c3 F2T/T2F 覆蓋 → L=4→2 ≤ 3。
    """
    decision_nodes, mod, log = _load_instrumented(
        _SIMPLE_SRC, f"_test_i07_{uuid.uuid4().hex[:6]}"
    )
    dn = decision_nodes[0]

    # T_W：c3=False，D=False（作為比對基準）
    _run_test(
        mod, "check",
        {"age": 70, "high_risk": True, "days_since_last": 100, "healthy": True},
    )

    engine = MCDCCoverageEngine()
    matrix = engine.build_matrix(dn.condition_set, log)

    conds = dn.condition_set.conditions  # c1, c2, c3, c4
    c1_id, c2_id = conds[0].cond_id, conds[1].cond_id

    # Pre-mark c1/c2 → L = 8 - 4 = 4
    for cid in (c1_id, c2_id):
        matrix.mark_covered(cid, "F2T")
        matrix.mark_covered(cid, "T2F")

    assert matrix.compute_loss() == 4, (
        f"預標記後 L 應為 4，實際 {matrix.compute_loss()}"
    )
    loss_history: list[int] = [matrix.compute_loss()]

    # GapAnalyzer → 第一個缺口（c3 F2T 或 T2F，皆 SAT）
    analyzer = GapAnalyzer()
    gaps = analyzer.analyze(matrix)
    assert len(gaps) == 4
    first_gap = gaps[0]

    # SMT 合成
    smt = SMTConstraintSynthesizer()
    smt_result = smt.synthesize(dn, first_gap, _SIMPLE_DOMAIN_TYPES)
    assert smt_result.satisfiable is True, (
        f"{first_gap.condition_id} {first_gap.flip_direction} 應 SAT"
    )

    # Prompt + MockLLM 採樣
    tx_json = json.dumps(
        {"age": 65, "high_risk": True, "days_since_last": 200, "healthy": True}
    )
    prompt = PromptConstructor().build(
        dn, first_gap, smt_result.bound_specs or [], _SIMPLE_FUNC_SIG, "測試情境"
    )
    mock = MockLLMBackend([tx_json])
    sampler = LLMSampler(mock, DomainValidator(DEFAULT_MEDICAL_RULES))
    new_case, _ = sampler.sample(prompt)

    # 執行新測試案例並透過 gate 評估
    test_id = _run_test(mod, "check", new_case)
    gate = AcceptanceGate(engine)
    gate.evaluate(matrix, log, test_id)
    loss_history.append(matrix.compute_loss())

    assert len(loss_history) == 2, (
        f"loss_history 應長度 2，實際 {len(loss_history)}"
    )
    assert loss_history[1] <= 3, (
        f"單次迭代後 L 應 ≤ 3，實際 {loss_history[1]}"
    )
    assert loss_history[1] < loss_history[0], "L 應在迭代後下降"
