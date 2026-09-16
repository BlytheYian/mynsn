#include "ifl_probe.h"
#include <stdbool.h>

/*
 * TCAS SIR alt_sep_test（C 版）
 * 對應 Python 版 tcas_sir.py（Positive_RA_Alt_Thresh 直接傳入版）
 *
 * Inlining map:
 *   ALIM()                 -> Positive_RA_Alt_Thresh（呼叫方預先計算）
 *   Inhibit_Biased_Climb() -> Up_Separation + Climb_Inhibit * 100
 *                            （Climb_Inhibit ∈ {0,1}，算術等價於三元式）
 *   Own_Below_Threat()     -> Own_Tracked_Alt < Other_Tracked_Alt
 *   Own_Above_Threat()     -> Other_Tracked_Alt < Own_Tracked_Alt
 *
 * 注意：使用明確的 else { if {} } 巢狀結構（不用 else if），
 *       確保 C 探針注入器可在每個判斷前正確插入探針變數宣告。
 *
 * Returns: 0=UNRESOLVED, 1=UPWARD_RA, 2=DOWNWARD_RA
 */
int alt_sep_test(
    int  Cur_Vertical_Sep,
    bool High_Confidence,
    bool Two_of_Three_Reports_Valid,
    int  Own_Tracked_Alt,
    int  Own_Tracked_Alt_Rate,
    int  Other_Tracked_Alt,
    int  Positive_RA_Alt_Thresh,
    int  Up_Separation,
    int  Down_Separation,
    int  Other_RAC,
    int  Other_Capability,
    int  Climb_Inhibit
) {
    /* D1: enabled && ((tcas_equipped && intent_not_known) || !tcas_equipped) */
    int _D1_c1 = (High_Confidence);
    int _D1_c2 = (Own_Tracked_Alt_Rate <= 600);
    int _D1_c3 = (Cur_Vertical_Sep > 600);
    int _D1_c4 = (Other_Capability == 1);
    int _D1_c5 = (Two_of_Three_Reports_Valid);
    int _D1_c6 = (Other_RAC == 0);
    int _D1_c7 = (Other_Capability != 1);
    _ifl_record_cond("D1.c1", _D1_c1);
    _ifl_record_cond("D1.c2", _D1_c2);
    _ifl_record_cond("D1.c3", _D1_c3);
    _ifl_record_cond("D1.c4", _D1_c4);
    _ifl_record_cond("D1.c5", _D1_c5);
    _ifl_record_cond("D1.c6", _D1_c6);
    _ifl_record_cond("D1.c7", _D1_c7);
    int _D1_decision = (_D1_c1 && (_D1_c2) && (_D1_c3) && ((_D1_c4 && _D1_c5 && _D1_c6) || _D1_c7));
    _ifl_record_decision("D1", _D1_decision);
    if (_D1_decision) {

        /* D2: upward_preferred = Inhibit_Biased_Climb() > Down_Separation */
        int _D2_c1 = ((Up_Separation + Climb_Inhibit * 100) > Down_Separation);
        _ifl_record_cond("D2.c1", _D2_c1);
        int _D2_decision = (_D2_c1);
        _ifl_record_decision("D2", _D2_decision);
        if (_D2_decision) {

            /* upward_preferred == true */

            /* D3: need_upward_RA && need_downward_RA → UNRESOLVED */
            int _D3_c1 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D3_c2 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D3_c3 = (Down_Separation >= Positive_RA_Alt_Thresh);
            int _D3_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D3_c5 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D3_c6 = (Cur_Vertical_Sep >= 300);
            int _D3_c7 = (Down_Separation >= Positive_RA_Alt_Thresh);
            int _D3_c8 = (Other_Tracked_Alt < Own_Tracked_Alt);
            _ifl_record_cond("D3.c1", _D3_c1);
            _ifl_record_cond("D3.c2", _D3_c2);
            _ifl_record_cond("D3.c3", _D3_c3);
            _ifl_record_cond("D3.c4", _D3_c4);
            _ifl_record_cond("D3.c5", _D3_c5);
            _ifl_record_cond("D3.c6", _D3_c6);
            _ifl_record_cond("D3.c7", _D3_c7);
            _ifl_record_cond("D3.c8", _D3_c8);
            int _D3_decision = (((!(_D3_c1) || ((_D3_c2) && !(_D3_c3))) && (_D3_c4)) && ((_D3_c5) && (_D3_c6) && (_D3_c7) && (_D3_c8)));
            _ifl_record_decision("D3", _D3_decision);
            if (_D3_decision) {
                return 0; /* UNRESOLVED */
            } else {

                /* D4: need_upward_RA → UPWARD_RA */
                int _D4_c1 = (Own_Tracked_Alt < Other_Tracked_Alt);
                int _D4_c2 = (Own_Tracked_Alt < Other_Tracked_Alt);
                int _D4_c3 = (Down_Separation >= Positive_RA_Alt_Thresh);
                int _D4_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
                _ifl_record_cond("D4.c1", _D4_c1);
                _ifl_record_cond("D4.c2", _D4_c2);
                _ifl_record_cond("D4.c3", _D4_c3);
                _ifl_record_cond("D4.c4", _D4_c4);
                int _D4_decision = ((!(_D4_c1) || ((_D4_c2) && !(_D4_c3))) && (_D4_c4));
                _ifl_record_decision("D4", _D4_decision);
                if (_D4_decision) {
                    return 1; /* UPWARD_RA */
                } else {

                    /* D5: need_downward_RA → DOWNWARD_RA */
                    int _D5_c1 = (Own_Tracked_Alt < Other_Tracked_Alt);
                    int _D5_c2 = (Cur_Vertical_Sep >= 300);
                    int _D5_c3 = (Down_Separation >= Positive_RA_Alt_Thresh);
                    int _D5_c4 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    _ifl_record_cond("D5.c1", _D5_c1);
                    _ifl_record_cond("D5.c2", _D5_c2);
                    _ifl_record_cond("D5.c3", _D5_c3);
                    _ifl_record_cond("D5.c4", _D5_c4);
                    int _D5_decision = ((_D5_c1) && (_D5_c2) && (_D5_c3) && (_D5_c4));
                    _ifl_record_decision("D5", _D5_decision);
                    if (_D5_decision) {
                        return 2; /* DOWNWARD_RA */
                    } else {
                        return 0; /* UNRESOLVED */
                    }
                }
            }

        } else {

            /* upward_preferred == false */

            /* D6: need_upward_RA && need_downward_RA → UNRESOLVED */
            int _D6_c1 = (Other_Tracked_Alt < Own_Tracked_Alt);
            int _D6_c2 = (Cur_Vertical_Sep >= 300);
            int _D6_c3 = (Up_Separation >= Positive_RA_Alt_Thresh);
            int _D6_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D6_c5 = (Other_Tracked_Alt < Own_Tracked_Alt);
            int _D6_c6 = (Other_Tracked_Alt < Own_Tracked_Alt);
            int _D6_c7 = (Up_Separation >= Positive_RA_Alt_Thresh);
            int _D6_c8 = (Other_Tracked_Alt < Own_Tracked_Alt);
            _ifl_record_cond("D6.c1", _D6_c1);
            _ifl_record_cond("D6.c2", _D6_c2);
            _ifl_record_cond("D6.c3", _D6_c3);
            _ifl_record_cond("D6.c4", _D6_c4);
            _ifl_record_cond("D6.c5", _D6_c5);
            _ifl_record_cond("D6.c6", _D6_c6);
            _ifl_record_cond("D6.c7", _D6_c7);
            _ifl_record_cond("D6.c8", _D6_c8);
            int _D6_decision = (((_D6_c1) && (_D6_c2) && (_D6_c3) && (_D6_c4)) && ((!(_D6_c5) || ((_D6_c6) && (_D6_c7))) && (_D6_c8)));
            _ifl_record_decision("D6", _D6_decision);
            if (_D6_decision) {
                return 0; /* UNRESOLVED */
            } else {

                /* D7: need_upward_RA → UPWARD_RA */
                int _D7_c1 = (Other_Tracked_Alt < Own_Tracked_Alt);
                int _D7_c2 = (Cur_Vertical_Sep >= 300);
                int _D7_c3 = (Up_Separation >= Positive_RA_Alt_Thresh);
                int _D7_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
                _ifl_record_cond("D7.c1", _D7_c1);
                _ifl_record_cond("D7.c2", _D7_c2);
                _ifl_record_cond("D7.c3", _D7_c3);
                _ifl_record_cond("D7.c4", _D7_c4);
                int _D7_decision = ((_D7_c1) && (_D7_c2) && (_D7_c3) && (_D7_c4));
                _ifl_record_decision("D7", _D7_decision);
                if (_D7_decision) {
                    return 1; /* UPWARD_RA */
                } else {

                    /* D8: need_downward_RA → DOWNWARD_RA */
                    int _D8_c1 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    int _D8_c2 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    int _D8_c3 = (Up_Separation >= Positive_RA_Alt_Thresh);
                    int _D8_c4 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    _ifl_record_cond("D8.c1", _D8_c1);
                    _ifl_record_cond("D8.c2", _D8_c2);
                    _ifl_record_cond("D8.c3", _D8_c3);
                    _ifl_record_cond("D8.c4", _D8_c4);
                    int _D8_decision = ((!(_D8_c1) || ((_D8_c2) && (_D8_c3))) && (_D8_c4));
                    _ifl_record_decision("D8", _D8_decision);
                    if (_D8_decision) {
                        return 2; /* DOWNWARD_RA */
                    } else {
                        return 0; /* UNRESOLVED */
                    }
                }
            }
        }
    }

    return 0; /* UNRESOLVED (not enabled) */
}
