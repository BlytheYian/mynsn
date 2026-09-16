#include "ifl_probe.h"
#include <stdbool.h>

/*
 * GPCA 輸液幫浦安全告警（C 版）k=10
 * 對應 Python 版 gpca_alarm.py
 * Source: D. Arney et al., FDA/UPenn Technical Report MS-CIS-08-31, Feb 2009
 *
 * 整數算術避免浮點邊界問題：
 *   過量 10%:  flow_rate * 10 > programmed * 11
 *   不足 90%:  flow_rate * 10 < programmed * 9
 */
bool gpca_alarm_decision(bool flow_sensor_equipped, int flow_rate_ml_hr,
                         int programmed_rate_ml_hr, int over_duration_min,
                         bool free_flow, int under_duration_min,
                         int reservoir_volume_ml, bool infusion_in_progress,
                         bool upstream_occlusion, bool downstream_occlusion) {
    int _D1_c1 = (flow_sensor_equipped);
    int _D1_c2 = (flow_rate_ml_hr * 10 > programmed_rate_ml_hr * 11);
    int _D1_c3 = (over_duration_min > 15);
    int _D1_c4 = (free_flow);
    int _D1_c5 = (flow_rate_ml_hr * 10 < programmed_rate_ml_hr * 9);
    int _D1_c6 = (under_duration_min >= 15);
    int _D1_c7 = (reservoir_volume_ml == 0);
    int _D1_c8 = (infusion_in_progress);
    int _D1_c9 = (upstream_occlusion);
    int _D1_c10 = (downstream_occlusion);
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
    int _D1_decision = ((_D1_c1 && ( (_D1_c2 && _D1_c3) || _D1_c4 || (_D1_c5 && _D1_c6) )) || (_D1_c7 && _D1_c8) || _D1_c9 || _D1_c10);
    _ifl_record_decision("D1", _D1_decision);
    if (_D1_decision) {
        return true;
    }
    return false;
}
