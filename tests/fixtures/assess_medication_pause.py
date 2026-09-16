def assess_medication_pause(patient_age, is_anticoagulant, surgery_blood_loss_high, creatinine_level, is_emergency_surgery, last_medication_time):
    if is_anticoagulant and (surgery_blood_loss_high or creatinine_level < 30):
        return True
    if patient_age > 75 and is_emergency_surgery and last_medication_time < 12:
        return True
    return False