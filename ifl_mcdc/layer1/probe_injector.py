"""
動態探針注入器：繼承 ast.NodeTransformer，對決策節點進行 AST 重寫。

核心設計：短路求值繞過
  Python 的 or / and 是短路求值。(True or X) 中 X 永遠不會被求值。
  必須在複合表達式組合前先將每個原子條件求值並存入暫存變數，
  否則 MC/DC 矩陣將出現假性空白。

TC-U-09: 短路求值繞過（最關鍵）
TC-U-10: 探針非干擾性——執行結果等價
TC-U-11: 探針執行效能開銷 ≤ 15%
TC-U-12: ProbeLog 執行緒安全
TC-U-13: 巢狀 if 的探針完整覆蓋
"""
from __future__ import annotations

import ast
import re
import threading

from ifl_mcdc.models.decision_node import DecisionNode
from ifl_mcdc.models.probe_record import ProbeLog, ProbeRecord

# ──────────────────────────────────────────
# Part A：全域 probe 函式（注入到被測模組的命名空間）
# ──────────────────────────────────────────

_GLOBAL_LOG: ProbeLog | None = None
_CURRENT_TEST_ID: threading.local = threading.local()


import typing

def _ifl_probe(cond_id: str, eval_func: typing.Callable[[], typing.Any]) -> bool:
    """記錄條件真值。遇到提早求值錯誤時，安全攔截並記錄錯誤訊息供 LLM 參考。"""
    error_for_llm = None
    try:
        raw_value = eval_func()
        value = bool(raw_value)
    except Exception as e:
        # 💥 攔截短路繞過引發的錯誤，預設為 False，但把彈殼（錯誤）留下來
        value = False
        error_for_llm = f"屬性存取錯誤 ({type(e).__name__}): {e}"

    if _GLOBAL_LOG is not None:
        record = ProbeRecord(
            test_id=getattr(_CURRENT_TEST_ID, "value", "UNKNOWN"),
            cond_id=cond_id,
            value=value,
            decision=False,
            error_msg=error_for_llm,  # 🌟 [新增] 將錯誤存入 Log
        )
        _GLOBAL_LOG.append(record)
    return value


def _ifl_probe_ifexp(
    decision_id: str,
    cond_id: str,
    cond_func: typing.Callable[[], typing.Any],
    true_func: typing.Callable[[], typing.Any],
    false_func: typing.Callable[[], typing.Any],
) -> typing.Any:
    """IfExp 三元式專用探針。

    記錄條件值與決策結果，並回傳對應分支的值：
      expr_true if cond else expr_false
    轉換為：
      _ifl_probe_ifexp("Dn", "Dn.c1", lambda: cond, lambda: expr_true, lambda: expr_false)

    注意：true_func / false_func 均以 lambda 延遲求值，避免提早計算副作用。
    """
    cond_val = _ifl_probe(cond_id, cond_func)
    _ifl_record_decision(decision_id, cond_val)
    return true_func() if cond_val else false_func()


def _ifl_record_decision(decision_id: str, result: bool) -> None:
    """回填此決策節點最後 k 筆記錄的 decision 欄位。

    Args:
        decision_id: 決策節點 ID，格式 "D{n}"。
        result: 整個決策表達式的布林結果。
    """
    if _GLOBAL_LOG is None:
        return
    test_id = getattr(_CURRENT_TEST_ID, "value", "UNKNOWN")
    prefix = decision_id + ".c"
    to_update = [
        r
        for r in reversed(_GLOBAL_LOG.records)
        if r.test_id == test_id and r.cond_id.startswith(prefix)
    ]
    for r in to_update:
        r.decision = result


# ──────────────────────────────────────────
# Part B：ProbeInjector 類別
# ──────────────────────────────────────────


