"""
用藥劑量調整指示（k=5，共用 age 變數）

條件說明：
  c1: age >= 65  （老年族群，與 c2 共用 age）
  c2: age >= 18  （成人資格，共用 age）
  c3: weight > 70（體重超標）
  c4: renal_impaired（腎功能異常，帶 not）
  c5: liver_impaired（肝功能異常，帶 not）

關鍵特性：
  c1 與 c2 共用 age 變數（和 vaccine_eligibility 的 age>=65 / age>=18 結構相同）。
  c1=True (age>=65) 時 c2 必然為 True，導致 OR 子式 = True。
  因此 c1 的 T2F MC/DC 配對需要 weight<=70（讓 c2 AND c3 = False）才能使 OR=False，
  否則在 c1 翻轉為 False 後整體決策仍為 True，無法構成有效 MC/DC 配對。

  Pairwise feasibility 約束應能識別此情況，強制 Z3 在求 c1 True 側時
  選擇 weight<=70，確保 False 側可行。

MC/DC 需要 10 個獨立對達到 100% 覆蓋率。
"""


def check_dose_adjustment(
    age: int,
    weight: int,
    renal_impaired: bool,
    liver_impaired: bool,
) -> bool:
    """用藥劑量調整指示（k=5，共用 age 變數，MC/DC 需 10 個獨立對）"""
    if (
        (age >= 65 or (age >= 18 and weight > 70))
        and not renal_impaired
        and not liver_impaired
    ):
        return True
    return False
