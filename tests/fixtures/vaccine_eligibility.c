#include <stdbool.h>

/*
 * 疫苗施打資格篩選邏輯（C 版）
 * k=4 個原子條件，對應 Python 版 vaccine_eligibility.py
 *
 * c1: age >= 65
 * c2: age >= 18 && high_risk
 * c3: days_since_last > 180
 * c4: egg_allergy（帶 !）
 */
bool check_vaccine_eligibility(int age, bool high_risk, int days_since_last, bool egg_allergy) {
    if (((age >= 65) || (age >= 18 && high_risk))
            && (days_since_last > 180)
            && !egg_allergy) {
        return true;
    }
    return false;
}
