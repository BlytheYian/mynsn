#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

bool check_loan_approval(int credit_score, int annual_income, int loan_amount, bool employed, bool has_collateral, bool bankruptcy_history);

int main(int argc, char* argv[]) {
    if (argc < 8) return 1;
    _ifl_test_id = atoi(argv[1]);
    int credit_score = (int)atoi(argv[2]);
    int annual_income = (int)atoi(argv[3]);
    int loan_amount = (int)atoi(argv[4]);
    bool employed = (bool)atoi(argv[5]);
    bool has_collateral = (bool)atoi(argv[6]);
    bool bankruptcy_history = (bool)atoi(argv[7]);
    check_loan_approval(credit_score, annual_income, loan_amount, employed, has_collateral, bankruptcy_history);
    return 0;
}
