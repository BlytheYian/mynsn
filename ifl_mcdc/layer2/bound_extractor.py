"""
邊界萃取器：從 Z3 Φ_gap 公式求解每個變數的真實合法範圍，輸出 BoundSpec 列表。

舊做法：從 model_val 取點，用 (val, val+10) 做為邊界 → 嚴重低估可行空間。
新做法：對每個 int 變數用 z3.Optimize() 分別 maximize/minimize，
        求出 Φ_gap 下的真實上下界，確保 LLM 收到正確的約束範圍。

TC-U-34: BoundExtractor——整數型邊界萃取
TC-U-35: BoundExtractor——布林型合法集合萃取
TC-U-36: BoundSpec 不可為空區間
"""
from __future__ import annotations

import z3

from ifl_mcdc.models.smt_models import BoundSpec

_OPTIMIZE_TIMEOUT_MS = 5_000  # 每個變數的上下界求解超時（毫秒）


class BoundExtractor:
    """從 Φ_gap 公式求解每個變數的真實合法範圍，輸出 BoundSpec 列表。

    int  型：用 z3.Optimize() maximize/minimize 求出 Φ_gap 下的真實上下界。
    bool 型：valid_set = frozenset({bool(model_val)})，未約束則 {True, False}。
    """

    def extract(
        self,
        z3_model: z3.ModelRef,
        z3_vars: dict[str, object],
        domain_types: dict[str, str],
        domain_bounds: dict[str, list[int]] | None = None,
        phi: list[object] | None = None,
    ) -> list[BoundSpec]:
        """從 Z3 model 與 Φ_gap 公式萃取 BoundSpec 列表。

        Args:
            z3_model: Z3 SAT model（s.model()），用於取 bool 變數的值。
            z3_vars: var_name → Z3 變數的映射。
            domain_types: var_name → "int" | "bool" | "float" 的映射。
            domain_bounds: var_name → [min, max]，作為 Optimize 的硬性邊界。
            phi: Φ_gap 約束列表（若提供，對 int 變數做 Optimize 求真實範圍）。

        Returns:
            每個變數一個 BoundSpec 的列表。
        """
        specs: list[BoundSpec] = []
        for var_name, z3_var in z3_vars.items():
            var_type = domain_types.get(var_name, "int")
            model_val = z3_model[z3_var]

            if var_type == "bool":
                if model_val is None:
                    specs.append(BoundSpec(
                        var_name=var_name, var_type="bool",
                        interval=None, valid_set=frozenset({True, False}),
                    ))
                else:
                    specs.append(BoundSpec(
                        var_name=var_name, var_type="bool",
                        interval=None, valid_set=frozenset({bool(z3.is_true(model_val))}),
                    ))

            elif var_type == "int":
                # domain_bounds 作為硬性邊界（Z3 Optimize 的上下限）
                db_lo: int = 0
                db_hi: int = 9999999
                if domain_bounds and var_name in domain_bounds:
                    db_lo, db_hi = domain_bounds[var_name][0], domain_bounds[var_name][1]

                true_lo, true_hi = self._solve_int_bounds(
                    z3_var, phi or [], db_lo, db_hi
                )

                # 確保區間非空
                if true_lo > true_hi:
                    true_lo, true_hi = db_lo, db_hi

                specs.append(BoundSpec(
                    var_name=var_name, var_type="int",
                    interval=(float(true_lo), float(true_hi)),
                    valid_set=None,
                ))

            else:  # float / real
                if model_val is None:
                    fallback: tuple[float, float] | None = None
                    if domain_bounds and var_name in domain_bounds:
                        lo, hi = domain_bounds[var_name]
                        fallback = (float(lo), float(hi))
                    specs.append(BoundSpec(
                        var_name=var_name, var_type="float",
                        interval=fallback, valid_set=None,
                    ))
                else:
                    try:
                        fv = float(z3.RealVal(str(model_val)).as_decimal(10).rstrip("?"))
                    except (ValueError, ArithmeticError):
                        fv = 0.0
                    interval: tuple[float, float] = (fv - 10.0, fv + 10.0)
                    if domain_bounds and var_name in domain_bounds:
                        lo, hi = domain_bounds[var_name]
                        interval = (float(max(lo, fv)), float(min(hi, fv + 10.0)))
                    specs.append(BoundSpec(
                        var_name=var_name, var_type="float",
                        interval=interval, valid_set=None,
                    ))

        return specs

    @staticmethod
    def _solve_int_bounds(
        z3_var: object,
        phi: list[object],
        db_lo: int,
        db_hi: int,
    ) -> tuple[int, int]:
        """用 z3.Optimize() 求 z3_var 在 phi 約束下的真實上下界。

        若 phi 為空或 Optimize 失敗，退回 domain_bounds。
        """
        if not phi:
            return db_lo, db_hi

        try:
            # 求下界
            opt_lo = z3.Optimize()
            opt_lo.set("timeout", _OPTIMIZE_TIMEOUT_MS)
            for c in phi:
                opt_lo.add(c)
            opt_lo.minimize(z3_var)
            lo_val = db_lo
            if opt_lo.check() == z3.sat:
                raw = opt_lo.model()[z3_var]
                if raw is not None:
                    lo_val = int(str(raw))

            # 求上界
            opt_hi = z3.Optimize()
            opt_hi.set("timeout", _OPTIMIZE_TIMEOUT_MS)
            for c in phi:
                opt_hi.add(c)
            opt_hi.maximize(z3_var)
            hi_val = db_hi
            if opt_hi.check() == z3.sat:
                raw = opt_hi.model()[z3_var]
                if raw is not None:
                    hi_val = int(str(raw))

            return lo_val, hi_val
        except Exception:
            return db_lo, db_hi