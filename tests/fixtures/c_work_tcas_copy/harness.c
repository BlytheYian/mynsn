#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

int alt_sep_test(int Cur_Vertical_Sep, bool High_Confidence, bool Two_of_Three_Reports_Valid, int Own_Tracked_Alt, int Own_Tracked_Alt_Rate, int Other_Tracked_Alt, int Alt_Layer_Value, int Up_Separation, int Down_Separation, int Other_RAC, int Other_Capability, int Climb_Inhibit);

int main(int argc, char* argv[]) {
    if (argc < 14) return 1;
    _ifl_test_id = atoi(argv[1]);
    int Cur_Vertical_Sep = (int)atoi(argv[2]);
    bool High_Confidence = (bool)atoi(argv[3]);
    bool Two_of_Three_Reports_Valid = (bool)atoi(argv[4]);
    int Own_Tracked_Alt = (int)atoi(argv[5]);
    int Own_Tracked_Alt_Rate = (int)atoi(argv[6]);
    int Other_Tracked_Alt = (int)atoi(argv[7]);
    int Alt_Layer_Value = (int)atoi(argv[8]);
    int Up_Separation = (int)atoi(argv[9]);
    int Down_Separation = (int)atoi(argv[10]);
    int Other_RAC = (int)atoi(argv[11]);
    int Other_Capability = (int)atoi(argv[12]);
    int Climb_Inhibit = (int)atoi(argv[13]);
    alt_sep_test(Cur_Vertical_Sep, High_Confidence, Two_of_Three_Reports_Valid, Own_Tracked_Alt, Own_Tracked_Alt_Rate, Other_Tracked_Alt, Alt_Layer_Value, Up_Separation, Down_Separation, Other_RAC, Other_Capability, Climb_Inhibit);
    return 0;
}
