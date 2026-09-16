#include <stdbool.h>

/*
 * ICU 入住資格評估（C 版）k=10
 * 對應 Python 版 icu_admission.py
 */
bool check_icu_admission(int age, bool low_bp, bool high_heart_rate, bool high_resp_rate,
                         bool high_temp, bool low_gcs, bool low_oxygen, bool low_urine,
                         bool high_creatinine, bool sepsis) {
    if (
        age >= 18
        && (low_bp || high_heart_rate)
        && (high_resp_rate || high_temp)
        && (low_gcs || low_oxygen)
        && (low_urine || high_creatinine || sepsis)
    ) {
        return true;
    }
    return false;
}
