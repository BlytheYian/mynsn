"""
缺口分析器：從 MCDCMatrix 提取未覆蓋的翻轉對，按難度排序。

難度估計公式：耦合邊數量 / (k-1)

TC-U-24: 缺口清單正確識別（k=4，覆蓋 2 個 → 缺口 6 個）
TC-U-25: 難度排序正確（升序）
TC-U-26: L(X)=0 時回傳空清單
TC-U-27: 難度估計公式驗證
"""
from __future__ import annotations

from ifl_mcdc.models.coverage_matrix import GapEntry, MCDCMatrix
from ifl_mcdc.models.decision_node import ConditionSet


class GapAnalyzer:
    """從 MCDCMatrix 提取所有缺口，按 estimated_difficulty 升序排列。"""

    def analyze(self, matrix: MCDCMatrix) -> list[GapEntry]:
        """提取所有未覆蓋的翻轉對並按難度排序。

        Args:
            matrix: 當前 MC/DC 覆蓋率矩陣。

        Returns:
            GapEntry 列表，按 estimated_difficulty 升序排列。
            若已達 100% 覆蓋，回傳空列表。
        """
        gaps: list[GapEntry] = []
        for cond in matrix.condition_set.conditions:
            for flip in ("F2T", "T2F"):
                if (cond.cond_id, flip) not in matrix._covered:
                    difficulty = self._estimate_difficulty(
                        matrix.condition_set, cond.cond_id
                    )
                    gaps.append(
                        GapEntry(
                            condition_id=cond.cond_id,
                            flip_direction=flip,
                            missing_pair_type="unique_cause",
                            estimated_difficulty=difficulty,
                        )
                    )
        return sorted(gaps, key=lambda g: g.estimated_difficulty)

    def _estimate_difficulty(
        self, cond_set: ConditionSet, cond_id: str
    ) -> float:
        """計算指定條件的難度估計值。

        難度 = 耦合邊數量 / (k-1)
        耦合邊越多，需要同時約束的條件越多，SMT 求解難度越高。

        Args:
            cond_set: 條件集合。
            cond_id: 目標條件 ID。

        Returns:
            0.0 ≤ difficulty ≤ 1.0
        """
        if cond_set.k <= 1:
            return 0.0
        coupled = cond_set.get_coupled(cond_id)
        return len(coupled) / (cond_set.k - 1)
