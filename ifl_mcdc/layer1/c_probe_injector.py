"""
C 語言探針注入器：對 C 原始碼進行文字層級插樁。

對應 Python 版 layer1/probe_injector.py，但改用原始碼文字替換而非 AST 重寫，
因為 C 沒有等效的 exec() 動態執行機制。

核心設計：
  1. 所有原子條件先個別求值存入 int 暫存變數，繞開 C 的短路求值。
  2. 呼叫 _ifl_record_cond / _ifl_record_decision 寫探針 log 到 stdout。
  3. 原 if (condition) 改為 if (_D{n}_decision)。
  4. 使用 byte offset 做文字替換，正確處理多位元組 UTF-8 字元（如中文注釋）。

依賴：pip install libclang（重新解析取得 IF_STMT byte offset）
"""
from __future__ import annotations

import re
from pathlib import Path

from clang.cindex import CursorKind, Index

from ifl_mcdc.models.decision_node import DecisionNode


# ──────────────────────────────────────────────────────────────────────────────
# 探針執行期標頭（ifl_probe.h）——注入後的 .c 會 #include 這個
# ──────────────────────────────────────────────────────────────────────────────

IFL_PROBE_HEADER = """\
#ifndef IFL_PROBE_H
#define IFL_PROBE_H

#include <stdio.h>

/* 由測試 harness 在每次函數呼叫前設定 */
extern int _ifl_test_id;

static inline void _ifl_record_cond(const char* cond_id, int value) {
    fprintf(stdout, "PROBE|%d|%s|%d\\n", _ifl_test_id, cond_id, value);
    fflush(stdout);
}

static inline void _ifl_record_decision(const char* decision_id, int value) {
    fprintf(stdout, "PROBE_DEC|%d|%s|%d\\n", _ifl_test_id, decision_id, value);
    fflush(stdout);
}

/* 三元式（? :）專用探針：記錄條件值並回傳對應分支的值 */
static inline int _ifl_probe_ternary(
        const char* decision_id, const char* cond_id,
        int cond_val, int true_val, int false_val) {
    _ifl_record_cond(cond_id, cond_val);
    _ifl_record_decision(decision_id, cond_val);
    return cond_val ? true_val : false_val;
}

#endif /* IFL_PROBE_H */
"""


# ──────────────────────────────────────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────────────────────────────────────

def _line_col_to_offset(src_bytes: bytes, line: int, col: int) -> int:
    """將 1-indexed (line, col) 轉換為 byte offset。

    libclang 的 location.line/column 均為 1-indexed，column 以 byte 計。
    """
    current_line = 1
    i = 0
    while i < len(src_bytes):
        if current_line == line:
            return i + col - 1
        if src_bytes[i] == ord('\n'):
            current_line += 1
        i += 1
    return len(src_bytes)


def _find_line_start(src_bytes: bytes, line: int) -> int:
    """回傳指定行（1-indexed）的 byte offset。"""
    return _line_col_to_offset(src_bytes, line, 1)


def _get_indent(src_bytes: bytes, line_start: int) -> str:
    """取得指定行的前置縮排空白（ASCII 安全）。"""
    i = line_start
    while i < len(src_bytes) and src_bytes[i:i + 1] in (b' ', b'\t'):
        i += 1
    return src_bytes[line_start:i].decode('ascii')


def _find_condition_parens(src_bytes: bytes, if_offset: int) -> tuple[int, int]:
    """從 if 關鍵字位置掃描，找到 if (condition) 的外層 ( 和 ) 的 byte offset。

    Returns:
        (open_paren, close_paren): '(' 和 ')' 各自的 byte offset。
    """
    i = if_offset
    # 跳過 'if' 關鍵字及空白，找到第一個 '('
    while i < len(src_bytes) and src_bytes[i:i + 1] != b'(':
        i += 1
    open_paren = i

    # 找到配對的 ')'
    depth = 1
    i += 1
    while i < len(src_bytes) and depth > 0:
        ch = src_bytes[i:i + 1]
        if ch == b'(':
            depth += 1
        elif ch == b')':
            depth -= 1
        i += 1
    close_paren = i - 1  # 退回到 ')' 的位置

    return open_paren, close_paren


