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
    if ((credit_score >= 700 || annual_income >= 50000)
            && (loan_amount <= 500000 || has_collateral)
            && employed
            && !bankruptcy_history) {
        return true;
    }
    return false;
}
