def check_vaccine_eligibility(
    age: int,
    high_risk: bool,
    days_since_last: int,
    egg_allergy: bool,
) -> bool:
    """
    疫苗施打資格篩選邏輯
    k=4 個原子條件，需要 8 個獨立對達到 100% MC/DC

    條件說明：
      c1: age >= 65
      c2: age >= 18 and high_risk
      c3: days_since_last > 180
      c4: egg_allergy（帶 not）
    """
    if ((age >= 65 or (age >= 18 and high_risk))
            and (days_since_last > 180)
            and not egg_allergy):
        return True
    return False
