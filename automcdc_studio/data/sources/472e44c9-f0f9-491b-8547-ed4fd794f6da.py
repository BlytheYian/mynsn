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
