"""
接受閘：評估新測試案例是否降低 MC/DC 覆蓋損失。

TC-U-52: L(X) 降低時接受（回傳 True）
TC-U-53: L(X) 不變時拒絕（回傳 False）
"""
from __future__ import annotations

from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.models.coverage_matrix import MCDCMatrix
from ifl_mcdc.models.probe_record import ProbeLog


class AcceptanceGate:
    """判斷新測試案例是否有效降低 MC/DC 覆蓋率損失。"""

    def __init__(self, engine: MCDCCoverageEngine) -> None:
        self.engine = engine

    def evaluate(
        self,
        matrix: MCDCMatrix,
        log: ProbeLog,
        new_test_id: str,
    ) -> bool:
        """呼叫 engine.update()，回傳 L(X) 是否降低。

        Args:
            matrix: 當前 MC/DC 覆蓋率矩陣（原地修改）。
            log: 探針觀測日誌（須包含 new_test_id 的記錄）。
            new_test_id: 新加入的測試案例 ID。

        Returns:
            True 若 L(X) 因此次更新而降低，否則 False。
        """
        return self.engine.update(matrix, log, new_test_id)