class ProbeInjector(ast.NodeTransformer):
    """繼承 ast.NodeTransformer，對決策節點進行 AST 重寫。

    NodeTransformer 的 visit_X 方法回傳的值會替換原本的節點。
    回傳 list[ast.stmt] 時，會展開插入到父節點的 body 中。
    """

    def __init__(self, decision_nodes: list[DecisionNode]) -> None:
        # line_no → DecisionNode（first-wins：同行多節點時以先登記者為準）
        # 鏈式 IfExp（400 if A else 500 if B else ...）各節點同行，first-wins 取最外層。
        self._node_map: dict[int, DecisionNode] = {}
        for dn in decision_nodes:
            if dn.line_no not in self._node_map:
                self._node_map[dn.line_no] = dn
        self._injected_count: int = 0

    def inject(self, source: str) -> str:
        """主入口：回傳注入探針後的 Python 原始碼字串。

        Args:
            source: 原始 Python 原始碼字串。

        Returns:
            注入探針後的 Python 原始碼字串。
        """
        tree = ast.parse(source)
        new_tree = self.visit(tree)
        ast.fix_missing_locations(new_tree)
        return ast.unparse(new_tree)

    def visit_If(self, node: ast.If) -> ast.AST | list[ast.stmt]:
        """重寫 if 語句，注入探針賦值語句。"""
        dn = self._node_map.get(node.lineno)
        if dn is None:
            return self.generic_visit(node)

        assignments = self._build_assignments(dn)
        decision_assign = self._build_decision_assign(dn)
        record_call = self._build_record_call(dn)

        # 把原始 if 的 test 換成暫存變數
        node.test = ast.Name(id=f"_{dn.node_id}_decision", ctx=ast.Load())

        # 遞迴處理 body 內的巢狀 if
        self.generic_visit(node)

        self._injected_count += 1
        # 在 if 語句前插入所有賦值語句
        return [*assignments, decision_assign, record_call, node]

    def visit_While(self, node: ast.While) -> ast.AST | list[ast.stmt]:
        """重寫 while 語句，注入探針賦值語句。"""
        dn = self._node_map.get(node.lineno)
        if dn is None:
            return self.generic_visit(node)

        assignments = self._build_assignments(dn)
        decision_assign = self._build_decision_assign(dn)
        record_call = self._build_record_call(dn)

        node.test = ast.Name(id=f"_{dn.node_id}_decision", ctx=ast.Load())
        self.generic_visit(node)
        self._injected_count += 1
        return [*assignments, decision_assign, record_call, node]

    def visit_Assert(self, node: ast.Assert) -> ast.AST | list[ast.stmt]:
        """重寫 assert 語句，注入探針賦值語句。"""
        dn = self._node_map.get(node.lineno)
        if dn is None:
            return self.generic_visit(node)

        assignments = self._build_assignments(dn)
        decision_assign = self._build_decision_assign(dn)
        record_call = self._build_record_call(dn)

        node.test = ast.Name(id=f"_{dn.node_id}_decision", ctx=ast.Load())
        self.generic_visit(node)
        self._injected_count += 1
        return [*assignments, decision_assign, record_call, node]

    def _build_assignments(self, dn: DecisionNode) -> list[ast.stmt]:
        """生成每個原子條件的探針賦值語句（Lambda 安全封裝版）。

        例：_D1_c1 = _ifl_probe("D1.c1", lambda: age >= 65)
        ⚠️ 使用 lambda 封裝，將求值動作推遲到探針內部，完美繞過短路求值的崩潰風險。
        """
        result: list[ast.stmt] = []
        for cond in dn.condition_set.conditions:
            part = cond.cond_id.split(".")[1]  # "c1"
            var_name = f"_{dn.node_id}_{part}"  # "_D1_c1"
            
            # 【關鍵修改】：將原本的 {cond.expression} 前面加上 lambda: 
            src = f'{var_name} = _ifl_probe("{cond.cond_id}", lambda: {cond.expression})'
            
            stmt = ast.parse(src).body[0]
            result.append(stmt)
        return result

    def _build_decision_assign(self, dn: DecisionNode) -> ast.stmt:
        """生成用暫存變數重組原始表達式的賦值語句。

        例：_D1_decision = (_D1_c1 or _D1_c2) and _D1_c3 and not _D1_c4
        """
        rebuilt_expr = dn.expression_str
        # 替換時從最長表達式開始，避免短字串誤替換
        sorted_conds = sorted(
            dn.condition_set.conditions,
            key=lambda c: len(c.expression),
            reverse=True,
        )
        for cond in sorted_conds:
            part = cond.cond_id.split(".")[1]
            var_name = f"_{dn.node_id}_{part}"
            # 使用 re.escape 逃避特殊字元，並確保只替換一次
            pattern = re.escape(cond.expression)
            rebuilt_expr = re.sub(pattern, var_name, rebuilt_expr, count=1)

        src = f"_{dn.node_id}_decision = {rebuilt_expr}"
        return ast.parse(src).body[0]

    def _build_record_call(self, dn: DecisionNode) -> ast.stmt:
        """生成回填決策結果的函式呼叫語句。

        例：_ifl_record_decision("D1", _D1_decision)
        """
        src = f'_ifl_record_decision("{dn.node_id}", _{dn.node_id}_decision)'
        return ast.parse(src).body[0]

    def visit_IfExp(self, node: ast.IfExp) -> ast.expr:
        """重寫三元表達式（x if cond else y），注入 _ifl_probe_ifexp 呼叫。

        轉換：
          expr_true if cond else expr_false
        →
          _ifl_probe_ifexp("Dn", "Dn.c1", lambda: cond,
                           lambda: expr_true, lambda: expr_false)

        先遞迴處理 body/orelse（處理巢狀 IfExp），再包裝最外層。
        """
        dn = self._node_map.get(node.lineno)
        if dn is None or dn.node_type != "IfExp":
            return self.generic_visit(node)

        # 先遞迴處理 body 與 orelse（內層 IfExp 優先轉換）
        node.body  = self.visit(node.body)
        node.orelse = self.visit(node.orelse)
        # 不遞迴 test，由下方直接替換

        self._injected_count += 1
        nid = dn.node_id
        cs  = dn.condition_set

        if len(cs.conditions) == 1:
            cond = cs.conditions[0]
            src = (
                f'_ifl_probe_ifexp('
                f'"{nid}", "{cond.cond_id}", '
                f'lambda: {cond.expression}, '
                f'lambda: ({ast.unparse(node.body)}), '
                f'lambda: ({ast.unparse(node.orelse)}))'
            )
        else:
            # 多條件（BoolOp 作為 IfExp test）：逐一包裝後重組
            probe_parts = " and ".join(
                f'_ifl_probe("{c.cond_id}", lambda: {c.expression})'
                for c in cs.conditions
            )
            rebuilt = dn.expression_str
            src = (
                f'_ifl_probe_ifexp('
                f'"{nid}", "{cs.conditions[0].cond_id}", '
                f'lambda: ({rebuilt}), '
                f'lambda: ({ast.unparse(node.body)}), '
                f'lambda: ({ast.unparse(node.orelse)}))'
            )

        return ast.parse(src, mode='eval').body
