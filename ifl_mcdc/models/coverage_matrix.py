"""
MC/DC 覆蓋率矩陣與間隙條目。

TC-U-03: MCDCMatrix.compute_loss 初始值測試
TC-U-04: MCDCMatrix.coverage_ratio 計算測試
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ifl_mcdc.models.decision_node import ConditionSet


@dataclass
class GapEntry:
    """尚未覆蓋的獨立對翻轉方向記錄。"""

    condition_id: str
    flip_direction: str          # "F2T" 或 "T2F"
    missing_pair_type: str       # "unique_cause"
    estimated_difficulty: float  # 0.0 ~ 1.0


@dataclass
class MCDCMatrix:
    """追蹤每個條件已覆蓋的翻轉方向（獨立對）。"""

    condition_set: ConditionSet
    _covered: set[tuple[str, str]] = field(default_factory=set, repr=False)
    _infeasible: set[tuple[str, str]] = field(default_factory=set, repr=False)

    @property
    def k(self) -> int:
        """條件數量。"""
        return self.condition_set.k

    @property
    def coverage_ratio(self) -> float:
        """傳統覆蓋率 = 已覆蓋 / 2k（全部缺口，含 infeasible）。k=0 時回傳 1.0。"""
        if self.k == 0:
            return 1.0
        return len(self._covered) / (2 * self.k)

    @property
    def feasible_count(self) -> int:
        """可行缺口總數 = 2k - infeasible 數量。"""
        return max(0, 2 * self.k - len(self._infeasible))

    @property
    def effective_coverage_ratio(self) -> float:
        """有效覆蓋率 = 已覆蓋可行缺口 / 可行缺口總數（排除 infeasible）。
        可行缺口為零時回傳 1.0（視為已完全覆蓋）。"""
        if self.feasible_count == 0:
            return 1.0
        covered_feasible = len(self._covered - self._infeasible)
        return covered_feasible / self.feasible_count

    def compute_loss(self) -> int:
        """傳統損失 = 2k - 已覆蓋數（含 infeasible）。"""
        return (2 * self.k) - len(self._covered)

    def compute_effective_loss(self) -> int:
        """有效損失 = 可行缺口 - 已覆蓋可行缺口（排除 infeasible 後的剩餘缺口數）。"""
        covered_feasible = len(self._covered - self._infeasible)
        return self.feasible_count - covered_feasible

    def mark_infeasible(self, cond_id: str, flip_direction: str) -> None:
        """標記此翻轉對為不可行路徑（Φ∩Valid=∅ 或 Z3 UNSAT）。

        Args:
            cond_id: 條件 ID。
            flip_direction: 必須為 "F2T" 或 "T2F"。

        Raises:
            ValueError: flip_direction 不合法時。
        """
        if flip_direction not in ("F2T", "T2F"):
            raise ValueError(f"無效的翻轉方向：{flip_direction!r}")
        self._infeasible.add((cond_id, flip_direction))

    def get_gap_list(self) -> list[GapEntry]:
        """回傳所有尚未覆蓋的 GapEntry 列表。"""
        gaps: list[GapEntry] = []
        for cond in self.condition_set.conditions:
            for flip in ("F2T", "T2F"):
                if (cond.cond_id, flip) not in self._covered:
                    gaps.append(
                        GapEntry(
                            condition_id=cond.cond_id,
                            flip_direction=flip,
                            missing_pair_type="unique_cause",
                            estimated_difficulty=0.5,
                        )
                    )
        return gaps

    def mark_covered(self, cond_id: str, flip_direction: str) -> None:
        """標記指定條件的翻轉方向為已覆蓋。

        Args:
            cond_id: 條件 ID。
            flip_direction: 必須為 "F2T" 或 "T2F"。

        Raises:
            ValueError: flip_direction 不合法時。
        """
        if flip_direction not in ("F2T", "T2F"):
            raise ValueError(
                f"flip_direction 必須為 'F2T' 或 'T2F'，得到 {flip_direction!r}"
            )
        self._covered.add((cond_id, flip_direction))
