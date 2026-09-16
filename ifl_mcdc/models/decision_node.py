"""
決策節點與原子條件的資料模型。

TC-U-01: AtomicCondition.evaluate 基本測試
TC-U-02: ConditionSet.get_coupled OR 型耦合測試
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ifl_mcdc.exceptions import ASTParseError


@dataclass
class AtomicCondition:
    """單一原子條件，格式 'D{n}.c{m}'。"""

    cond_id: str
    expression: str
    var_names: list[str]
    negated: bool = False
    ast_node: object | None = field(default=None, repr=False)

    def evaluate(self, bindings: dict[str, object]) -> bool:
        """使用 eval 對 expression 求值。

        Args:
            bindings: 變數名稱 → 值的字典。

        Returns:
            條件的布林值。

        Raises:
            ASTParseError: eval 執行失敗時。
        """
        try:
            result = eval(self.expression, {}, bindings)  # noqa: S307
            return bool(result)
        except Exception as exc:
            raise ASTParseError(
                f"AtomicCondition.evaluate 失敗 (cond_id={self.cond_id!r}, "
                f"expression={self.expression!r}): {exc}"
            ) from exc


@dataclass
class ConditionSet:
    """一個決策點的所有原子條件及其耦合矩陣。"""

    decision_id: str
    conditions: list[AtomicCondition]
    coupling_matrix: list[list[str | None]]  # k×k，值 "OR"/"AND"/None
    k: int = field(init=False)

    def __post_init__(self) -> None:
        self.k = len(self.conditions)

    def get_coupled(
        self, cond_id: str
    ) -> list[tuple[AtomicCondition, str]]:
        """回傳與指定條件直接耦合的條件列表。

        Args:
            cond_id: 目標條件的 ID，格式 "D{n}.c{m}"。

        Returns:
            [(AtomicCondition, coupling_type), ...]，coupling_type 為 "OR" 或 "AND"。

        Raises:
            KeyError: cond_id 不存在時。
        """
        idx: int | None = None
        for i, cond in enumerate(self.conditions):
            if cond.cond_id == cond_id:
                idx = i
                break
        if idx is None:
            raise KeyError(f"cond_id {cond_id!r} 不存在於 decision_id={self.decision_id!r}")

        result: list[tuple[AtomicCondition, str]] = []
        for j, cond in enumerate(self.conditions):
            if j == idx:
                continue
            coupling = self.coupling_matrix[idx][j]
            if coupling is not None:
                result.append((cond, coupling))
        return result


@dataclass
class DecisionNode:
    """AST 中一個決策節點（if/while/assert/ternary）的完整描述。"""

    node_id: str
    node_type: str        # "If" | "While" | "Assert" | "IfExp"
    line_no: int
    expression_str: str
    condition_set: ConditionSet
    source_context: str = ""
