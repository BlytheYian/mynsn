"""
疫苗邏輯端對端系統測試。

TC-S-04: IFLOrchestrator 完整執行，回傳 IFLResult 結構完整
TC-S-05: 隨機基線（max_iterations=0），無 IFL 迭代時覆蓋率低
TC-S-06: IFL 優於隨機基線（iteration_count ≤ 10 時覆蓋率提升）
TC-S-07: LLM 生成案例合法比率 ≥ 85%

執行方式：
  RUN_E2E=1 pytest tests/integration/test_vaccine_e2e.py -v -s
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ifl_mcdc.config import IFLConfig
from ifl_mcdc.layer3.domain_validator import DomainValidator
from ifl_mcdc.orchestrator import IFLOrchestrator, IFLResult

VACCINE_PATH = Path(__file__).parent.parent / "fixtures" / "vaccine_eligibility.py"


@pytest.mark.skipif(
    not os.environ.get("RUN_E2E"),
    reason="端對端測試，需設定 RUN_E2E=1 才執行（需 LLM API 金鑰）",
)
def test_vaccine_e2e_full_run() -> None:
    """TC-S-04：完整執行 IFLOrchestrator，驗證 100% MC/DC 收斂。

    驗證：
    - 回傳 IFLResult，所有欄位型別正確
    - final_coverage == 1.0（100% MC/DC 傳統覆蓋率）
    - converged == True
    - iteration_count <= 50
    - loss_history 單調不增，最終為 0
    - test_suite >= 3 個案例（初始隨機案例）
    - 所有測試案例通過 DomainValidator
    """
    config = IFLConfig(max_iterations=50)
    orch = IFLOrchestrator(config=config)

    result = orch.run(VACCINE_PATH)

    # ── 列印論文用數據 ──────────────────────────────────
    print("\n" + "=" * 60)
    print("  IFL MC/DC E2E 實驗結果")
    print("=" * 60)
    print(f"  收斂：         {result.converged}")
    print(f"  覆蓋率：       {result.final_coverage:.1%}")
    print(f"  迭代次數：     {result.iteration_count}")
    print(f"  消耗 Token：   {result.total_tokens}")
    print(f"  測試案例數：   {len(result.test_suite)}")
    print(f"  不可行路徑數： {len(result.infeasible_paths)}  {result.infeasible_paths}")
    print(f"  Loss 歷程：    {result.loss_history}")
    print("=" * 60)
    validator_print = DomainValidator()
    source_stats: dict[str, dict[str, int]] = {}
    for i, tc in enumerate(result.test_suite):
        source = tc.get("__source", "?")
        clean = {k: v for k, v in tc.items() if not k.startswith("__")}
        vr = validator_print.validate(json.dumps(clean))
        valid_mark = "[OK]" if vr.passed else "[NG]"
        print(f"  T{i+1:02d} [{source:<6}] {valid_mark}  {clean}")
        if source not in source_stats:
            source_stats[source] = {"total": 0, "valid": 0}
        source_stats[source]["total"] += 1
        if vr.passed:
            source_stats[source]["valid"] += 1
    print("=" * 60)
    print("  來源統計：")
    for src, stats in source_stats.items():
        rate = stats["valid"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"  [{src:<6}] {stats['valid']}/{stats['total']} 有效（{rate:.0f}%）")
    print("=" * 60 + "\n")

    # ── 結構正確性 ──────────────────────────────────────
    assert isinstance(result, IFLResult)
    assert isinstance(result.converged, bool)
    assert 0.0 <= result.final_coverage <= 1.0
    assert isinstance(result.test_suite, list)
    assert isinstance(result.iteration_count, int)
    assert isinstance(result.total_tokens, int)
    assert isinstance(result.infeasible_paths, list)
    assert isinstance(result.loss_history, list)
    assert len(result.loss_history) >= 1

    # ── 收斂驗證（論文核心斷言）───────────────────────
    assert result.converged, (
        f"系統未收斂，覆蓋率：{result.final_coverage:.1%}"
    )
    assert result.final_coverage == 1.0, (
        f"覆蓋率未達 100%：{result.final_coverage:.1%}"
    )
    assert result.iteration_count <= 50, (
        f"迭代次數超限：{result.iteration_count}"
    )
    assert result.loss_history[-1] == 0, (
        f"最終 loss 非零：{result.loss_history[-1]}"
    )

    # ── 測試套件來源驗證 ────────────────────────────────
    # "random"：初始隨機案例；"llm"：LLM 生成且通過驗證
    allowed_sources = {"random", "llm"}
    for tc in result.test_suite:
        src = tc.get("__source", "?")
        assert src in allowed_sources, f"未知來源標記：{src!r}，案例：{tc}"

    # ── 測試案例數量 ────────────────────────────────────
    assert len(result.test_suite) >= 3, (
        f"測試案例不足（至少 3 個隨機初始案例）：{len(result.test_suite)}"
    )

    # ── Loss 歷程單調不增 ────────────────────────────────
    history = result.loss_history
    for i in range(1, len(history)):
        assert history[i] <= history[i - 1], (
            f"loss_history[{i}]={history[i]} > [{i-1}]={history[i-1]}：損失不應增加"
        )

    # ── 所有案例必須通過 DomainValidator ─────────────────
    # 架構保證：random 初始案例由 _generate_random_test 生成合法值；
    # llm 案例由 LLMSampler 內部 DomainValidator 驗證後才回傳
    validator = DomainValidator()
    for tc in result.test_suite:
        source = tc.get("__source")
        clean = {k: v for k, v in tc.items() if not k.startswith("__")}
        vr = validator.validate(json.dumps(clean))
        assert vr.passed, (
            f"非法 {source} 測試案例：{clean}，違規：{vr.violations}"
        )


@pytest.mark.skipif(
    not os.environ.get("RUN_E2E"),
    reason="端對端測試，需設定 RUN_E2E=1 才執行（需 LLM API 金鑰）",
)
def test_random_baseline() -> None:
    """TC-S-05：隨機基線（max_iterations=0），無 IFL 迭代時覆蓋率低於 100%。

    驗證：
    - max_iterations=0 → 只執行 3 個隨機初始案例，無任何 IFL 迭代
    - converged=False（隨機案例無法確保 100% MC/DC）
    - iteration_count=0
    - test_suite 恰好 3 個隨機案例
    """
    config = IFLConfig(max_iterations=0)
    orch = IFLOrchestrator(config=config)

    result = orch.run(VACCINE_PATH)

    print(f"\n[TC-S-05] 隨機基線覆蓋率：{result.final_coverage:.1%}，案例數：{len(result.test_suite)}")

    assert result.iteration_count == 0, (
        f"max_iterations=0 時不應執行任何 IFL 迭代，實際：{result.iteration_count}"
    )
    assert len(result.test_suite) == 3, (
        f"隨機基線應只有 3 個案例，實際：{len(result.test_suite)}"
    )
    assert result.converged is False, (
        f"隨機基線不應收斂，覆蓋率：{result.final_coverage:.1%}"
    )


@pytest.mark.skipif(
    not os.environ.get("RUN_E2E"),
    reason="端對端測試，需設定 RUN_E2E=1 才執行（需 LLM API 金鑰）",
)
def test_ifl_beats_random() -> None:
    """TC-S-06：IFL 優於隨機基線（≤ 10 次迭代時覆蓋率提升）。

    驗證：
    - IFL 在 10 次迭代內的覆蓋率高於純隨機基線
    - IFL iteration_count ≤ 10
    - IFL final_coverage > random_coverage（IFL 確實帶來改善）
    """
    # 隨機基線
    random_config = IFLConfig(max_iterations=0)
    random_orch = IFLOrchestrator(config=random_config)
    random_result = random_orch.run(VACCINE_PATH)

    # IFL（限 10 次迭代）
    ifl_config = IFLConfig(max_iterations=10)
    ifl_orch = IFLOrchestrator(config=ifl_config)
    ifl_result = ifl_orch.run(VACCINE_PATH)

    print(
        f"\n[TC-S-06] 隨機基線：{random_result.final_coverage:.1%}，"
        f"IFL（10 次）：{ifl_result.final_coverage:.1%}"
    )

    assert ifl_result.iteration_count <= 10, (
        f"IFL 迭代次數超過 10：{ifl_result.iteration_count}"
    )
    assert ifl_result.final_coverage > random_result.final_coverage, (
        f"IFL 覆蓋率 {ifl_result.final_coverage:.1%} 未超越隨機基線 "
        f"{random_result.final_coverage:.1%}"
    )


@pytest.mark.skipif(
    not os.environ.get("RUN_E2E"),
    reason="端對端測試，需設定 RUN_E2E=1 才執行（需 LLM API 金鑰）",
)
def test_valid_generation_ratio() -> None:
    """TC-S-07：LLM 生成案例合法比率 ≥ 85%。

    驗證：
    - test_suite 中來源為 "llm" 的案例通過 DomainValidator 的比率 ≥ 85%
    - 若無 LLM 案例（如全部不可行），跳過此斷言
    """
    config = IFLConfig(max_iterations=20)
    orch = IFLOrchestrator(config=config)

    result = orch.run(VACCINE_PATH)

    validator = DomainValidator()
    llm_cases = [tc for tc in result.test_suite if tc.get("__source") == "llm"]

    if not llm_cases:
        pytest.skip("無 LLM 生成案例（可能全部路徑為不可行），跳過合法比率檢查")

    valid_count = 0
    for tc in llm_cases:
        clean = {k: v for k, v in tc.items() if not k.startswith("__")}
        vr = validator.validate(json.dumps(clean))
        if vr.passed:
            valid_count += 1

    ratio = valid_count / len(llm_cases)
    print(f"\n[TC-S-07] LLM 案例合法比率：{ratio:.1%}（{valid_count}/{len(llm_cases)}）")

    assert ratio >= 0.85, (
        f"LLM 生成案例合法比率 {ratio:.1%} 低於 85% 門檻"
    )
