def evaluate_discharge_criteria(temp_ok: bool, hr_ok: bool, spo2: int, no_iv_vasopressor: bool, post_surgery: bool, wound_clear: bool, pain_score: int, can_ambulate: bool, has_caregiver: bool, self_care: bool):
    if not (temp_ok and hr_ok and spo2 >= 94 and not no_iv_vasopressor):
        return False

    if post_surgery:
        if not (wound_clear and pain_score < 5):
            return False
    else:
        if not (can_ambulate or (has_caregiver and self_care)):
            return False

    return True