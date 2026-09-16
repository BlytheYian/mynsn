"""
C 語言 AST 解析器：使用 libclang 走訪 C 原始碼，識別決策節點並輸出 DecisionNode 清單。

對應 Python 版 layer1/ast_parser.py，輸出相同的 DecisionNode 格式，
確保下游（SMT 合成、LLM、Coverage Engine）無需修改。

關鍵設計決策：
  1. expression_str / expression 儲存 Python 語法（&&→and, ||→or, !→not），
     讓 ASTToZ3Converter 可直接呼叫 ast.parse() 而不用改動。
  2. AtomicCondition.ast_node 存放 libclang Cursor（已是 object|None 型別）。
  3. 耦合矩陣由 CCouplingGraphBuilder 建構，以 (kind, line, col) 作為 cursor 識別鍵。

依賴：pip install libclang
"""
from __future__ import annotations

import re
from pathlib import Path

from clang.cindex import CursorKind, Index

from ifl_mcdc.exceptions import ASTParseError
from ifl_mcdc.layer1.c_coupling_graph import CCouplingGraphBuilder
from ifl_mcdc.models.decision_node import AtomicCondition, ConditionSet, DecisionNode

# 透明穿透層：這些 CursorKind 只是包裹，不影響邏輯結構
# libclang 18+ 無 IMPLICIT_CAST_EXPR；隱式轉型以 UNEXPOSED_EXPR 表示
_TRANSPARENT_KINDS = frozenset({
    CursorKind.PAREN_EXPR,
    CursorKind.UNEXPOSED_EXPR,
    CursorKind.CSTYLE_CAST_EXPR,
})


def _normalize_c_to_py(text: str) -> str:
    """將 C 布林運算子正規化為 Python 語法，讓 ast.parse() 可讀。

    &&  →  and
    ||  →  or
    !x  →  not x  （不替換 !=）
    換行/多餘空白 → 單空格（多行條件展平為一行）
    """
    text = text.replace("&&", " and ")
    text = text.replace("||", " or ")
    text = re.sub(r"!(?!=)", "not ", text)
    text = re.sub(r"\s+", " ", text)   # 折疊所有空白（含換行）
    return text.strip()


