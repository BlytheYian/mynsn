#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

bool gpca_alarm_decision(bool flow_sensor_equipped, int flow_rate_ml_hr, int programmed_rate_ml_hr, int over_duration_min, bool free_flow, int under_duration_min, int reservoir_volume_ml, bool infusion_in_progress, bool upstream_occlusion, bool downstream_occlusion);

int main(int argc, char* argv[]) {
    if (argc < 12) return 1;
    _ifl_test_id = atoi(argv[1]);
    bool flow_sensor_equipped = (bool)atoi(argv[2]);
    int flow_rate_ml_hr = (int)atoi(argv[3]);
    int programmed_rate_ml_hr = (int)atoi(argv[4]);
    int over_duration_min = (int)atoi(argv[5]);
    bool free_flow = (bool)atoi(argv[6]);
    int under_duration_min = (int)atoi(argv[7]);
    int reservoir_volume_ml = (int)atoi(argv[8]);
    bool infusion_in_progress = (bool)atoi(argv[9]);
    bool upstream_occlusion = (bool)atoi(argv[10]);
    bool downstream_occlusion = (bool)atoi(argv[11]);
    gpca_alarm_decision(flow_sensor_equipped, flow_rate_ml_hr, programmed_rate_ml_hr, over_duration_min, free_flow, under_duration_min, reservoir_volume_ml, infusion_in_progress, upstream_occlusion, downstream_occlusion);
    return 0;
}
