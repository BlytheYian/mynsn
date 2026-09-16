"""
領域驗證結果資料模型。

TC-U-07: ValidationResult.to_corrective_prompt 格式測試
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Violation:
    """單一欄位的違規記錄。"""

    field: str
    description: str
    actual_value: str


@dataclass
class ValidationResult:
    """領域驗證的彙總結果。"""

    passed: bool
    violations: list[Violation]

    def to_corrective_prompt(self) -> str:
        """將所有違規轉換為 LLM 可用的修正提示。

        每個違規一行，格式：
            欄位 [{field}]：{description}，實際值：{actual_value}
        """
        lines = [
            f"欄位 [{v.field}]：{v.description}，實際值：{v.actual_value}"
            for v in self.violations
        ]
        return "\n".join(lines)


@dataclass
class DomainRule:
    """單一領域規則定義。"""

    field: str
    description: str
    validator: Callable[[Any], bool]