class CLibclangASTParser:
    """使用 libclang 解析 C 原始碼，識別 if 決策節點。

    輸出與 ASTParser 相同格式的 DecisionNode 清單，
    可直接銜接現有的 SMT 合成、LLM 生成、Coverage 追蹤。
    """

    def __init__(self) -> None:
        self._decision_nodes: list[DecisionNode] = []
        self._node_counter: int = 0
        self._source_lines: list[str] = []
        self._src: str = ""
        self._src_bytes: bytes = b""  # libclang extent.offset 為 byte offset，必須用 bytes 切片

    # ──────────────────────────────────────────────────────────
    # 公開介面
    # ──────────────────────────────────────────────────────────

    def parse_file(
        self,
        filepath: str | Path,
        func_name: str | None = None,
    ) -> list[DecisionNode]:
        """解析 C 原始碼檔案，回傳 DecisionNode 列表。

        Args:
            filepath:  C 原始碼路徑（.c 或 .h）。
            func_name: 指定只解析該函數；None 表示解析全部函數定義。

        Returns:
            DecisionNode 列表（副本）。

        Raises:
            ASTParseError: 檔案不存在或 libclang 解析失敗。
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise ASTParseError(f"檔案不存在：{filepath}")

        self._src_bytes = filepath.read_bytes()
        self._src = self._src_bytes.decode("utf-8")
        self._source_lines = self._src.splitlines()
        self._decision_nodes = []
        self._node_counter = 0

        index = Index.create()
        try:
            tu = index.parse(str(filepath), args=["-std=c11"])
        except Exception as exc:
            raise ASTParseError(f"libclang 解析失敗：{exc}") from exc

        self._walk_tu(tu.cursor, func_name)
        return list(self._decision_nodes)

    # ──────────────────────────────────────────────────────────
    # AST 走訪
    # ──────────────────────────────────────────────────────────

    def _walk_tu(self, cursor, target_func: str | None) -> None:
        """從 Translation Unit 根節點找到目標函數定義。"""
        if cursor.kind == CursorKind.FUNCTION_DECL and cursor.is_definition():
            if target_func is None or cursor.spelling == target_func:
                self._process_function(cursor)
            return  # 不跨函數遞迴

        for child in cursor.get_children():
            self._walk_tu(child, target_func)

    def _process_function(self, func_cursor) -> None:
        """走訪函數體，登記所有 IF_STMT 和獨立 CONDITIONAL_OPERATOR 為 DecisionNode。

        cond_depth > 0 時表示在 if/while 的條件子樹或三元式的條件裡，
        此時內部的 CONDITIONAL_OPERATOR 屬於計算輔助（如 ALIM 查表），不單獨登記。
        """
        cond_depth = [0]  # 可變容器讓閉包修改

        def visit(cursor) -> None:
            if cursor.kind == CursorKind.IF_STMT:
                self._register_if(cursor)
                children = list(cursor.get_children())
                # 條件子樹：深度遞增（不登記內部三元式）
                if children:
                    cond_depth[0] += 1
                    visit(children[0])
                    cond_depth[0] -= 1
                # then / else：正常走訪
                for child in children[1:]:
                    visit(child)

            elif cursor.kind == CursorKind.CONDITIONAL_OPERATOR:
                children = list(cursor.get_children())
                if cond_depth[0] == 0 and len(children) >= 3:
                    # 獨立三元式：登記為 IfExp 決策節點
                    self._register_conditional(cursor)
                # 三元式自身的條件子樹：深度遞增
                if children:
                    cond_depth[0] += 1
                    visit(children[0])
                    cond_depth[0] -= 1
                # true / false 分支：正常走訪（可能含獨立三元式）
                for child in children[1:]:
                    visit(child)

            else:
                for child in cursor.get_children():
                    visit(child)

        visit(func_cursor)

    def _register_if(self, if_cursor) -> None:
        """將一個 IF_STMT 轉換並登記為 DecisionNode。"""
        children = list(if_cursor.get_children())
        if not children:
            return

        cond_cursor = children[0]  # IF_STMT 第一個 child 為條件表達式

        self._node_counter += 1
        node_id = f"D{self._node_counter}"
        line_no = if_cursor.location.line

        # 整個條件的 Python 語法字串（供 SMT 合成器 ast.parse 用）
        expr_str = self._extract_py_expr(cond_cursor)

        # 分解原子條件 + 耦合矩陣
        condition_set = self._decompose_conditions(node_id, cond_cursor)

        self._decision_nodes.append(DecisionNode(
            node_id=node_id,
            node_type="If",
            line_no=line_no,
            expression_str=expr_str,
            condition_set=condition_set,
            source_context=self._get_context(line_no),
        ))

    def _register_conditional(self, cond_cursor) -> None:
        """將獨立的 CONDITIONAL_OPERATOR（三元式）登記為 DecisionNode（node_type='IfExp'）。

        三元式 'cond ? true_val : false_val' 中，只有 cond（第一個 child）
        被視為決策條件。node_type 使用 'IfExp' 與 Python 版統一。
        """
        children = list(cond_cursor.get_children())
        if not children:
            return

        cond_child = self._unwrap(children[0])  # 條件部分（穿透 PAREN_EXPR 等）

        self._node_counter += 1
        node_id = f"D{self._node_counter}"
        line_no = cond_cursor.location.line          # libclang 回傳 '?' 所在行

        expr_str      = self._extract_py_expr(cond_child)
        condition_set = self._decompose_conditions(node_id, cond_child)

        self._decision_nodes.append(DecisionNode(
            node_id=node_id,
            node_type="IfExp",
            line_no=line_no,
            expression_str=expr_str,
            condition_set=condition_set,
            source_context=self._get_context(line_no),
        ))

    # ──────────────────────────────────────────────────────────
    # 條件分解
    # ──────────────────────────────────────────────────────────

    def _decompose_conditions(self, decision_id: str, root_cursor) -> ConditionSet:
        """遞迴分解布林表達式，提取原子條件列表及耦合矩陣。

        分解規則（對應 Python 版 ASTParser._decompose_conditions）：
          BINARY_OPERATOR(&&/||) → 遞迴兩個子節點
          UNARY_OPERATOR(!)      → 遞迴子節點，翻轉 negated 旗標
          其他                   → 終止，視為原子條件
        """
        conditions: list[AtomicCondition] = []
        counter = [0]

        def recurse(cursor, negated: bool = False) -> None:
            cursor = self._unwrap(cursor)

            if self._is_logical_and_or(cursor):
                for child in cursor.get_children():
                    recurse(child, negated)
                return

            if self._is_logical_not(cursor):
                children = list(cursor.get_children())
                if children:
                    recurse(children[0], not negated)
                return

            # 原子條件
            counter[0] += 1
            cond_id = f"{decision_id}.c{counter[0]}"
            conditions.append(AtomicCondition(
                cond_id=cond_id,
                expression=self._extract_py_expr(cursor),
                var_names=self._get_var_names(cursor),
                negated=negated,
                ast_node=cursor,  # 存 libclang Cursor，供 CCouplingGraphBuilder 識別
            ))

        recurse(root_cursor)

        coupling = CCouplingGraphBuilder().build(
            decision_id, root_cursor, conditions, self._src_bytes
        )
        return ConditionSet(
            decision_id=decision_id,
            conditions=conditions,
            coupling_matrix=coupling,
        )

    # ──────────────────────────────────────────────────────────
    # Cursor 工具函式
    # ──────────────────────────────────────────────────────────

    def _unwrap(self, cursor):
        """穿透 PAREN_EXPR / IMPLICIT_CAST_EXPR 等透明包裹層。"""
        while cursor.kind in _TRANSPARENT_KINDS:
            children = list(cursor.get_children())
            if not children:
                break
            cursor = children[0]
        return cursor

    def _is_logical_and_or(self, cursor) -> bool:
        """判斷是否為 && 或 || 的 BINARY_OPERATOR。"""
        if cursor.kind != CursorKind.BINARY_OPERATOR:
            return False
        return self._get_binary_op_text(cursor) in ("&&", "||")

    def _is_logical_not(self, cursor) -> bool:
        """判斷是否為 ! 的 UNARY_OPERATOR。"""
        if cursor.kind != CursorKind.UNARY_OPERATOR:
            return False
        return self._get_unary_op_text(cursor) == "!"

    def _get_binary_op_text(self, cursor) -> str:
        """從原始碼（bytes）讀取兩個子節點之間的運算子文字。"""
        children = list(cursor.get_children())
        if len(children) < 2:
            return ""
        left_end = children[0].extent.end.offset
        right_start = children[1].extent.start.offset
        return self._src_bytes[left_end:right_start].decode("utf-8").strip()

    def _get_unary_op_text(self, cursor) -> str:
        """從原始碼（bytes）讀取 UNARY_OPERATOR 前置運算子文字。"""
        children = list(cursor.get_children())
        if not children:
            return ""
        child_start = children[0].extent.start.offset
        cursor_start = cursor.extent.start.offset
        return self._src_bytes[cursor_start:child_start].decode("utf-8").strip()

    def _extract_py_expr(self, cursor) -> str:
        """提取 cursor 範圍的 C 原始碼（bytes offset），正規化為 Python 語法。"""
        start = cursor.extent.start.offset
        end = cursor.extent.end.offset
        raw = self._src_bytes[start:end].decode("utf-8").strip()
        return _normalize_c_to_py(raw)

    def _get_var_names(self, cursor) -> list[str]:
        """遞迴收集子樹中所有變數參照名稱（DECL_REF_EXPR）。"""
        names: list[str] = []

        def walk(c) -> None:
            if c.kind == CursorKind.DECL_REF_EXPR:
                names.append(c.spelling)
            for child in c.get_children():
                walk(child)

        walk(cursor)
        # 保序去重（同一變數在複合條件中可能重複出現）
        return list(dict.fromkeys(names))

    def _get_context(self, line_no: int, radius: int = 2) -> str:
        """回傳 line_no 前後各 radius 行的原始碼片段。"""
        start = max(0, line_no - radius - 1)
        end = min(len(self._source_lines), line_no + radius)
        return "\n".join(self._source_lines[start:end])
