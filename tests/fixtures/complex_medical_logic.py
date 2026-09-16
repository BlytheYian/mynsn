"""
複雜醫療邏輯測試函式集
用於測試 IFL 系統在不同條件數量下的表現

k=8：藥物交互作用篩選
k=9：手術風險評估
k=10：ICU 入住資格評估
"""

"""
複雜醫療邏輯測試函式集（修正版）
"""


def check_drug_interaction(
    age: int,
    renal_failure: bool,
    liver_disease: bool,
    taking_warfarin: bool,
    taking_aspirin: bool,
    systolic_bp: int,
    heart_rate: int,
    is_pregnant: bool,
) -> bool:
    """藥物交互作用風險篩選（k=8）"""
    if (
        (age >= 65 or renal_failure or liver_disease)
        and (taking_warfarin and taking_aspirin)
        and (systolic_bp > 160 or heart_rate > 100)
        and not is_pregnant
    ):
        return True
    return False


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
    """
    手術風險評估（k=9）
    obese 代表 BMI >= 35，使用布林參數避免 Z3 型別衝突
    """
    if (
        (age >= 70 or obese)
        and (has_diabetes and has_hypertension)
        and (is_smoker or cardiac_history or has_copd)
        and (low_hemoglobin or low_platelets)
    ):
        return True
    return False


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
    """
    ICU 入住資格評估（k=10）
    注意：將數值比較預先轉為布林參數，避免 Z3 型別衝突
    """
    if (
        age >= 18
        and (low_bp or high_heart_rate)
        and (high_resp_rate or high_temp)
        and (low_gcs or low_oxygen)
        and (low_urine or high_creatinine or sepsis)
    ):
        return True
    return False