def _find_if_else_end(src_bytes: bytes, close_paren: int) -> int:
    """從條件 ')' 位置出發，找到整個 if-[else if]*-[else] 鏈最後一個 '}' 的 byte offset。

    演算法：
      1. 找到 '{' → 計深度到配對的 '}'
      2. 跳過空白，檢查是否有 'else' 關鍵字
      3. 若有：繼續（處理 else if 和 else），若無：回傳 last '}'
    """
    i = close_paren

    while True:
        # 跳到下一個 '{'
        while i < len(src_bytes) and src_bytes[i:i + 1] != b'{':
            i += 1
        if i >= len(src_bytes):
            return len(src_bytes) - 1

        # 找配對的 '}'
        depth = 1
        i += 1
        while i < len(src_bytes) and depth > 0:
            ch = src_bytes[i:i + 1]
            if ch == b'{':
                depth += 1
            elif ch == b'}':
                depth -= 1
            i += 1
        last_close = i - 1  # 配對的 '}' 位置

        # 跳過空白，檢查是否跟著 'else'
        j = i
        while j < len(src_bytes) and src_bytes[j:j + 1] in (b' ', b'\t', b'\n', b'\r'):
            j += 1

        if src_bytes[j:j + 4] == b'else':
            # 確認是關鍵字（後接空白或 '{'）
            k = j + 4
            if k < len(src_bytes) and src_bytes[k:k + 1] in (b' ', b'\t', b'\n', b'\r', b'{'):
                i = k  # 繼續掃描 else 的主體
                continue

        return last_close  # 沒有 else，回傳最後一個 '}'


def _py_expr_to_c(expr: str) -> str:
    """將 Python 語法布林式轉換回 C 語法。

    and  → &&
    or   → ||
    not  → !
    使用 word boundary 避免誤替換變數名中含 and/or/not 的子串。
    """
    expr = re.sub(r'\bor\b', '||', expr)
    expr = re.sub(r'\band\b', '&&', expr)
    expr = re.sub(r'\bnot\b\s*', '!', expr)
    return expr


# ──────────────────────────────────────────────────────────────────────────────
# 主類別
# ──────────────────────────────────────────────────────────────────────────────

