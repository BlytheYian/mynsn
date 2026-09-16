#include "ifl_probe.h"
#include <stdbool.h>

/*
 * 貸款審核邏輯（C 版）
 * k=6 個原子條件，對應 Python 版 loan_approval.py
 *
 * c1: credit_score >= 700
 * c2: annual_income >= 50000
 * c3: loan_amount <= 500000
 * c4: has_collateral
 * c5: employed
 * c6: bankruptcy_history（帶 !）
 */
bool check_loan_approval(int credit_score, int annual_income, int loan_amount,
                         bool employed, bool has_collateral, bool bankruptcy_history) {
    int _D1_c1 = (credit_score >= 700);
    int _D1_c2 = (annual_income >= 50000);
    int _D1_c3 = (loan_amount <= 500000);
    int _D1_c4 = (has_collateral);
    int _D1_c5 = (employed);
    int _D1_c6 = (bankruptcy_history);
    _ifl_record_cond("D1.c1", _D1_c1);
    _ifl_record_cond("D1.c2", _D1_c2);
    _ifl_record_cond("D1.c3", _D1_c3);
    _ifl_record_cond("D1.c4", _D1_c4);
    _ifl_record_cond("D1.c5", _D1_c5);
    _ifl_record_cond("D1.c6", _D1_c6);
    int _D1_decision = ((_D1_c1 || _D1_c2) && (_D1_c3 || _D1_c4) && _D1_c5 && !_D1_c6);
    _ifl_record_decision("D1", _D1_decision);
    if (_D1_decision) {
        return true;
    }
    return false;
}
