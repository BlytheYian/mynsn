"""
布林導數引擎：使用 Z3 精確計算 ∂f/∂xᵢ，偵測遮罩效應。

數學定義：
  ∂f/∂xᵢ = f(..., xᵢ=True, ...) XOR f(..., xᵢ=False, ...)
  若 ∂f/∂xᵢ = 0（恆為 False），則 xᵢ 被遮罩。

TC-U-19: AND 算子下的遮罩效應偵測（A and False → A 被遮罩）
TC-U-20: OR 算子下的遮罩效應偵測（A or True → A 被遮罩）
TC-U-21: 非遮罩條件的正確識別
TC-U-22: 遮罩成因正確歸因
TC-U-23: 布林導數計算精確性（無近似）
"""
from __future__ import annotations

from z3 import BoolVal, Solver, Xor, sat, substitute

from ifl_mcdc.layer2.smt_synthesizer import ASTToZ3Converter
from ifl_mcdc.models.decision_node import AtomicCondition, DecisionNode
from ifl_mcdc.models.smt_models import MaskingReport


class BooleanDerivativeEngine:
    """使用 Z3 精確計算布林導數。不使用採樣或近似——結果必須是數學精確的。"""

    def compute(
        self,
        decision_node: DecisionNode,
        target_cond: AtomicCondition,
    ) -> MaskingReport:
        """計算 ∂f/∂target_cond 是否為 0。

        演算法：
          1. 建立所有條件的 Z3 Bool 符號變數（以 cond_id 命名）
          2. 用 ASTToZ3Converter 建立 f_expr
          3. f_T = substitute(f_expr, target_var, BoolVal(True))
          4. f_F = substitute(f_expr, target_var, BoolVal(False))
          5. Solver.add(Xor(f_T, f_F))
          6. SAT → is_masked=False；UNSAT → is_masked=True

        Args:
            decision_node: 決策節點。
            target_cond: 要計算導數的目標條件。

        Returns:
            MaskingReport 含遮罩狀態及成因。
        """
        # 建立 Z3 符號變數（以 cond_id 為名，避免名稱衝突）
        z3_vars: dict[str, object] = {
            c.cond_id: ASTToZ3Converter._make_bool(c.cond_id)
            for c in decision_node.condition_set.conditions
        }

        # 建立以 cond_id 為變數的 Z3 表達式
        f_expr = self._build_z3_expr(decision_node, z3_vars)
        target_var = z3_vars[target_cond.cond_id]

        # f_T = f(..., target=True, ...)
        f_t = substitute(f_expr, (target_var, BoolVal(True)))
        # f_F = f(..., target=False, ...)
        f_f = substitute(f_expr, (target_var, BoolVal(False)))

        # 布林導數 = f_T XOR f_F
        # 若 ∃ 某個賦值使得 f_T XOR f_F = True → 導數非恆為 0 → 不被遮罩
        s = Solver()
        s.add(Xor(f_t, f_f))
        result = s.check()

        if result == sat:
            return MaskingReport(
                condition_id=target_cond.cond_id,
                is_masked=False,
                masking_cause=[],
                derivative_value=1,
            )
        else:
            # 導數恆為 0，target 永遠被遮罩
            masking = self._find_masking_cause(
                decision_node, target_cond, z3_vars, f_t, f_f
            )
            return MaskingReport(
                condition_id=target_cond.cond_id,
                is_masked=True,
                masking_cause=masking,
                derivative_value=0,
            )

    def _find_masking_cause(
        self,
        dn: DecisionNode,
        target: AtomicCondition,
        z3_vars: dict[str, object],
        f_t: object,
        f_f: object,
    ) -> list[str]:
        """暴力搜尋：逐一嘗試固定每個耦合條件，看哪個固定後使導數可以為 1。

        OR 夥伴 → 固定為 False（消除 OR 遮罩）
        AND 夥伴 → 固定為 True（解除 AND 短路）
        若固定後 SAT → 此夥伴為遮罩成因。
        """
        coupled = dn.condition_set.get_coupled(target.cond_id)
        causes: list[str] = []

        for other_cond, coupling_type in coupled:
            # 若耦合夥伴是字面常量（"True"/"False"），直接判斷是否為遮罩成因
            if other_cond.expression in ("True", "False"):
                const_val = other_cond.expression == "True"
                if (coupling_type == "OR" and const_val) or (
                    coupling_type == "AND" and not const_val
                ):
                    causes.append(other_cond.cond_id)
                continue

            fix_value = BoolVal(False) if coupling_type == "OR" else BoolVal(True)
            other_var = z3_vars[other_cond.cond_id]

            f_t_fixed = substitute(f_t, (other_var, fix_value))
            f_f_fixed = substitute(f_f, (other_var, fix_value))

            s2 = Solver()
            s2.add(Xor(f_t_fixed, f_f_fixed))
            if s2.check() == sat:
                causes.append(other_cond.cond_id)

        return causes

    def _build_z3_expr(
        self, dn: DecisionNode, z3_vars: dict[str, object]
    ) -> object:
        """用 ASTToZ3Converter 從 DecisionNode 的表達式建構 Z3 公式。

        注意：此處使用 cond_id 作為變數名，需要一個特殊的 converter，
        把每個原子條件的 expression 替換為其 cond_id 變數。
        """
        return _CondIdConverter(z3_vars, dn).convert_expr(dn.expression_str)


class _CondIdConverter:
    """將決策表達式轉為以 cond_id 為葉節點的 Z3 公式。

    掃描決策表達式 AST，遇到葉節點時按 expression 字串反查對應的 cond_id 變數。
    """

    def __init__(
        self,
        z3_vars: dict[str, object],
        dn: DecisionNode,
    ) -> None:
        # expression → cond_id 的映射
        self._expr_to_var: dict[str, object] = {}
        for cond in dn.condition_set.conditions:
            # 可能有多個條件有相同表達式（罕見），後者覆蓋前者
            self._expr_to_var[cond.expression] = z3_vars[cond.cond_id]
        self._z3_vars = z3_vars

    def convert_expr(self, expr_str: str) -> object:
        """轉換表達式字串為 Z3 公式。"""
        import ast as _ast

        tree = _ast.parse(expr_str, mode="eval")
        return self._visit(tree.body)

    def _visit(self, node: object) -> object:
        import ast as _ast

        assert isinstance(node, _ast.expr)

        if isinstance(node, _ast.BoolOp):
            operands = [self._visit(v) for v in node.values]
            import z3
            if isinstance(node.op, _ast.And):
                return z3.And(*operands)
            else:
                return z3.Or(*operands)

        elif isinstance(node, _ast.UnaryOp) and isinstance(node.op, _ast.Not):
            import z3
            return z3.Not(self._visit(node.operand))

        else:
            # Constant True/False/int 必須先處理，避免被 _expr_to_var 截走
            if isinstance(node, _ast.Constant):
                from z3 import BoolVal, IntVal
                if isinstance(node.value, bool):
                    return BoolVal(node.value)
                if isinstance(node.value, int):
                    return IntVal(node.value)
            # 葉節點：按 ast.unparse 查找對應 cond_id 變數
            import ast as _ast2
            node_str = _ast2.unparse(node)
            if node_str in self._expr_to_var:
                return self._expr_to_var[node_str]
            raise KeyError(f"Leaf expression not found in condition map: {node_str!r}")


