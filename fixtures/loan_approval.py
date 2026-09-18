"""
貸款審核邏輯（k=6，無共用變數）

條件說明：
  c1: credit_score >= 700
  c2: annual_income >= 50000
  c3: loan_amount <= 500000
  c4: has_collateral
  c5: employed
  c6: bankruptcy_history（帶 not）

特性：
  - 6 個原子條件，MC/DC 需要 12 個獨立對達到 100% 覆蓋率
  - 無共用變數：每個條件使用獨立的輸入欄位
  - 兩個 OR 子群：(c1 or c2) 和 (c3 or c4)
  - 兩個純 bool：c5 AND not c6

適合測試：基本複雜 MC/DC 覆蓋率，無 pairwise feasibility 問題。
"""


def check_loan_approval(
    credit_score: int,
    annual_income: int,
    loan_amount: int,
    employed: bool,
    has_collateral: bool,
    bankruptcy_history: bool,
) -> bool:
    """貸款審核邏輯（k=6，無共用變數）"""
    if (
        (credit_score >= 700 or annual_income >= 50000)
        and (loan_amount <= 500000 or has_collateral)
        and employed
        and not bankruptcy_history
    ):
        return True
    return False
