#include "ifl_probe.h"
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
    int _D1_c1 = (age >= 65);
    int _D1_c2 = (age >= 18);
    int _D1_c3 = (high_risk);
    int _D1_c4 = (days_since_last > 180);
    int _D1_c5 = (egg_allergy);
    _ifl_record_cond("D1.c1", _D1_c1);
    _ifl_record_cond("D1.c2", _D1_c2);
    _ifl_record_cond("D1.c3", _D1_c3);
    _ifl_record_cond("D1.c4", _D1_c4);
    _ifl_record_cond("D1.c5", _D1_c5);
    int _D1_decision = (((_D1_c1) || (_D1_c2 && _D1_c3)) && (_D1_c4) && !_D1_c5);
    _ifl_record_decision("D1", _D1_decision);
    if (_D1_decision) {
        return true;
    }
    return false;
}
