#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

bool check_surgery_risk(int age, bool obese, bool has_diabetes, bool has_hypertension, bool is_smoker, bool low_hemoglobin, bool low_platelets, bool cardiac_history, bool has_copd);

int main(int argc, char* argv[]) {
    if (argc < 11) return 1;
    _ifl_test_id = atoi(argv[1]);
    int age = (int)atoi(argv[2]);
    bool obese = (bool)atoi(argv[3]);
    bool has_diabetes = (bool)atoi(argv[4]);
    bool has_hypertension = (bool)atoi(argv[5]);
    bool is_smoker = (bool)atoi(argv[6]);
    bool low_hemoglobin = (bool)atoi(argv[7]);
    bool low_platelets = (bool)atoi(argv[8]);
    bool cardiac_history = (bool)atoi(argv[9]);
    bool has_copd = (bool)atoi(argv[10]);
    check_surgery_risk(age, obese, has_diabetes, has_hypertension, is_smoker, low_hemoglobin, low_platelets, cardiac_history, has_copd);
    return 0;
}
