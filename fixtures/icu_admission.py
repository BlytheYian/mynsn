"""
ICU 入住資格評估（k=10）
使用布林參數避免 Z3 型別衝突
"""


def check_icu_admission(
    age: int,
    low_bp: bool,
    high_heart_rate: bool,
    high_resp_rate: bool,
    high_temp: bool,
    low_gcs: bool,
    low_oxygen: bool,
    low_urine: bool,
    high_creatinine: bool,
    sepsis: bool,
) -> bool:
    """ICU 入住資格評估（k=10）"""
    if (
        age >= 18
        and (low_bp or high_heart_rate)
        and (high_resp_rate or high_temp)
        and (low_gcs or low_oxygen)
        and (low_urine or high_creatinine or sepsis)
    ):
        return True
    return False
