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
    if (
        (flow_sensor_equipped && (
            (flow_rate_ml_hr * 10 > programmed_rate_ml_hr * 11
             && over_duration_min > 15)
            || free_flow
            || (flow_rate_ml_hr * 10 < programmed_rate_ml_hr * 9
                && under_duration_min >= 15)
        ))
        || (reservoir_volume_ml == 0 && infusion_in_progress)
        || upstream_occlusion
        || downstream_occlusion
    ) {
        return true;
    }
    return false;
}
