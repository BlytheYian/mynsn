#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

bool check_vaccine_eligibility(int age, bool high_risk, int days_since_last, bool egg_allergy);

int main(int argc, char* argv[]) {
    if (argc < 6) return 1;
    _ifl_test_id = atoi(argv[1]);
    int age = (int)atoi(argv[2]);
    bool high_risk = (bool)atoi(argv[3]);
    int days_since_last = (int)atoi(argv[4]);
    bool egg_allergy = (bool)atoi(argv[5]);
    check_vaccine_eligibility(age, high_risk, days_since_last, egg_allergy);
    return 0;
}
