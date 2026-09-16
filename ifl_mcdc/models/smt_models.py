"""
SMT 求解相關資料模型。

TC-U-06: BoundSpec.to_prompt_str 輸出格式測試
TC-U-19~23: MaskingReport（BooleanDerivativeEngine 輸出）
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MaskingReport:
    """布林導數計算結果：條件是否被遮罩及成因。"""

    condition_id: str
    is_masked: bool
    masking_cause: list[str]       # 造成遮罩的其他條件 cond_id 列表
    derivative_value: int          # 0 = 恆遮罩，1 = 可獨立影響


@dataclass
class BoundSpec:
    """單一變數的 SMT 邊界規格。"""

    var_name: str
    var_type: str                               # "int" | "bool" | "float"
    interval: tuple[float, float] | None        # 數值型：(min, max)
    valid_set: frozenset[object] | None         # 類別型：允許的值集合
    medical_unit: str = ""
    sub_intervals: list[tuple[float, float]] | None = field(default=None)
    # 長尾邊界覆蓋用的三個子區間，index 0=邊界區（靠近臨界點）、1=中間區、2=極端區

    # def to_prompt_str(self) -> str:
    #     """回傳人類可讀的約束描述。
    #
    #     範例：
    #         "age: int，範圍 [18, 64]（單位：years）"
    #         "high_risk: bool，必須為 True"
    #     """
    #     parts: list[str] = [f"{self.var_name}: {self.var_type}"]
    #     if self.interval is not None:
    #         lo, hi = self.interval
    #         lo_str = str(int(lo)) if lo == int(lo) else str(lo)
    #         hi_str = str(int(hi)) if hi == int(hi) else str(hi)
    #         parts.append(f"，範圍 [{lo_str}, {hi_str}]")
    #     if self.valid_set is not None:
    #         parts.append(f"，必須為 {sorted(self.valid_set, key=str)}")
    #     if self.medical_unit:
    #         parts.append(f"（單位：{self.medical_unit}）")
    #     return "".join(parts)


@dataclass
class SMTResult:
    """Z3 求解器的回傳結果。"""

    satisfiable: bool
    model: dict[str, object] | None = field(default=None)
    model_python: dict[str, object] | None = field(default=None)
    # model 存 Z3 原始物件；model_python 為 Python-native 具體解，
    # 可在 LLM 輸出違反約束時直接作為 fallback 測試案例使用。
    bound_specs: list[BoundSpec] | None = field(default=None)             # True 側（目標條件=True）BoundSpec
    complement_bound_specs: list[BoundSpec] | None = field(default=None)  # False 側（目標條件=False）BoundSpec
    core: list[str] | None = field(default=None)
    solve_time: float = field(default=0.0)
