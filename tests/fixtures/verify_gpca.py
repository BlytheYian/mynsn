"""
verify_gpca.py
用 Z3 驗證 gpca_alarm_decision 所有 MC/DC independence pairs 是否 SAT。

Independence pair 定義（嚴格 two-set 版本）：
  對每個 condition ci，存在 inputs_A 和 inputs_B 使得：
    - condition_i(A) = True,  condition_i(B) = False
    - 所有其他 condition j != i 的值在 A 和 B 中相同
    - output(A) = True,  output(B) = False

實作：建立兩套完整的 Z3 Int/Bool 變數（suffix _a, _b），
分別計算 10 個 conditions 和 output，加入上述約束後求解。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from z3 import And, Bool, BoolVal, If, Int, Not, Or, Solver, sat  # noqa: E402


# ── 變數工廠 ──────────────────────────────────────────────────────

def _make_vars(suffix: str) -> dict:
    return {
        "fse":   Bool(f"fse_{suffix}"),
        "flow":  Int(f"flow_{suffix}"),
        "prog":  Int(f"prog_{suffix}"),
        "over":  Int(f"over_{suffix}"),
        "ff":    Bool(f"ff_{suffix}"),
        "under": Int(f"under_{suffix}"),
        "res":   Int(f"res_{suffix}"),
        "iip":   Bool(f"iip_{suffix}"),
        "uo":    Bool(f"uo_{suffix}"),
        "do_":   Bool(f"do_{suffix}"),
    }


def _domain(v: dict) -> list:
    """合法輸入域約束。programmed_rate >= 1 避免語義上的除以零。"""
    return [
        v["flow"]  >= 0,   v["flow"]  <= 1000,
        v["prog"]  >= 1,   v["prog"]  <= 1000,
        v["over"]  >= 0,   v["over"]  <= 60,
        v["under"] >= 0,   v["under"] <= 60,
        v["res"]   >= 0,   v["res"]   <= 500,
    ]


def _conditions(v: dict) -> list:
    """
    計算 10 個原子條件的 Z3 表達式。

    c2: flow * 10 > prog * 11   ↔  flow > prog * 1.10  (Req 1.2.2, strict)
    c5: flow * 10 < prog * 9    ↔  flow < prog * 0.90  (Req 1.2.3, strict)
    c6: under >= 15                                      (Req 1.2.3, non-strict)
    """
    return [
        v["fse"],                                 # c1  flow_sensor_equipped
        v["flow"] * 10 > v["prog"] * 11,          # c2  flow_rate > programmed * 110%
        v["over"] > 15,                           # c3  over_duration_min > 15
        v["ff"],                                  # c4  free_flow
        v["flow"] * 10 < v["prog"] * 9,           # c5  flow_rate < programmed * 90%
        v["under"] >= 15,                         # c6  under_duration_min >= 15
        v["res"] == 0,                            # c7  reservoir_volume_ml == 0
        v["iip"],                                 # c8  infusion_in_progress
        v["uo"],                                  # c9  upstream_occlusion
        v["do_"],                                 # c10 downstream_occlusion
    ]


def _output(c: list):
    """GPCA 警報輸出：接受長度 10 的條件清單（Z3 expressions）。"""
    c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = c
    overinfusion  = And(c1, Or(And(c2, c3), c4))
    underinfusion = And(c1, c5, c6)
    reservoir     = And(c7, c8)
    occlusion     = Or(c9, c10)
    return Or(overinfusion, underinfusion, reservoir, occlusion)


# ── Independence pair 檢查 ────────────────────────────────────────

def check_independence_pair(target_idx: int) -> bool:
    """
    對索引 target_idx 的 condition 做 two-set independence pair SAT 檢查。

    Two-set 方法：
      va = 一套完整輸入變數（A 側）
      vb = 另一套完整輸入變數（B 側）
      ca = conditions 計算自 va；cb = conditions 計算自 vb
      約束：
        ca[i] == True,  cb[i] == False          （目標條件在兩側不同）
        ca[j] == cb[j]  for j != i              （其他條件真值相同）
        output(ca) == True, output(cb) == False  （輸出相反）
    """
    va = _make_vars("a")
    vb = _make_vars("b")
    ca = _conditions(va)
    cb = _conditions(vb)

    s = Solver()
    s.add(_domain(va))
    s.add(_domain(vb))

    # 目標條件：A 為 True，B 為 False
    s.add(ca[target_idx] == BoolVal(True))
    s.add(cb[target_idx] == BoolVal(False))

    # 非目標條件：兩側真值相同
    for j in range(10):
        if j != target_idx:
            s.add(ca[j] == cb[j])

    # 輸出相反：A=True, B=False
    s.add(_output(ca) == BoolVal(True))
    s.add(_output(cb) == BoolVal(False))

    return s.check() == sat


# ── 主程式 ────────────────────────────────────────────────────────

LABELS = [
    ("c1",  "flow_sensor_equipped         "),
    ("c2",  "flow_rate > programmed * 110%"),
    ("c3",  "over_duration_min > 15       "),
    ("c4",  "free_flow                    "),
    ("c5",  "flow_rate < programmed * 90% "),
    ("c6",  "under_duration_min >= 15     "),
    ("c7",  "reservoir_volume_ml == 0     "),
    ("c8",  "infusion_in_progress         "),
    ("c9",  "upstream_occlusion           "),
    ("c10", "downstream_occlusion         "),
]


def main() -> None:
    print("gpca_alarm_decision -- MC/DC Independence Pair Feasibility (Z3 two-set)")
    print("-" * 66)

    sat_count = 0
    for idx, (cid, label) in enumerate(LABELS):
        ok = check_independence_pair(idx)
        status = "SAT ✓" if ok else "UNSAT ✗"
        if ok:
            sat_count += 1
        print(f"  {cid:<3} {label}: {status}")

    print("-" * 66)
    print(f"  Total feasible: {sat_count} / 10")
    if sat_count == 10:
        print("  gpca_alarm (k=10): all independence pairs SAT ✓")
    else:
        print("  [!] Strongly coupled conditions detected -- review above.")


if __name__ == "__main__":
    main()
