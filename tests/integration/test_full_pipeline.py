"""
全管線整合測試：驗證層與層之間的資料流正確性。

TC-P-01: Layer1 → Layer2 資料流
TC-P-02: Layer2 → Layer3 資料流
TC-P-03: LLMSampler → DomainValidator 資料流
"""
from __future__ import annotations

import json


# ─────────────────────────────────────────────
# TC-P-01：Layer1 → Layer2 資料流
# ─────────────────────────────────────────────


def test_layer1_to_layer2_data_flow() -> None:
    """TC-P-01：ASTParser 解析的 DecisionNode 能被 SMTConstraintSynthesizer 正確處理。

    驗證：
    - parse_file 回傳 1 個決策節點
    - 初始 ProbeLog 產生缺口
    - Z3 對第一個缺口回傳 satisfiable=True
    - bound_specs 非空且每個 BoundSpec 有 var_name
    """
    from ifl_mcdc.config import IFLConfig
    from ifl_mcdc.layer1.ast_parser import ASTParser
    from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
    from ifl_mcdc.layer2.gap_analyzer import GapAnalyzer
    from ifl_mcdc.layer2.smt_synthesizer import SMTConstraintSynthesizer
    from ifl_mcdc.models.probe_record import ProbeLog

    parser = ASTParser()
    nodes = parser.parse_file("tests/fixtures/vaccine_eligibility.py")
    assert len(nodes) == 1

    engine = MCDCCoverageEngine()
    matrix = engine.build_matrix(nodes[0].condition_set, ProbeLog())

    analyzer = GapAnalyzer()
    gaps = analyzer.analyze(matrix)
    assert len(gaps) > 0, "應有缺口待填補"

    smt = SMTConstraintSynthesizer()
    config = IFLConfig()
    result = smt.synthesize(nodes[0], gaps[0], config.domain_types)
    assert result.satisfiable, "Z3 應能找到可行解"
    assert result.bound_specs, "bound_specs 不應為空"
    for bs in result.bound_specs:
        assert bs.var_name, "每個 BoundSpec 應有 var_name"


# ─────────────────────────────────────────────
# TC-P-02：Layer2 → Layer3 資料流
# ─────────────────────────────────────────────


def test_layer2_to_layer3_data_flow() -> None:
    """TC-P-02：BoundSpec 能被 PromptConstructor 正確轉化為提示。

    驗證：
    - prompt 非空
    - 包含領域關鍵字（age 或 days）
    - 包含 JSON 指示
    - 長度不超過 2048 tokens（粗估 len/3）
    """
    from ifl_mcdc.config import IFLConfig
    from ifl_mcdc.layer1.ast_parser import ASTParser
    from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
    from ifl_mcdc.layer2.gap_analyzer import GapAnalyzer
    from ifl_mcdc.layer2.smt_synthesizer import SMTConstraintSynthesizer
    from ifl_mcdc.layer3.prompt_builder import PromptConstructor
    from ifl_mcdc.models.probe_record import ProbeLog

    parser = ASTParser()
    nodes = parser.parse_file("tests/fixtures/vaccine_eligibility.py")
    engine = MCDCCoverageEngine()
    matrix = engine.build_matrix(nodes[0].condition_set, ProbeLog())
    gaps = GapAnalyzer().analyze(matrix)
    config = IFLConfig()
    smt_result = SMTConstraintSynthesizer().synthesize(
        nodes[0], gaps[0], config.domain_types
    )

    prompt_builder = PromptConstructor()
    prompt = prompt_builder.build(
        nodes[0], gaps[0],
        smt_result.bound_specs or [],
        config.func_signature,
        config.domain_context,
    )
    assert len(prompt) > 0
    assert "age" in prompt.lower() or "days" in prompt.lower()
    assert "JSON" in prompt or "json" in prompt
    assert len(prompt) / 3 <= 2048, "提示不應超過 2048 tokens"


# ─────────────────────────────────────────────
# TC-P-03：LLMSampler → DomainValidator 資料流
# ─────────────────────────────────────────────


def test_llm_output_always_passes_validator() -> None:
    """TC-P-03：LLMSampler 回傳的案例一定通過 DomainValidator。

    驗證：
    - sample() 回傳 (dict, ValidationResult)
    - ValidationResult.passed == True
    - result 含 "age" 欄位
    """
    from ifl_mcdc.layer3.domain_validator import DEFAULT_MEDICAL_RULES, DomainValidator
    from ifl_mcdc.layer3.llm_sampler import LLMSampler, MockLLMBackend

    valid_responses = [
        '{"age": 45, "high_risk": true, "days_since_last": 200, "egg_allergy": false}',
        '{"age": 70, "high_risk": false, "days_since_last": 365, "egg_allergy": false}',
    ]
    backend = MockLLMBackend(valid_responses)
    validator = DomainValidator(DEFAULT_MEDICAL_RULES)
    sampler = LLMSampler(backend, validator)

    for _ in range(2):
        result, vr = sampler.sample("test prompt")
        assert vr.passed, f"LLMSampler 回傳的案例應通過 DomainValidator: {vr.violations}"
        assert isinstance(result, dict)
        assert "age" in result
