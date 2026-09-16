def alt_sep_test(
    Cur_Vertical_Sep: int,
    High_Confidence: bool,
    Two_of_Three_Reports_Valid: bool,
    Own_Tracked_Alt: int,
    Own_Tracked_Alt_Rate: int,
    Other_Tracked_Alt: int,
    Alt_Layer_Value: int,
    Up_Separation: int,
    Down_Separation: int,
    Other_RAC: int,
    Other_Capability: int, 
    Climb_Inhibit: bool
) -> int:
    """
    攤平版 (Flattened/Inlined) TCAS 主函數。
    所有輔助函數與中間變數皆已展開為直接的輸入參數比較，
    完美相容於不具備 IPA (跨函數分析) 的 AST-to-SMT 解析器。
    """
    
    # ==========================================================
    # 1. 信心度檢查 (將 is_confident 展開)
    # ==========================================================
    if not (High_Confidence and Two_of_Three_Reports_Valid):
        return 0

    # ==========================================================
    # 2. 決策 A：需要爬升 (Upward RA)
    # 將 Non_Crossing_Biased_Climb 與 Own_Below_Threat 完全攤平
    # ==========================================================
    if (((Own_Tracked_Alt_Rate <= 600) and (Cur_Vertical_Sep > 600) and (Alt_Layer_Value >= 1)) and 
        (Own_Tracked_Alt < Other_Tracked_Alt) and 
        (Up_Separation >= 300) and 
        not Climb_Inhibit):
        return 1
        
    # ==========================================================
    # 3. 決策 B：需要下降 (Downward RA)
    # 將 Non_Crossing_Biased_Descend 與 Own_Above_Threat 完全攤平
    # ==========================================================
    elif (not ((Own_Tracked_Alt_Rate <= 600) and (Cur_Vertical_Sep > 600) and (Alt_Layer_Value >= 1)) and 
          (Other_Tracked_Alt < Own_Tracked_Alt) and 
          (Down_Separation >= 300) and 
          (Other_RAC == 0)):
        return 2
        
    # 4. 保持原狀 (Unresolved)
    else:
        return 0