#include "ifl_probe.h"
#include <stdbool.h>

/*
 * 手術風險評估（C 版）k=9
 * 對應 Python 版 surgery_risk.py
 */
bool check_surgery_risk(int age, bool obese, bool has_diabetes, bool has_hypertension,
                        bool is_smoker, bool low_hemoglobin, bool low_platelets,
                        bool cardiac_history, bool has_copd) {
    int _D1_c1 = (age >= 70);
    int _D1_c2 = (obese);
    int _D1_c3 = (has_diabetes);
    int _D1_c4 = (has_hypertension);
    int _D1_c5 = (is_smoker);
    int _D1_c6 = (cardiac_history);
    int _D1_c7 = (has_copd);
    int _D1_c8 = (low_hemoglobin);
    int _D1_c9 = (low_platelets);
    _ifl_record_cond("D1.c1", _D1_c1);
    _ifl_record_cond("D1.c2", _D1_c2);
    _ifl_record_cond("D1.c3", _D1_c3);
    _ifl_record_cond("D1.c4", _D1_c4);
    _ifl_record_cond("D1.c5", _D1_c5);
    _ifl_record_cond("D1.c6", _D1_c6);
    _ifl_record_cond("D1.c7", _D1_c7);
    _ifl_record_cond("D1.c8", _D1_c8);
    _ifl_record_cond("D1.c9", _D1_c9);
    int _D1_decision = ((_D1_c1 || _D1_c2) && (_D1_c3 && _D1_c4) && (_D1_c5 || _D1_c6 || _D1_c7) && (_D1_c8 || _D1_c9));
    _ifl_record_decision("D1", _D1_decision);
    if (_D1_decision) {
        return true;
    }
    return false;
}
