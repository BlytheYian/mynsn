"""
AST 解析器：走訪 Python AST，識別所有決策節點並輸出 DecisionNode 清單。

TC-U-01: 基本 if 節點識別
TC-U-02: 巢狀 if 的兩層識別
TC-U-03: IfExp 行內三元表達式識別
TC-U-04: 語法錯誤原始碼的錯誤處理
"""
from __future__ import annotations

import ast
from pathlib import Path

from ifl_mcdc.exceptions import ASTParseError
from ifl_mcdc.layer1.coupling_graph import CouplingGraphBuilder
from ifl_mcdc.models.decision_node import AtomicCondition, ConditionSet, DecisionNode


class ASTParser(ast.NodeVisitor):
    """走訪 Python AST，識別所有決策節點並輸出 DecisionNode 清單。

    繼承 ast.NodeVisitor —— 每遇到目標節點類型就觸發對應的 visit_X 方法。
    """

    DECISION_NODE_TYPES = ("If", "While", "Assert", "IfExp")

    def __init__(self) -> None:
        self._decision_nodes: list[DecisionNode] = []
        self._node_counter: int = 0
        self._source_lines: list[str] = []
        self._condition_depth: int = 0  # 進入條件子樹時遞增，避免誤登記計算用 IfExp

    def parse_file(self, filepath: str | Path) -> list[DecisionNode]:
        """主要入口：解析原始碼檔案，回傳 DecisionNode 列表副本。

        Args:
            filepath: Python 原始碼檔案路徑。

        Returns:
            解析出的 DecisionNode 列表（副本）。

        Raises:
            ASTParseError: 語法錯誤時，訊息含行號。
        """
        source = Path(filepath).read_text(encoding="utf-8")
        return self.parse_source(source)

    def parse_source(self, source: str) -> list[DecisionNode]:
        """解析原始碼字串，回傳 DecisionNode 列表副本。

        Args:
            source: Python 原始碼字串。

        Returns:
            解析出的 DecisionNode 列表（副本）。

        Raises:
            ASTParseError: 語法錯誤時，訊息含行號。
        """
        self._decision_nodes = []
        self._node_counter = 0
        self._source_lines = source.splitlines()
        self._condition_depth = 0
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            lineno = exc.lineno or 0
            raise ASTParseError(
                f"語法錯誤（第 {lineno} 行）：{exc.msg}"
            ) from exc
        self.visit(tree)
        return list(self._decision_nodes)

    def visit_If(self, node: ast.If) -> None:
        """處理 if 語句節點。"""
        self._register_decision(node, "If", node.test)
        # 條件子樹：IfExp 在此處是計算用的，不應登記為決策節點
        self._condition_depth += 1
        self.visit(node.test)
        self._condition_depth -= 1
        # body / orelse：可能含獨立 IfExp，深度維持原值
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)

    def visit_While(self, node: ast.While) -> None:
        """處理 while 語句節點。"""
        self._register_decision(node, "While", node.test)
        self._condition_depth += 1
        self.visit(node.test)
        self._condition_depth -= 1
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)

    def visit_Assert(self, node: ast.Assert) -> None:
        """處理 assert 語句節點。"""
        if node.test:
            self._register_decision(node, "Assert", node.test)
            self._condition_depth += 1
            self.visit(node.test)
            self._condition_depth -= 1

    def visit_IfExp(self, node: ast.IfExp) -> None:
        """處理三元表達式節點（x if cond else y）。

        只登記「語句層級」的 IfExp（賦值、回傳、函式引數等）。
        在 if/while/assert 條件子樹或另一個 IfExp 的 test 裡的 IfExp
        （例如 ALIM 查表 400 if x==0 else 500 ...）屬於計算輔助，不登記。
        """
        if self._condition_depth > 0:
            # 在條件子樹內：走訪 body/orelse（可能含獨立 IfExp），但不登記
            self._condition_depth += 1
            self.visit(node.test)
            self._condition_depth -= 1
            self.visit(node.body)
            self.visit(node.orelse)
            return

        self._register_decision(node, "IfExp", node.test)
        # IfExp 自身的 test 也是條件子樹
        self._condition_depth += 1
        self.visit(node.test)
        self._condition_depth -= 1
        # body / orelse 可能含獨立 IfExp
        self.visit(node.body)
        self.visit(node.orelse)

    def _register_decision(
        self, node: ast.AST, ntype: str, test_expr: ast.expr
    ) -> None:
        """登記一個決策節點。"""
        self._node_counter += 1
        node_id = f"D{self._node_counter}"
        line_no = getattr(node, "lineno", 0)
        expr_str = ast.unparse(test_expr)
        condition_set = self._decompose_conditions(node_id, test_expr)
        context = self._get_context(line_no)
        self._decision_nodes.append(
            DecisionNode(
                node_id=node_id,
                node_type=ntype,
                line_no=line_no,
                expression_str=expr_str,
                condition_set=condition_set,
                source_context=context,
            )
        )

    def _decompose_conditions(
        self, decision_id: str, node: ast.expr
    ) -> ConditionSet:
        """遞迴分解布林表達式，提取原子條件。

        規則：
        - BoolOp (and/or) → 遞迴進入 values 列表
        - UnaryOp (not)   → 遞迴進入 operand，標記 negated=True
        - Compare / Name / Call / Constant → 終止，此為原子條件
        """
        conditions: list[AtomicCondition] = []
        cond_counter = [0]

        def recurse(expr: ast.expr, negated: bool = False) -> None:
            if isinstance(expr, ast.BoolOp):
                for value in expr.values:
                    recurse(value, negated)
            elif isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
                recurse(expr.operand, not negated)
            else:
                cond_counter[0] += 1
                cond_id = f"{decision_id}.c{cond_counter[0]}"
                expr_str = ast.unparse(expr)
                var_names = [
                    n.id for n in ast.walk(expr) if isinstance(n, ast.Name)
                ]
                conditions.append(
                    AtomicCondition(
                        cond_id=cond_id,
                        expression=expr_str,
                        var_names=var_names,
                        negated=negated,
                        ast_node=expr,
                    )
                )

        recurse(node)
        coupling = CouplingGraphBuilder().build(decision_id, node, conditions)
        return ConditionSet(
            decision_id=decision_id,
            conditions=conditions,
            coupling_matrix=coupling,
        )

    def _get_context(self, line_no: int, radius: int = 2) -> str:
        """回傳 line_no 前後各 radius 行的原始碼片段。"""
        start = max(0, line_no - radius - 1)
        end = min(len(self._source_lines), line_no + radius)
        return "\n".join(self._source_lines[start:end])
