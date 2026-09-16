#include <stdbool.h>

/*
 * 手術風險評估（C 版）k=9
 * 對應 Python 版 surgery_risk.py
 */
bool check_surgery_risk(int age, bool obese, bool has_diabetes, bool has_hypertension,
                        bool is_smoker, bool low_hemoglobin, bool low_platelets,
                        bool cardiac_history, bool has_copd) {
    if (
        (age >= 70 || obese)
        && (has_diabetes && has_hypertension)
        && (is_smoker || cardiac_history || has_copd)
        && (low_hemoglobin || low_platelets)
    ) {
        return true;
    }
    return false;
}
