def should_pause_medication(anticoagulant, high_blood_loss, gfr, age, diabetic, metformin, not_emergency, hours_since_last_dose):
    if anticoagulant and (high_blood_loss or gfr < 30):
        return True
    if age > 75 and diabetic and metformin:
        return True
    if not_emergency and hours_since_last_dose < 12:
        return True
    return False