class CProbeInjector:
    """對 C 原始碼進行探針插樁，輸出插樁後的原始碼字串。

    使用方式：
        injector = CProbeInjector()
        instrumented_src = injector.inject(filepath, decision_nodes)
        # 寫出 ifl_probe.h 讓 gcc 可找到探針 API
        Path("ifl_probe.h").write_text(IFL_PROBE_HEADER)
    """

    def inject(
        self,
        filepath: str | Path,
        decision_nodes: list[DecisionNode],
    ) -> str:
        """主入口：回傳插樁後的 C 原始碼字串。

        Args:
            filepath:       原始 .c 檔路徑。
            decision_nodes: 由 CLibclangASTParser 解析出的 DecisionNode 列表。

        Returns:
            插樁後的 C 原始碼（UTF-8 字串）。
        """
        filepath = Path(filepath)
        src_bytes = filepath.read_bytes()

        index = Index.create()
        tu    = index.parse(str(filepath), args=['-std=c11'])

        # 建立 cursor 映射
        if_cursor_map:      dict[int, object] = {}
        ternary_cursor_map: dict[int, object] = {}
        inner_ternary_lines: set[int]         = set()
        self._collect_if_stmts(tu.cursor, if_cursor_map)
        self._collect_ternary_ops(tu.cursor, ternary_cursor_map, inner_ternary_lines)

        # ── Step 1：先處理三元式（使用原始 src_bytes 的 offset，無漂移）──
        ternary_nodes = [dn for dn in decision_nodes if dn.node_type == "IfExp"]

        # 同行多個三元式：first-wins（取外層）
        ternary_by_line: dict[int, DecisionNode] = {}
        for dn in ternary_nodes:
            if dn.line_no not in ternary_by_line:
                ternary_by_line[dn.line_no] = dn

        # 跳過被標為「鏈式 false 分支」的內層三元式，避免 cascading offset 問題
        # 外層注入時會把整段 false 文字（含內層）一起帶入
        processable = [
            (dn, ternary_cursor_map[dn.line_no])
            for dn in ternary_by_line.values()
            if dn.line_no in ternary_cursor_map
            and dn.line_no not in inner_ternary_lines
        ]

        def _ternary_sort_key(item: tuple) -> int:
            children = list(item[1].get_children())
            return children[0].extent.start.offset if children else -1

        processable.sort(key=_ternary_sort_key, reverse=True)

        result = bytearray(src_bytes)
        for dn, cur in processable:
            result = self._inject_ternary(result, cur, dn)

        # ── Step 2：處理 IF_STMT（行號降序）──
        if_nodes    = [dn for dn in decision_nodes if dn.node_type != "IfExp"]
        sorted_if   = sorted(if_nodes, key=lambda dn: dn.line_no, reverse=True)
        for dn in sorted_if:
            if_cursor = if_cursor_map.get(dn.line_no)
            if if_cursor is None:
                continue
            result = self._inject_one(result, if_cursor, dn)

        # 在檔案最頂部插入 #include "ifl_probe.h"
        header_line = b'#include "ifl_probe.h"\n'
        return (header_line + bytes(result)).decode('utf-8')

    # ──────────────────────────────────────────────────────────
    # 內部：AST 走訪
    # ──────────────────────────────────────────────────────────

    def _collect_if_stmts(self, cursor, result: dict) -> None:
        """遞迴收集所有 IF_STMT cursor，以 line_no 為 key。"""
        if cursor.kind == CursorKind.IF_STMT:
            result[cursor.location.line] = cursor
            children = list(cursor.get_children())
            for child in children[1:]:  # 跳過條件 child，遞迴 then/else
                self._collect_if_stmts(child, result)
        else:
            for child in cursor.get_children():
                self._collect_if_stmts(child, result)

    def _collect_ternary_ops(
        self,
        cursor,
        result: dict,
        inner_lines: set | None = None,
    ) -> None:
        """遞迴收集獨立的 CONDITIONAL_OPERATOR，以 line_no 為 key。

        inner_lines：收集「作為另一個三元式 false 分支」的行號集合。
          這些是鏈式三元式（A?a:B?b:c）的內層，不應單獨注入；
          由外層注入時以原始文字整段帶入即可。
        """
        if inner_lines is None:
            inner_lines = set()

        if cursor.kind == CursorKind.IF_STMT:
            children = list(cursor.get_children())
            for child in children[1:]:
                self._collect_ternary_ops(child, result, inner_lines)

        elif cursor.kind == CursorKind.CONDITIONAL_OPERATOR:
            line = cursor.location.line
            if line not in result:
                result[line] = cursor
            children = list(cursor.get_children())
            # true 分支：正常走訪
            if len(children) >= 2:
                self._collect_ternary_ops(children[1], result, inner_lines)
            # false 分支：若也是三元式，標記為 inner（鏈式），再走訪
            if len(children) >= 3:
                false_child = children[2]
                if false_child.kind == CursorKind.CONDITIONAL_OPERATOR:
                    inner_lines.add(false_child.location.line)
                self._collect_ternary_ops(false_child, result, inner_lines)

        else:
            for child in cursor.get_children():
                self._collect_ternary_ops(child, result, inner_lines)

    # ──────────────────────────────────────────────────────────
    # 內部：三元式注入
    # ──────────────────────────────────────────────────────────

    def _inject_ternary(
        self,
        src_bytes: bytearray,
        ternary_cursor,
        dn: DecisionNode,
    ) -> bytearray:
        """將 cond ? true_val : false_val 替換為 _ifl_probe_ternary(...)。

        使用 children 的 byte offset 切片（而非 CONDITIONAL_OPERATOR 自身的 extent，
        後者在 libclang 多行情況下可能不準確）。

        false_val 可能本身是另一個三元式（鏈式），此時遞迴已由 Step 1 處理；
        若 false_val 已被替換（在更高 offset 的那一輪），byte 切片自然取到新文字。
        """
        raw = bytes(src_bytes)
        children = list(ternary_cursor.get_children())
        if len(children) < 3:
            return src_bytes

        c0, c1, c2 = children[0], children[1], children[2]

        # 以 byte offset 切片（正確處理含中文注釋的原始碼）
        cond_text  = raw[c0.extent.start.offset:c0.extent.end.offset].decode('utf-8').strip()
        true_text  = raw[c1.extent.start.offset:c1.extent.end.offset].decode('utf-8').strip()
        false_text = raw[c2.extent.start.offset:c2.extent.end.offset].decode('utf-8').strip()

        # 整體替換範圍：從條件開始到 false_val 結束
        overall_start = c0.extent.start.offset
        overall_end   = c2.extent.end.offset

        nid     = dn.node_id
        cond_id = (dn.condition_set.conditions[0].cond_id
                   if dn.condition_set.conditions else f"{nid}.c1")

        replacement_str = (
            f'_ifl_probe_ternary("{nid}", "{cond_id}", '
            f'({cond_text}), ({true_text}), ({false_text}))'
        )

        # 補齊換行數：確保替換後行數與原始一致，避免後續 IF_STMT 的行號偏移
        original_nl = raw[overall_start:overall_end].count(ord('\n'))
        padding_nl  = original_nl - replacement_str.count('\n')
        if padding_nl > 0:
            replacement_str += '\n' * padding_nl

        return bytearray(raw[:overall_start] + replacement_str.encode('utf-8') + raw[overall_end:])

    # ──────────────────────────────────────────────────────────
    # 內部：單一 IF_STMT 注入
    # ──────────────────────────────────────────────────────────

    def _inject_one(
        self,
        src_bytes: bytearray,
        if_cursor,
        dn: DecisionNode,
    ) -> bytearray:
        """對一個 IF_STMT 進行插樁：插入探針賦值並替換條件。

        支援兩種情況：
          普通 if：在行前插入 probe_code，替換 if (cond) → if (_Dn_decision)
          else if：轉換成 else { probe_code; if (_Dn_decision) { ... } }
                   並在整個 if-else 鏈末尾補一個 '}'
        """
        raw = bytes(src_bytes)
        if_line = if_cursor.location.line
        if_col  = if_cursor.location.column

        line_start = _find_line_start(raw, if_line)
        indent     = _get_indent(raw, line_start)
        if_offset  = _line_col_to_offset(raw, if_line, if_col)
        open_paren, close_paren = _find_condition_parens(raw, if_offset)

        decision_var = f"_{dn.node_id}_decision"

        # ── else if 偵測 ──────────────────────────────────────
        # 若同行（line_start ~ if_offset）包含 'else' 關鍵字，則為 else if 結構
        line_before_if  = raw[line_start:if_offset]
        else_byte_idx   = line_before_if.rfind(b'else')
        is_else_if = (
            else_byte_idx >= 0
            and (else_byte_idx == 0
                 or line_before_if[else_byte_idx - 1:else_byte_idx]
                 in (b' ', b'\t', b'}'))
        )

        if is_else_if:
            # 轉換：} else if (cond) { body … }
            #    →  } else {
            #           probe_code
            #           if (_Dn_decision) { body … }
            #       }
            else_byte_pos = line_start + else_byte_idx
            extra_indent  = indent + "    "
            probe_code    = self._build_probe_code(dn, extra_indent).encode('utf-8')

            # 找到整個 if-else 鏈的最後一個 '}'（在當前 bytes 中計算）
            chain_end = _find_if_else_end(raw, close_paren)

            new_bytes = (
                raw[:else_byte_pos]                         # "    } "
                + b'else {\n'                               # 開新 else block
                + probe_code                                # 探針賦值與記錄
                + extra_indent.encode()                     # if 前縮排
                + b'if ('                                   # "if ("
                + decision_var.encode('ascii')              # "_Dn_decision"
                + raw[close_paren:chain_end + 1]           # ") { ... }" 原始主體
                + b'\n' + indent.encode() + b'}'           # 關閉新 else {
                + raw[chain_end + 1:]                      # 檔案其餘部分
            )
            return bytearray(new_bytes)

        # ── 普通 if：在行前插入 probe_code ────────────────────
        probe_code = self._build_probe_code(dn, indent).encode('utf-8')
        new_src = (
            raw[:line_start]
            + probe_code
            + raw[line_start:open_paren + 1]       # 保留 "    if ("
            + decision_var.encode('ascii')
            + raw[close_paren:]                     # 保留 ") { ..."
        )
        return bytearray(new_src)

    # ──────────────────────────────────────────────────────────
    # 內部：生成探針代碼字串
    # ──────────────────────────────────────────────────────────

    def _build_probe_code(self, dn: DecisionNode, indent: str) -> str:
        """生成插入 if 前的探針代碼區塊。

        格式：
            int _D1_c1 = (expression);
            ...
            _ifl_record_cond("D1.c1", _D1_c1);
            ...
            int _D1_decision = (rebuilt_c_expr);
            _ifl_record_decision("D1", _D1_decision);
        """
        nid = dn.node_id
        cs = dn.condition_set
        lines: list[str] = []

        # 各原子條件求值（繞開短路）
        for cond in cs.conditions:
            part = cond.cond_id.split('.')[1]   # "c1"
            var = f"_{nid}_{part}"              # "_D1_c1"
            lines.append(f"{indent}int {var} = ({cond.expression});")

        # 探針記錄呼叫
        for cond in cs.conditions:
            part = cond.cond_id.split('.')[1]
            var = f"_{nid}_{part}"
            lines.append(f'{indent}_ifl_record_cond("{cond.cond_id}", {var});')

        # 重組決策表達式（用暫存變數取代原子條件）
        decision_c_expr = self._build_decision_expr(dn)
        lines.append(f"{indent}int _{nid}_decision = ({decision_c_expr});")

        # 記錄整體決策結果
        lines.append(f'{indent}_ifl_record_decision("{nid}", _{nid}_decision);')

        return '\n'.join(lines) + '\n'

    def _build_decision_expr(self, dn: DecisionNode) -> str:
        """從 expression_str（Python 語法）重建 C 語法決策表達式。

        步驟：
          1. 依 expression 長度降序替換每個原子條件 → 暫存變數名
             （長的先替換，避免短字串誤替換子串）
          2. 將 Python 布林運算子轉回 C 語法
        """
        expr = dn.expression_str
        nid = dn.node_id
        cs = dn.condition_set

        sorted_conds = sorted(
            cs.conditions,
            key=lambda c: len(c.expression),
            reverse=True,
        )
        for cond in sorted_conds:
            part = cond.cond_id.split('.')[1]
            var = f"_{nid}_{part}"
            expr = re.sub(re.escape(cond.expression), var, expr, count=1)

        return _py_expr_to_c(expr)
