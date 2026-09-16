"""
領域驗證器：驗證 LLM 輸出的測試案例符合醫療領域規則。

TC-U-41: 拒絕負數年齡
TC-U-42: 拒絕超過 130 的年齡
TC-U-43: 邊界值 0 和 130 通過（SRS FR-10：不得拒絕合法邊界）
TC-U-44: 拒絕負數距上次接種天數
TC-U-45: 拒絕非布林型高風險/過敏標記
TC-U-46: 無效 JSON 時 passed=False
"""
from __future__ import annotations

import json

from ifl_mcdc.models.validation import DomainRule, ValidationResult, Violation


DEFAULT_MEDICAL_RULES: list[DomainRule] = [
    DomainRule(
        field="age",
        description="年齡必須在 0～130 之間",
        validator=lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 130,
    ),
    DomainRule(
        field="days_since_last",
        description="距上次接種天數必須為非負整數",
        validator=lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    ),
    DomainRule(
        field="high_risk",
        description="高風險標記必須為布林值",
        validator=lambda v: isinstance(v, bool),
    ),
    DomainRule(
        field="egg_allergy",
        description="過敏標記必須為布林值",
        validator=lambda v: isinstance(v, bool),
    ),
]


class DomainValidator:
    """驗證 LLM 生成的測試案例是否符合醫療領域規則。"""

    def __init__(self, rules: list[DomainRule] | None = None) -> None:
        self.rules = rules if rules is not None else DEFAULT_MEDICAL_RULES

    def validate(self, test_case_json: str) -> ValidationResult:
        """驗證 JSON 字串形式的測試案例。

        Args:
            test_case_json: LLM 輸出的 JSON 字串。

        Returns:
            ValidationResult，passed=True 表示全部規則通過。
        """
        # 步驟 1：解析 JSON
        try:
            data: dict[str, object] = json.loads(test_case_json)
        except json.JSONDecodeError as exc:
            return ValidationResult(
                passed=False,
                violations=[
                    Violation(
                        field="__json__",
                        description=f"JSON 解析失敗：{exc}",
                        actual_value=test_case_json[:100],
                    )
                ],
            )

        # 步驟 2：逐一套用規則
        violations: list[Violation] = []
        for rule in self.rules:
            if rule.field not in data:
                continue  # 欄位不存在時跳過（允許部分欄位）
            value = data[rule.field]
            try:
                ok = rule.validator(value)
            except (TypeError, ValueError):
                ok = False
            if not ok:
                violations.append(
                    Violation(
                        field=rule.field,
                        description=rule.description,
                        actual_value=repr(value),
                    )
                )

        return ValidationResult(passed=len(violations) == 0, violations=violations)
