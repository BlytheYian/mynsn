#include "ifl_probe.h"
#include <stdbool.h>

/*
 * ICU 入住資格評估（C 版）k=10
 * 對應 Python 版 icu_admission.py
 */
bool check_icu_admission(int age, bool low_bp, bool high_heart_rate, bool high_resp_rate,
                         bool high_temp, bool low_gcs, bool low_oxygen, bool low_urine,
                         bool high_creatinine, bool sepsis) {
    int _D1_c1 = (age >= 18);
    int _D1_c2 = (low_bp);
    int _D1_c3 = (high_heart_rate);
    int _D1_c4 = (high_resp_rate);
    int _D1_c5 = (high_temp);
    int _D1_c6 = (low_gcs);
    int _D1_c7 = (low_oxygen);
    int _D1_c8 = (low_urine);
    int _D1_c9 = (high_creatinine);
    int _D1_c10 = (sepsis);
    _ifl_record_cond("D1.c1", _D1_c1);
    _ifl_record_cond("D1.c2", _D1_c2);
    _ifl_record_cond("D1.c3", _D1_c3);
    _ifl_record_cond("D1.c4", _D1_c4);
    _ifl_record_cond("D1.c5", _D1_c5);
    _ifl_record_cond("D1.c6", _D1_c6);
    _ifl_record_cond("D1.c7", _D1_c7);
    _ifl_record_cond("D1.c8", _D1_c8);
    _ifl_record_cond("D1.c9", _D1_c9);
    _ifl_record_cond("D1.c10", _D1_c10);
    int _D1_decision = (_D1_c1 && (_D1_c2 || _D1_c3) && (_D1_c4 || _D1_c5) && (_D1_c6 || _D1_c7) && (_D1_c8 || _D1_c9 || _D1_c10));
    _ifl_record_decision("D1", _D1_decision);
    if (_D1_decision) {
        return true;
    }
    return false;
}
