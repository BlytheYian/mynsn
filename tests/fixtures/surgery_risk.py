"""
手術風險評估（k=9）
使用布林參數避免 Z3 型別衝突
"""


def check_surgery_risk(
    age: int,
    obese: bool,
    has_diabetes: bool,
    has_hypertension: bool,
    is_smoker: bool,
    low_hemoglobin: bool,
    low_platelets: bool,
    cardiac_history: bool,
    has_copd: bool,
) -> bool:
    """手術風險評估（k=9）"""
    if (
        (age >= 70 or obese)
        and (has_diabetes and has_hypertension)
        and (is_smoker or cardiac_history or has_copd)
        and (low_hemoglobin or low_platelets)
    ):
        return True
    return False
