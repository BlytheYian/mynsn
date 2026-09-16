def gpca_alarm_decision(
    flow_sensor_equipped: bool,
    flow_rate_ml_hr: int,        # 0-1000 ml/hr
    programmed_rate_ml_hr: int,  # 1-1000 ml/hr
    over_duration_min: int,      # 0-60 min
    free_flow: bool,
    under_duration_min: int,     # 0-60 min
    reservoir_volume_ml: int,    # 0-500 ml
    infusion_in_progress: bool,
    upstream_occlusion: bool,
    downstream_occlusion: bool,
) -> bool:
    """
    GPCA Infusion Pump Alarm Decision Logic
    Source: D. Arney et al., FDA/UPenn Technical Report MS-CIS-08-31, Feb 2009
    Aggregates Requirements 1.2.2, 1.2.3, 1.5.6, 1.10.1, 1.10.2
    k=10, independence_pairs=20

    Threshold notes (directly from document):
    - Req 1.2.2: "more than 10%" -> flow_rate * 10 > programmed * 11
    - Req 1.2.3: "less than 90%"  -> flow_rate * 10 < programmed * 9
    - Req 1.2.2: "more than 15 minutes" -> over_duration_min > 15 (strict)
    - Req 1.2.3: "15 minutes"           -> under_duration_min >= 15 (non-strict)
    - Integer arithmetic used throughout to avoid floating-point edge cases.
    """
    if (
        (flow_sensor_equipped and (
            (flow_rate_ml_hr * 10 > programmed_rate_ml_hr * 11
             and over_duration_min > 15)
            or free_flow
            or (flow_rate_ml_hr * 10 < programmed_rate_ml_hr * 9
                and under_duration_min >= 15)
        ))
        or (reservoir_volume_ml == 0 and infusion_in_progress)
        or upstream_occlusion
        or downstream_occlusion
    ):
        return True
    return False
