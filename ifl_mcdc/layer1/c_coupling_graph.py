"""
C 語言耦合圖建構器：使用 libclang Cursor 建立 k×k 耦合鄰接矩陣。
對應 Python 版的 layer1/coupling_graph.py，邏輯相同，只換掉 AST 型別判斷。

矩陣值語義：
  "AND" — 兩條件共處同一個 && 運算
  "OR"  — 兩條件共處同一個 || 運算
  None  — 無直接耦合關係
"""
from __future__ import annotations

from clang.cindex import CursorKind

from ifl_mcdc.models.decision_node import AtomicCondition

# 需要透明穿透的 CursorKind（包裹層，不影響邏輯結構）
# libclang 18+ 無 IMPLICIT_CAST_EXPR；隱式轉型以 UNEXPOSED_EXPR 表示
_TRANSPARENT_KINDS = frozenset({
    CursorKind.PAREN_EXPR,
    CursorKind.UNEXPOSED_EXPR,
    CursorKind.CSTYLE_CAST_EXPR,
})


def _cursor_key(cursor) -> tuple:
    """返回 cursor 的穩定識別鍵。

    使用 (kind, line, column) 而非 id()，因為 libclang Cursor 是值型別，
    同一 AST 節點在不同次走訪中會建立不同的 Python 物件，但位置資訊一致。
    """
    return (cursor.kind.value, cursor.location.line, cursor.location.column)


class CCouplingGraphBuilder:
    """從 libclang Cursor 樹建立 k×k 耦合鄰接矩陣。

    用法與 CouplingGraphBuilder 相同，但接受 libclang Cursor 而非 ast.expr。
    額外需要 src（原始碼字串）以提取運算子文字。
    """

    def build(
        self,
        decision_id: str,
        root_cursor,
        conditions: list[AtomicCondition],
        src: bytes,
    ) -> list[list[str | None]]:
        """建立耦合矩陣。

        Args:
            decision_id: 決策節點 ID（供除錯用）。
            root_cursor:  整個條件表達式的根 Cursor（IF_STMT 的第一個 child）。
            conditions:   已提取的原子條件列表（其 ast_node 為 libclang Cursor）。
            src:          C 原始碼完整字串（用於提取運算子文字）。

        Returns:
            k×k 矩陣，值為 "AND"、"OR" 或 None。
        """
        k = len(conditions)
        matrix: list[list[str | None]] = [[None] * k for _ in range(k)]

        if k <= 1:
            return matrix

        # cursor_key → conditions 索引
        node_to_idx: dict[tuple, int] = {
            _cursor_key(c.ast_node): i
            for i, c in enumerate(conditions)
            if c.ast_node is not None
        }

        def _set(a: int, b: int, op: str) -> None:
            if a == b:
                return
            if matrix[a][b] == "OR":  # OR 優先保留（比 AND 弱）
                return
            matrix[a][b] = op
            matrix[b][a] = op

        def _unwrap(cursor):
            """穿透透明包裹層，回傳實質表達式 cursor。"""
            while cursor.kind in _TRANSPARENT_KINDS:
                children = list(cursor.get_children())
                if not children:
                    break
                cursor = children[0]
            return cursor

        def _get_binary_op(cursor) -> str:
            """從原始碼（bytes）提取 BINARY_OPERATOR 的運算子文字。"""
            children = list(cursor.get_children())
            if len(children) < 2:
                return ""
            left_end = children[0].extent.end.offset
            right_start = children[1].extent.start.offset
            return src[left_end:right_start].decode("utf-8").strip()

        def _get_unary_op(cursor) -> str:
            """從原始碼（bytes）提取 UNARY_OPERATOR 的運算子文字（前置）。"""
            children = list(cursor.get_children())
            if not children:
                return ""
            child_start = children[0].extent.start.offset
            cursor_start = cursor.extent.start.offset
            return src[cursor_start:child_start].decode("utf-8").strip()

        def _is_and_or(cursor) -> tuple[bool, str]:
            """判斷是否為 && 或 ||，回傳 (是否成立, 運算子文字)。"""
            if cursor.kind != CursorKind.BINARY_OPERATOR:
                return False, ""
            op = _get_binary_op(cursor)
            return op in ("&&", "||"), op

        def _is_not(cursor) -> bool:
            """判斷是否為 ! 一元運算。"""
            if cursor.kind != CursorKind.UNARY_OPERATOR:
                return False
            return _get_unary_op(cursor) == "!"

        def _all_leaves(cursor) -> list[int]:
            """收集此子樹所有原子條件的 conditions 索引。

            對應 Python 版 CouplingGraphBuilder._all_leaves()。
            """
            cursor = _unwrap(cursor)

            is_ao, _ = _is_and_or(cursor)
            if is_ao:
                result: list[int] = []
                for child in cursor.get_children():
                    result.extend(_all_leaves(child))
                return result

            if _is_not(cursor):
                children = list(cursor.get_children())
                if children:
                    return _all_leaves(children[0])
                return []

            # 葉子：查 node_to_idx
            idx = node_to_idx.get(_cursor_key(cursor))
            return [idx] if idx is not None else []

        def _traverse(cursor) -> None:
            """遞迴走訪，對跨子樹的葉子配對設定耦合關係。

            對應 Python 版 CouplingGraphBuilder._traverse()。
            """
            cursor = _unwrap(cursor)

            is_ao, op = _is_and_or(cursor)
            if not is_ao:
                if _is_not(cursor):
                    children = list(cursor.get_children())
                    if children:
                        _traverse(children[0])
                return

            coupling_type = "OR" if op == "||" else "AND"
            children = list(cursor.get_children())

            # 每個直接子節點的葉子集合
            child_leaves = [_all_leaves(child) for child in children]

            # 跨子樹配對（同 Python 版邏輯）
            for i in range(len(child_leaves)):
                for j in range(i + 1, len(child_leaves)):
                    for a in child_leaves[i]:
                        for b in child_leaves[j]:
                            _set(a, b, coupling_type)

            # 遞迴子 BoolOp
            for child in children:
                _traverse(child)

        _traverse(root_cursor)
        return matrix
