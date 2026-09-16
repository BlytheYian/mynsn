"""
耦合圖建構器：從 BoolOp AST 結構建立 k×k 耦合鄰接矩陣。
"""
from __future__ import annotations

import ast

from ifl_mcdc.models.decision_node import AtomicCondition


class CouplingGraphBuilder:

    def build(
        self,
        decision_id: str,
        root_expr: ast.expr,
        conditions: list[AtomicCondition],
    ) -> list[list[str | None]]:
        k = len(conditions)
        matrix: list[list[str | None]] = [[None] * k for _ in range(k)]

        node_to_idx: dict[int, int] = {
            id(c.ast_node): i
            for i, c in enumerate(conditions)
            if c.ast_node is not None
        }

        def _set(a: int, b: int, op: str) -> None:
            if a == b:
                return
            if matrix[a][b] == "OR":
                return
            matrix[a][b] = op
            matrix[b][a] = op

        def _all_leaves(expr: ast.expr) -> list[int]:
            """收集此子樹所有原子葉子索引。"""
            if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
                return _all_leaves(expr.operand)
            if isinstance(expr, ast.BoolOp):
                result: list[int] = []
                for v in expr.values:
                    result.extend(_all_leaves(v))
                return result
            idx = node_to_idx.get(id(expr))
            return [idx] if idx is not None else []

        def _traverse(expr: ast.expr) -> None:
            if not isinstance(expr, ast.BoolOp):
                if isinstance(expr, ast.UnaryOp):
                    _traverse(expr.operand)
                return

            op = "OR" if isinstance(expr.op, ast.Or) else "AND"

            # 每個直接子節點的葉子列表
            child_leaves: list[list[int]] = [
                _all_leaves(v) for v in expr.values
            ]

            # 同一子節點內部已由遞迴處理，這裡只做跨子節點配對
            for i in range(len(child_leaves)):
                for j in range(i + 1, len(child_leaves)):
                    for a in child_leaves[i]:
                        for b in child_leaves[j]:
                            _set(a, b, op)

            # 遞迴處理子 BoolOp
            for v in expr.values:
                _traverse(v)

        _traverse(root_expr)
        return matrix