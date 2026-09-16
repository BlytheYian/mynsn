def can_vaccinate(age, is_high_risk, days_since_vaccination, has_egg_allergy):
    if age >= 18 and (is_high_risk or age >= 65) and days_since_vaccination > 180 and not has_egg_allergy:
        return True
    else:
        return False