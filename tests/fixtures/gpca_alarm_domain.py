"""
GPCA 輸液泵警報 Domain Spec（gpca_alarm_decision, k=10）

所有整數欄位的合法範圍與布林欄位的型別規則。
shared condition 標記：flow_sensor_equipped 同時出現在 overinfusion 與 underinfusion。
"""
from __future__ import annotations

from ifl_mcdc.models.validation import DomainRule


def _bool_rule(field: str, desc: str) -> DomainRule:
    return DomainRule(
        field=field,
        description=f"{desc}必須為布林值",
        validator=lambda v: isinstance(v, bool),
    )


def _int_range_rule(field: str, lo: int, hi: int, desc: str) -> DomainRule:
    return DomainRule(
        field=field,
        description=f"{desc}必須為整數（{lo}～{hi}）",
        validator=lambda v, lo=lo, hi=hi: (
            isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
        ),
    )


# ── 域規則 ────────────────────────────────────────────────────────

GPCA_ALARM_RULES: list[DomainRule] = [
    _bool_rule("flow_sensor_equipped",  "流量感測器裝備標記"),
    _int_range_rule("flow_rate_ml_hr",        0, 1000, "實際流速（ml/hr）"),
    _int_range_rule("programmed_rate_ml_hr",  1, 1000, "設定流速（ml/hr）"),
    _int_range_rule("over_duration_min",      0,   60, "超量持續時間（分鐘）"),
    _bool_rule("free_flow",             "自由流動標記"),
    _int_range_rule("under_duration_min",     0,   60, "不足持續時間（分鐘）"),
    _int_range_rule("reservoir_volume_ml",    0,  500, "藥液儲量（ml）"),
    _bool_rule("infusion_in_progress",  "輸液進行中標記"),
    _bool_rule("upstream_occlusion",    "上游堵塞標記"),
    _bool_rule("downstream_occlusion",  "下游堵塞標記"),
]

# ── Fixture Spec（供 run_validation / run_crosshair 使用）──────────

DOMAIN_TYPES: dict[str, str] = {
    "flow_sensor_equipped":  "bool",
    "flow_rate_ml_hr":       "int",
    "programmed_rate_ml_hr": "int",
    "over_duration_min":     "int",
    "free_flow":             "bool",
    "under_duration_min":    "int",
    "reservoir_volume_ml":   "int",
    "infusion_in_progress":  "bool",
    "upstream_occlusion":    "bool",
    "downstream_occlusion":  "bool",
}

DOMAIN_BOUNDS: dict[str, list[int]] = {
    "flow_rate_ml_hr":       [0, 1000],
    "programmed_rate_ml_hr": [1, 1000],
    "over_duration_min":     [0, 60],
    "under_duration_min":    [0, 60],
    "reservoir_volume_ml":   [0, 500],
}

# shared condition 標記（flow_sensor_equipped 同時控制 overinfusion 和 underinfusion）
SHARED_CONDITIONS: list[str] = ["flow_sensor_equipped"]

FUNC_DEF = """\
def gpca_alarm_decision(flow_sensor_equipped, flow_rate_ml_hr, programmed_rate_ml_hr,
                        over_duration_min, free_flow, under_duration_min,
                        reservoir_volume_ml, infusion_in_progress,
                        upstream_occlusion, downstream_occlusion):
    overinfusion = flow_sensor_equipped and (
        (flow_rate_ml_hr * 10 > programmed_rate_ml_hr * 11 and over_duration_min > 15)
        or free_flow
    )
    underinfusion = flow_sensor_equipped and (
        flow_rate_ml_hr * 10 < programmed_rate_ml_hr * 9
        and under_duration_min >= 15
    )
    reservoir = reservoir_volume_ml == 0 and infusion_in_progress
    occlusion = upstream_occlusion or downstream_occlusion
    return overinfusion or underinfusion or reservoir or occlusion"""
