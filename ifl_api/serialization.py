"""將 ifl_mcdc 內部 dataclass 轉為 JSON 安全的 dict。

DecisionNode/ConditionSet/AtomicCondition 是 ifl_mcdc.models.decision_node 的
dataclass，AtomicCondition.ast_node 帶原始 ast.AST 節點，不可直接 JSON 化，
因此不能用 dataclasses.asdict() 一鍵轉換，需手動挑選欄位。
"""
from __future__ import annotations

from ifl_mcdc.models.decision_node import DecisionNode


def serialize_decision_node(dn: DecisionNode) -> dict:
    cs = dn.condition_set
    return {
        "node_id": dn.node_id,
        "node_type": dn.node_type,
        "line_no": dn.line_no,
        "expression_str": dn.expression_str,
        "source_context": dn.source_context,
        "condition_set": {
            "decision_id": cs.decision_id,
            "k": cs.k,
            "coupling_matrix": cs.coupling_matrix,
            "conditions": [
                {
                    "cond_id": c.cond_id,
                    "expression": c.expression,
                    "var_names": c.var_names,
                    "negated": c.negated,
                }
                for c in cs.conditions
            ],
        },
    }
