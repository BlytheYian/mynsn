#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

bool check_icu_admission(int age, bool low_bp, bool high_heart_rate, bool high_resp_rate, bool high_temp, bool low_gcs, bool low_oxygen, bool low_urine, bool high_creatinine, bool sepsis);

int main(int argc, char* argv[]) {
    if (argc < 12) return 1;
    _ifl_test_id = atoi(argv[1]);
    int age = (int)atoi(argv[2]);
    bool low_bp = (bool)atoi(argv[3]);
    bool high_heart_rate = (bool)atoi(argv[4]);
    bool high_resp_rate = (bool)atoi(argv[5]);
    bool high_temp = (bool)atoi(argv[6]);
    bool low_gcs = (bool)atoi(argv[7]);
    bool low_oxygen = (bool)atoi(argv[8]);
    bool low_urine = (bool)atoi(argv[9]);
    bool high_creatinine = (bool)atoi(argv[10]);
    bool sepsis = (bool)atoi(argv[11]);
    check_icu_admission(age, low_bp, high_heart_rate, high_resp_rate, high_temp, low_gcs, low_oxygen, low_urine, high_creatinine, sepsis);
    return 0;
}
