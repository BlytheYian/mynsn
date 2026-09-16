"""
SMT 約束合成器：將 GapEntry 轉化為 Z3 可求解的 SMT 公式。

包含兩個類別：
  ASTToZ3Converter     — 將 Python AST 節點轉換為 Z3 表達式
  SMTConstraintSynthesizer — 建構 Φ_gap 並求解，輸出 SMTResult

TC-U-28: 疫苗邏輯 c2 F2T 缺口 SAT 求解
TC-U-29: 互斥條件 UNSAT 識別
TC-U-30: 10 秒超時保護
TC-U-32: ASTToZ3Converter——所有算子類型
TC-U-33: 多重比較連鎖（a < b < c）
"""
from __future__ import annotations

import ast
import random as _random
import time

import z3
from z3 import Bool, BoolVal, Int, IntVal, Real, RealVal, Solver, sat

from ifl_mcdc.exceptions import Z3TimeoutError
from ifl_mcdc.models.coverage_matrix import GapEntry
from ifl_mcdc.models.decision_node import AtomicCondition, DecisionNode
from ifl_mcdc.models.smt_models import BoundSpec, SMTResult


class ASTToZ3Converter:
    """將 Python AST 節點轉換為 Z3 表達式。

    支援：BoolOp(and/or)、UnaryOp(not)、Compare(>,<,>=,<=,==,!=)、
          Name、Constant（bool/int/float）。
    """

    def __init__(self, z3_vars: dict[str, object]) -> None:
        self.z3_vars = z3_vars

    @staticmethod
    def _make_bool(name: str) -> object:
        """建立具名 Z3 Bool 符號變數。"""
        return Bool(name)

    def convert(self, decision_node: DecisionNode) -> object:
        """轉換整個 DecisionNode 的布林表達式為 Z3 公式。"""
        tree = ast.parse(decision_node.expression_str, mode="eval")
        return self._visit(tree.body)

    def convert_cond(self, cond: AtomicCondition) -> object:
        """轉換單一原子條件的表達式為 Z3 公式（含 negated 旗標）。"""
        tree = ast.parse(cond.expression, mode="eval")
        expr = self._visit(tree.body)
        if cond.negated:
            return z3.Not(expr)
        return expr

    def _visit(self, node: ast.expr) -> object:
        """遞迴走訪 AST 節點，轉換為對應 Z3 表達式。"""
        if isinstance(node, ast.BoolOp):
            operands = [self._visit(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return z3.And(*operands)
            else:
                return z3.Or(*operands)

        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return z3.Not(self._visit(node.operand))

        elif isinstance(node, ast.Compare):
            left = self._visit(node.left)
            parts: list[object] = []
            prev = left
            for op, comp in zip(node.ops, node.comparators):
                right = self._visit(comp)
                if isinstance(op, ast.Gt):
                    parts.append(prev > right)   # type: ignore[operator]
                elif isinstance(op, ast.Lt):
                    parts.append(prev < right)   # type: ignore[operator]
                elif isinstance(op, ast.GtE):
                    parts.append(prev >= right)  # type: ignore[operator]
                elif isinstance(op, ast.LtE):
                    parts.append(prev <= right)  # type: ignore[operator]
                elif isinstance(op, ast.Eq):
                    parts.append(prev == right)
                elif isinstance(op, ast.NotEq):
                    parts.append(prev != right)
                else:
                    raise NotImplementedError(
                        f"Unsupported comparison operator: {type(op).__name__}"
                    )
                prev = right
            return z3.And(*parts) if len(parts) > 1 else parts[0]

        elif isinstance(node, ast.Name):
            if node.id in self.z3_vars:
                return self.z3_vars[node.id]
            if node.id in ("True", "true"):
                return BoolVal(True)
            if node.id in ("False", "false"):
                return BoolVal(False)
            raise KeyError(f"Unknown variable: {node.id!r}")

        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return BoolVal(node.value)
            if isinstance(node.value, int):
                return IntVal(node.value)
            if isinstance(node.value, float):
                return RealVal(node.value)
            raise TypeError(
                f"Unsupported constant type: {type(node.value).__name__}"
            )

        elif isinstance(node, ast.BinOp):
            left = self._visit(node.left)
            right = self._visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            elif isinstance(node.op, ast.Sub):
                return left - right
            elif isinstance(node.op, ast.Mult):
                return left * right
            elif isinstance(node.op, ast.FloorDiv):
                return left / right
            elif isinstance(node.op, ast.Mod):
                return left % right
            else:
                raise NotImplementedError(
                    f"Unsupported binary operator: {type(node.op).__name__}"
                )

        elif isinstance(node, ast.IfExp):
            return z3.If(
                self._visit(node.test),
                self._visit(node.body),
                self._visit(node.orelse),
            )

        else:
            raise NotImplementedError(
                f"Unsupported AST node: {type(node).__name__}"
            )


class SMTConstraintSynthesizer:
    """將缺口轉化為 SMT 公式並求解，輸出 SMTResult。

    SAT 時：提供可行輸入向量（model）。
    UNSAT 時：提供不可滿足性核心（core）。
    超時時：拋出 Z3TimeoutError。
    """

    TIMEOUT_MS: int = 10_000  # 10 秒

    def __init__(
        self,
        domain_bounds: dict[str, list[int]] | None = None,
    ) -> None:
        self.domain_bounds = domain_bounds

    def _bool_fixed_value(self, var_name: str) -> bool | None:
        """domain_bounds 若把 var_name 限定為 [1,1]/[0,0]，回傳該固定布林值；否則 None（不受限）。"""
        bounds = (self.domain_bounds or {}).get(var_name)
        if bounds == [1, 1]:
            return True
        if bounds == [0, 0]:
            return False
        return None

    def synthesize(
        self,
        decision_node: DecisionNode,
        gap: GapEntry,
        domain_types: dict[str, str],
        preceding_nodes: list[DecisionNode] | None = None,
        excluded_solutions: list[dict[str, object]] | None = None,
        positive_constraints: list[DecisionNode] | None = None,
        negative_constraints: list[DecisionNode] | None = None,
        fixed_values: dict[str, object] | None = None,
    ) -> SMTResult:
        """主方法：合成 Φ_gap 並求解。

        Args:
            decision_node: 決策節點。
            gap: 待填補的缺口（condition_id + flip_direction）。
            domain_types: var_name → "int" | "bool" | "float" 的映射。
            positive_constraints: 必須為 True 的前置節點（巢狀 if 外層 gate）。
            fixed_values: 將指定變數釘死為已知值（var_name → value）後再求解，
                用於「欄位已經逐一定案，重新推導其餘欄位在此條件下的真實合法區間」
                （條件式 bound_specs），而不是沿用跟已定案欄位無關的邊際區間。

        Returns:
            SMTResult：SAT 含 model，UNSAT 含 core。

        Raises:
            Z3TimeoutError: 求解超過 TIMEOUT_MS。
        """
        t0 = time.time()

        # 步驟 1：建立 Z3 變數（含前置節點的變數，供路徑約束使用）
        z3_vars = self._create_z3_vars(decision_node, domain_types)
        for pn in (preceding_nodes or []):
            z3_vars.update(self._create_z3_vars(pn, domain_types))
        for pn in (positive_constraints or []):
            z3_vars.update(self._create_z3_vars(pn, domain_types))
        for pn in (negative_constraints or []):
            z3_vars.update(self._create_z3_vars(pn, domain_types))

        # 步驟 2：將 DecisionNode 轉為 Z3 公式
        f_expr = ASTToZ3Converter(z3_vars).convert(decision_node)

        # 步驟 3：建構 Φ_gap
        phi_gap = self._build_phi_gap(decision_node, gap, z3_vars, f_expr, domain_types)

        # 步驟 3a：路徑約束——所有前置決策節點的表達式必須為 False
        for pn in (preceding_nodes or []):
            pn_expr = ASTToZ3Converter(z3_vars).convert(pn)
            phi_gap.append(pn_expr == BoolVal(False))

        # 步驟 3a2：正向路徑約束——巢狀 if 的外層 gate 必須為 True
        for pn in (positive_constraints or []):
            pn_expr = ASTToZ3Converter(z3_vars).convert(pn)
            phi_gap.append(pn_expr == BoolVal(True))

        # 步驟 3a3：負向路徑約束——巢狀 if 的 else-branch gate 必須為 False
        for pn in (negative_constraints or []):
            pn_expr = ASTToZ3Converter(z3_vars).convert(pn)
            phi_gap.append(pn_expr == BoolVal(False))

        # 步驟 3a4：釘死已定案欄位的值，讓其餘變數的 bound_specs 是「條件式」的
        # 真實合法區間（而非跟已定案欄位無關的邊際投影）。用於逐一修補多個彼此
        # 數值耦合欄位時，後一個欄位的區間需要建立在前一個欄位已固定的基礎上。
        for var_name, value in (fixed_values or {}).items():
            z3_var = z3_vars.get(var_name)
            if z3_var is None:
                continue
            vtype = domain_types.get(var_name, "int")
            if vtype == "bool":
                phi_gap.append(z3_var == BoolVal(bool(value)))
            else:
                try:
                    phi_gap.append(z3_var == IntVal(int(value)))  # type: ignore[operator]
                except (ValueError, TypeError):
                    continue

        # 步驟 3b：排除前次解的鄰域，強迫 Z3 探索不同區域（多樣性）
        # 對每個前次解，加入：NOT(所有 int 變數在鄰域內 AND 所有 bool 變數相同)
        # 鄰域寬度 = max(1, span // 10)，約為域寬的 10%
        for prev in (excluded_solutions or []):
            exclusion_clauses: list[object] = []
            for var_name, z3_var in z3_vars.items():
                prev_val = prev.get(var_name)
                if prev_val is None:
                    continue
                vtype = domain_types.get(var_name, "int")
                if vtype == "bool":
                    exclusion_clauses.append(z3_var == BoolVal(bool(prev_val)))
                else:
                    try:
                        pv = int(str(prev_val))
                    except (ValueError, TypeError):
                        continue
                    if self.domain_bounds and var_name in self.domain_bounds:
                        span = self.domain_bounds[var_name][1] - self.domain_bounds[var_name][0]
                        radius = max(1, span // 10)
                    else:
                        radius = 50
                    exclusion_clauses.append(z3_var >= pv - radius)  # type: ignore[operator]
                    exclusion_clauses.append(z3_var <= pv + radius)  # type: ignore[operator]
            if exclusion_clauses:
                phi_gap.append(z3.Not(z3.And(*exclusion_clauses)))

        # 步驟 4：求解
        s = Solver()
        s.set("timeout", self.TIMEOUT_MS)
        s.add(phi_gap)

        try:
            result = s.check()
        except z3.Z3Exception as exc:
            raise Z3TimeoutError(
                f"Z3 求解超時（>{self.TIMEOUT_MS}ms）：{exc}"
            ) from exc

        solve_time = time.time() - t0

        # 超時：z3 回傳 unknown
        if result == z3.unknown:
            raise Z3TimeoutError(
                f"Z3 求解超時（>{self.TIMEOUT_MS}ms），結果 unknown"
            )

        if result == sat:
            model = s.model()
            from ifl_mcdc.layer2.bound_extractor import BoundExtractor
            # 傳入 phi_gap，讓 BoundExtractor 用 Optimize 求每個 int 變數的真實上下界
            bound_specs = BoundExtractor().extract(
                model, z3_vars, domain_types, self.domain_bounds, phi=phi_gap
            )
            concrete: dict[str, object] = {
                var_name: model.eval(z3_var, model_completion=True)
                for var_name, z3_var in z3_vars.items()
            }
            # Python-native 具體解：供 smt_example 參考範例使用
            concrete_python: dict[str, object] = {
                var_name: self._z3_val_to_python(val, domain_types.get(var_name, "int"))
                for var_name, val in concrete.items()
            }

            # 額外計算 False 側（補集）BoundSpec：目標條件=False、決策=False
            complement_bound_specs = None
            comp_phi = self._build_complement_phi(decision_node, gap, z3_vars, f_expr, domain_types)
            s_comp = Solver()
            s_comp.set("timeout", self.TIMEOUT_MS)
            s_comp.add(comp_phi)
            try:
                if s_comp.check() == sat:
                    complement_bound_specs = BoundExtractor().extract(
                        s_comp.model(), z3_vars, domain_types, self.domain_bounds, phi=comp_phi
                    )
            except z3.Z3Exception:
                pass

            return SMTResult(
                satisfiable=True,
                model=concrete,
                model_python=concrete_python,
                bound_specs=bound_specs,
                complement_bound_specs=complement_bound_specs,
                solve_time=solve_time,
            )
        else:
            # UNSAT：取 unsat core
            s2 = Solver()
            s2.set("unsat_core", True)
            tracked: list[str] = []
            for i, clause in enumerate(phi_gap):
                p = Bool(f"_p{i}")
                s2.assert_and_track(clause, p)
                tracked.append(str(p))
            s2.check()
            core = [str(c) for c in s2.unsat_core()]
            return SMTResult(satisfiable=False, core=core, solve_time=solve_time)

    def _create_z3_vars(
        self,
        dn: DecisionNode,
        domain_types: dict[str, str],
    ) -> dict[str, object]:
        """建立所有出現在條件中的 Z3 變數。"""
        z3_vars: dict[str, object] = {}
        for cond in dn.condition_set.conditions:
            for var_name in cond.var_names:
                if var_name in z3_vars:
                    continue
                t = domain_types.get(var_name, "int")
                if t == "bool":
                    z3_vars[var_name] = Bool(var_name)
                elif t == "float":
                    z3_vars[var_name] = Real(var_name)
                else:
                    z3_vars[var_name] = Int(var_name)
        return z3_vars

    def _build_phi_gap(
        self,
        dn: DecisionNode,
        gap: GapEntry,
        z3_vars: dict[str, object],
        f_expr: object,
        domain_types: dict[str, str],
    ) -> list[object]:
        phi: list[object] = []
        converter = ASTToZ3Converter(z3_vars)

        target_cond = next(
            c for c in dn.condition_set.conditions
            if c.cond_id == gap.condition_id
        )
        target_var_names: set[str] = set(target_cond.var_names)

        # (A) 決策結果為 True（保證整體判定可翻轉，適用任何布林邏輯）
        phi.append(f_expr == BoolVal(True))

        # (B) 目標條件為 True
        target_z3 = converter.convert_cond(target_cond)
        phi.append(target_z3 == BoolVal(True))

        # (C) int 變數域約束：優先使用 self.domain_bounds，否則僅約束 >= 0
        #     bool 變數若 domain_bounds 限定為 [1,1]/[0,0]，一併固定其值
        for var_name, z3_var in z3_vars.items():
            vtype = domain_types.get(var_name, "int")
            if vtype == "int":
                if self.domain_bounds and var_name in self.domain_bounds:
                    lo, hi = self.domain_bounds[var_name][0], self.domain_bounds[var_name][1]
                    phi.append(z3_var >= lo)  # type: ignore[operator]
                    phi.append(z3_var <= hi)  # type: ignore[operator]
                else:
                    phi.append(z3_var >= 0)  # type: ignore[operator]
            elif vtype == "bool":
                fixed = self._bool_fixed_value(var_name)
                if fixed is not None:
                    phi.append(z3_var == BoolVal(fixed))  # type: ignore[operator]

        # (D) 配對可行性約束：確保 True 側的非目標條件值允許有效 False 側存在
        #
        # 為目標條件的變數建立「補集副本」(_c_<var>)，非目標變數共用原始 Z3 變數。
        # 補集副本須滿足：target_cond=False、所有非目標條件值不變、decision=False。
        # 這樣 Z3 在選 True 側值時就能保證有可配對的 False 側，
        # 避免 LLM 選到使補集不可行的值（如 age>=65 缺口時 high_risk=True）。
        comp_vars: dict[str, object] = {}
        for var_name in target_var_names:
            t = domain_types.get(var_name, "int")
            if t == "bool":
                comp_vars[var_name] = Bool(f"_c_{var_name}")
            elif t == "float":
                comp_vars[var_name] = Real(f"_c_{var_name}")
            else:
                comp_vars[var_name] = Int(f"_c_{var_name}")

        # 混合變數映射：目標變數用補集副本，非目標變數共用原始
        mixed_vars = {**z3_vars, **comp_vars}
        mixed_converter = ASTToZ3Converter(mixed_vars)
        f_expr_comp = mixed_converter.convert(dn)

        # 補集副本的目標條件 = False
        target_z3_comp = mixed_converter.convert_cond(target_cond)
        phi.append(z3.Not(target_z3_comp))

        # 各非目標條件的值在補集側必須與 True 側相同（用混合變數表達）
        for cond in dn.condition_set.conditions:
            if cond.cond_id == gap.condition_id:
                continue
            # 若此非目標條件使用了目標變數，需在補集側施加相同約束
            if not any(v in target_var_names for v in cond.var_names):
                continue
            # 該條件使用目標變數（comp var），必須等於原始 True 側條件值
            # 在 True 側 phi 中，原始 z3_vars 版的此條件已被約束
            # 在補集側，用混合變數版，讓 Z3 找到同樣滿足的解
            cond_z3_orig = converter.convert_cond(cond)
            cond_z3_comp = mixed_converter.convert_cond(cond)
            phi.append(cond_z3_comp == cond_z3_orig)

        # 補集決策 = False
        phi.append(z3.Not(f_expr_comp))

        # 補集副本域約束
        for var_name, z3_var in comp_vars.items():
            vtype = domain_types.get(var_name, "int")
            if vtype == "int":
                if self.domain_bounds and var_name in self.domain_bounds:
                    lo, hi = self.domain_bounds[var_name][0], self.domain_bounds[var_name][1]
                    phi.append(z3_var >= lo)  # type: ignore[operator]
                    phi.append(z3_var <= hi)  # type: ignore[operator]
                else:
                    phi.append(z3_var >= 0)  # type: ignore[operator]
            elif vtype == "bool":
                fixed = self._bool_fixed_value(var_name)
                if fixed is not None:
                    phi.append(z3_var == BoolVal(fixed))  # type: ignore[operator]

        return phi

    def synthesize_complement(
        self,
        decision_node: DecisionNode,
        gap: GapEntry,
        domain_types: dict[str, str],
        true_side_concrete: dict[str, object],
        preceding_nodes: list[DecisionNode] | None = None,
    ) -> dict[str, object] | None:
        """True 側執行後，合成保證有效的 MC/DC False 側測試。

        做法：固定所有「非目標條件的變數」到 True 側的具體值，
        讓 Z3 只需為目標條件的變數找滿足 target=False & decision=False 的值。

        由於非目標條件的所有變數值完全相同，_others_ok 一定成立，
        (True 側, False 側) 必然構成有效 MC/DC 配對。

        Args:
            decision_node: 決策節點。
            gap: 待填補的缺口。
            domain_types: var_name → 型別。
            true_side_concrete: True 側測試的具體輸入值 {var_name → Python value}。

        Returns:
            Python-native 型別的測試字典，UNSAT 或超時時回傳 None。
        """
        z3_vars = self._create_z3_vars(decision_node, domain_types)
        for pn in (preceding_nodes or []):
            z3_vars.update(self._create_z3_vars(pn, domain_types))
        converter = ASTToZ3Converter(z3_vars)
        f_expr = converter.convert(decision_node)

        target_cond = next(
            c for c in decision_node.condition_set.conditions
            if c.cond_id == gap.condition_id
        )
        target_var_names: set[str] = set(target_cond.var_names)

        phi: list[object] = []

        # (A) 決策結果 = False
        phi.append(z3.Not(f_expr))

        # (B) 目標條件 = False
        target_z3 = converter.convert_cond(target_cond)
        phi.append(z3.Not(target_z3))

        # (C) 固定非目標條件的變數為 True 側具體值（確保非目標條件值不變）
        for var_name, z3_var in z3_vars.items():
            if var_name in target_var_names:
                continue
            true_val = true_side_concrete.get(var_name)
            if true_val is None:
                continue
            var_type = domain_types.get(var_name, "int")
            if var_type == "bool":
                phi.append(z3_var == BoolVal(bool(true_val)))
            else:
                try:
                    phi.append(z3_var == int(str(true_val)))
                except ValueError:
                    phi.append(z3_var == int(float(str(true_val))))

        # (C2) 對使用目標變數的非目標條件，施加其 True 側條件值不變的約束
        # （由於目標變數在補集中為自由變數，共用目標變數的非目標條件可能改變值）
        for cond in decision_node.condition_set.conditions:
            if cond.cond_id == gap.condition_id:
                continue  # 目標條件跳過（允許改變）
            if not any(v in target_var_names for v in cond.var_names):
                continue  # 此條件不使用目標變數，已由 (C) 處理
            # 計算此條件在 True 側的值
            try:
                raw_val = bool(eval(  # noqa: S307
                    cond.expression,
                    {"__builtins__": {}},
                    dict(true_side_concrete),
                ))
                cond_true_val = (not raw_val) if cond.negated else raw_val
            except Exception:  # noqa: BLE001
                continue
            # 在補集中，此條件的 Z3 表達式（使用自由目標變數）必須等於 True 側值
            cond_z3 = converter.convert_cond(cond)
            phi.append(cond_z3 == BoolVal(cond_true_val))

        # (D) 目標條件變數的域約束
        for var_name, z3_var in z3_vars.items():
            if var_name not in target_var_names:
                continue
            if domain_types.get(var_name, "int") == "int":
                if self.domain_bounds and var_name in self.domain_bounds:
                    lo, hi = self.domain_bounds[var_name][0], self.domain_bounds[var_name][1]
                    phi.append(z3_var >= lo)  # type: ignore[operator]
                    phi.append(z3_var <= hi)  # type: ignore[operator]
                else:
                    phi.append(z3_var >= 0)  # type: ignore[operator]

        # (E) 隨機偏移：為目標變數加入隨機下界，避免 Z3 跨 run 永遠回傳最小解
        phi_with_offset = list(phi)
        has_offset = False
        for var_name, z3_var in z3_vars.items():
            if var_name not in target_var_names:
                continue
            if domain_types.get(var_name, "int") != "int":
                continue
            if not (self.domain_bounds and var_name in self.domain_bounds):
                continue
            lo_db, hi_db = self.domain_bounds[var_name][0], self.domain_bounds[var_name][1]
            span = hi_db - lo_db
            if span >= 4:
                offset = _random.randint(0, max(0, span // 4))
                if offset > 0:
                    phi_with_offset.append(z3_var >= lo_db + offset)  # type: ignore[operator]
                    has_offset = True

        s = Solver()
        s.set("timeout", self.TIMEOUT_MS)
        s.add(phi_with_offset)

        try:
            check_result = s.check()
            if check_result != sat and has_offset:
                # 偏移使求解失敗，回退到無偏移版本
                s = Solver()
                s.set("timeout", self.TIMEOUT_MS)
                s.add(phi)
                check_result = s.check()
            if check_result != sat:
                return None
            comp_model = s.model()
            result: dict[str, object] = {}
            for var_name, z3_var in z3_vars.items():
                if var_name not in target_var_names:
                    # 直接用 True 側值，保證完全相同
                    result[var_name] = true_side_concrete.get(var_name, 0)
                else:
                    val = comp_model.eval(z3_var, model_completion=True)
                    result[var_name] = self._z3_val_to_python(
                        val, domain_types.get(var_name, "int")
                    )
            # 補充 true_side_concrete 中不在此決策節點條件集的其餘函數參數。
            # 多參數函數（如 TCAS）若補集測試缺少必要參數，Python 在函數呼叫層即拋出
            # TypeError，導致探針完全未執行，MC/DC 配對永遠無法成立。
            for var_name, val in true_side_concrete.items():
                if var_name not in result:
                    result[var_name] = val
            return result
        except z3.Z3Exception:
            return None

    @staticmethod
    def _z3_val_to_python(val: object, var_type: str) -> object:
        """將 Z3 模型值轉換為 Python 原生型別。"""
        if var_type == "bool":
            return bool(z3.is_true(val))
        elif var_type == "int":
            try:
                return int(str(val))
            except (ValueError, TypeError):
                return 0
        else:  # float
            try:
                return float(z3.RealVal(str(val)).as_decimal(10).rstrip("?"))
            except Exception:
                return 0.0

    def _build_complement_phi(
        self,
        dn: DecisionNode,
        gap: GapEntry,
        z3_vars: dict[str, object],
        f_expr: object,
        domain_types: dict[str, str],
    ) -> list[object]:
        """建構 False 側（補集）的 SMT 公式 Φ_complement（供 synthesize 內部使用）。

        (A) 決策結果 = False
        (B) 目標條件 = False
        (C) int 變數域約束
        """
        phi: list[object] = []
        converter = ASTToZ3Converter(z3_vars)

        target_cond = next(
            c for c in dn.condition_set.conditions
            if c.cond_id == gap.condition_id
        )

        # (A) 決策結果為 False
        phi.append(f_expr == BoolVal(False))

        # (B) 目標條件為 False
        target_z3 = converter.convert_cond(target_cond)
        phi.append(target_z3 == BoolVal(False))

        # (C) int 變數域約束
        for var_name, z3_var in z3_vars.items():
            if domain_types.get(var_name, "int") == "int":
                if self.domain_bounds and var_name in self.domain_bounds:
                    lo, hi = self.domain_bounds[var_name][0], self.domain_bounds[var_name][1]
                    phi.append(z3_var >= lo)  # type: ignore[operator]
                    phi.append(z3_var <= hi)  # type: ignore[operator]
                else:
                    phi.append(z3_var >= 0)  # type: ignore[operator]

        return phi

    def synthesize_with_preferences(
        self,
        decision_node: DecisionNode,
        gap: GapEntry,
        domain_types: dict[str, str],
        bool_values: dict[str, object],
        int_preferences: dict[str, object],
        preceding_nodes: list[DecisionNode] | None = None,
    ) -> dict[str, object]:
        """以 LLM 的語意偏好為軟約束，在 Φ_gap 可行空間內找具體解。

        流程（三層 fallback，確保一定能找到解）：
          1. Φ_gap + bool 偏好 + int 子區間偏好 → 最完整的偏好
          2. Φ_gap + int 子區間偏好（放棄 bool 偏好）
          3. Φ_gap 原始約束（退回 synthesize 的行為）

        int 偏好映射（基於 domain_bounds）：
          "low"    → [lo,              lo + span//3]
          "medium" → [lo + span//3,    lo + 2*span//3]
          "high"   → [lo + 2*span//3,  hi]

        Args:
            decision_node: 目標決策節點。
            gap: 目標缺口。
            domain_types: var_name → 型別。
            bool_values: LLM 給出的布林偏好 {var_name: True/False}。
            int_preferences: LLM 給出的整數偏好 {var_name: "low"/"medium"/"high"}。
            preceding_nodes: 路徑前置節點（確保函數可達性）。

        Returns:
            Python-native 型別的具體測試案例 dict。
        """
        z3_vars = self._create_z3_vars(decision_node, domain_types)
        for pn in (preceding_nodes or []):
            z3_vars.update(self._create_z3_vars(pn, domain_types))

        converter = ASTToZ3Converter(z3_vars)
        f_expr = converter.convert(decision_node)

        phi_core = self._build_phi_gap(decision_node, gap, z3_vars, f_expr, domain_types)
        for pn in (preceding_nodes or []):
            pn_expr = ASTToZ3Converter(z3_vars).convert(pn)
            phi_core.append(pn_expr == BoolVal(False))

        def _int_pref_constraints() -> list[object]:
            constraints: list[object] = []
            for var_name, pref in int_preferences.items():
                if domain_types.get(var_name, "int") != "int":
                    continue
                z3_var = z3_vars.get(var_name)
                if z3_var is None:
                    continue
                if not (self.domain_bounds and var_name in self.domain_bounds):
                    continue
                lo, hi = self.domain_bounds[var_name][0], self.domain_bounds[var_name][1]
                span = hi - lo
                if span < 3:
                    continue
                third = span // 3
                if pref == "low":
                    sub_lo, sub_hi = lo, lo + third
                elif pref == "high":
                    sub_lo, sub_hi = lo + 2 * third, hi
                else:
                    sub_lo, sub_hi = lo + third, lo + 2 * third
                constraints.append(z3_var >= sub_lo)  # type: ignore[operator]
                constraints.append(z3_var <= sub_hi)  # type: ignore[operator]
            return constraints

        def _bool_pref_constraints() -> list[object]:
            constraints: list[object] = []
            for var_name, val in bool_values.items():
                if domain_types.get(var_name, "int") != "bool":
                    continue
                z3_var = z3_vars.get(var_name)
                if z3_var is None:
                    continue
                constraints.append(z3_var == BoolVal(bool(val)))
            return constraints

        def _try_solve(extra: list[object]) -> dict[str, object] | None:
            s = Solver()
            s.set("timeout", self.TIMEOUT_MS)
            s.add(phi_core + extra)
            try:
                if s.check() == sat:
                    m = s.model()
                    return {
                        vn: self._z3_val_to_python(
                            m.eval(zv, model_completion=True),
                            domain_types.get(vn, "int"),
                        )
                        for vn, zv in z3_vars.items()
                    }
            except z3.Z3Exception:
                pass
            return None

        # 層 1：完整偏好（bool + int 子區間）
        result = _try_solve(_bool_pref_constraints() + _int_pref_constraints())
        if result is None:
            # 層 2：只保留 int 子區間偏好
            result = _try_solve(_int_pref_constraints())
        if result is None:
            # 層 3：退回純 Φ_gap
            result = _try_solve([])
        assert result is not None, "Φ_gap 應為 SAT（synthesize 已驗證）"

        # 補充 domain_types 中不在 z3_vars 的其餘函數參數。
        # z3_vars 只含目標決策節點的條件變數，多參數函數（如 TCAS）還有其他必要參數。
        # 依 LLM 偏好決定值：bool 用 bool_values，int 用 int_preferences 對應的子區間中點，
        # 缺少偏好則用域中點作為預設值（比隨機值更符合「中等場景」的語意）。
        for var_name, var_type in (domain_types or {}).items():
            if var_name in result:
                continue
            if var_type == "bool":
                result[var_name] = bool(bool_values.get(var_name, _random.choice([True, False])))
            else:
                bounds = (self.domain_bounds or {}).get(var_name, [0, 130])
                lo, hi = bounds[0], bounds[1]
                span = hi - lo
                pref = str(int_preferences.get(var_name, "medium")).lower()
                if span >= 3:
                    third = span // 3
                    if pref == "low":
                        result[var_name] = lo + third // 2
                    elif pref == "high":
                        result[var_name] = lo + 2 * third + third // 2
                    else:
                        result[var_name] = lo + third + third // 2
                else:
                    result[var_name] = (lo + hi) // 2

        return result