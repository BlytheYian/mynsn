#include "ifl_probe.h"
#include <stdbool.h>

/*
 * TCAS SIR alt_sep_test — Alt_Layer_Value 版（C）
 * 對應 Python 版 tcas_sir copy.py
 *
 * 與 tcas_sir.c 的差異：
 *   Positive_RA_Alt_Thresh → Alt_Layer_Value (0-3)，ALIM 由函數內三元式計算
 *   Up_Separation + Climb_Inhibit * 100 → Inhibit_Biased_Climb 由三元式計算
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
    int  Alt_Layer_Value,
    int  Up_Separation,
    int  Down_Separation,
    int  Other_RAC,
    int  Other_Capability,
    int  Climb_Inhibit
) {
    /* ALIM() lookup via chained ternary（三元式決策節點）*/
    int alim = _ifl_probe_ternary("D1", "D1.c1", (Alt_Layer_Value == 0), (400), (Alt_Layer_Value == 1 ? 500 :
               Alt_Layer_Value == 2 ? 640 : 740))
;

    /* Inhibit_Biased_Climb()（三元式決策節點）*/
    int inh_climb = _ifl_probe_ternary("D4", "D4.c1", (Climb_Inhibit != 0), (Up_Separation + 100), (Up_Separation));

    /* D1: enabled && ((tcas_equipped && intent_not_known) || !tcas_equipped) */
    int _D5_c1 = (High_Confidence);
    int _D5_c2 = (Own_Tracked_Alt_Rate <= 600);
    int _D5_c3 = (Cur_Vertical_Sep > 600);
    int _D5_c4 = (Other_Capability == 1);
    int _D5_c5 = (Two_of_Three_Reports_Valid);
    int _D5_c6 = (Other_RAC == 0);
    int _D5_c7 = (Other_Capability != 1);
    _ifl_record_cond("D5.c1", _D5_c1);
    _ifl_record_cond("D5.c2", _D5_c2);
    _ifl_record_cond("D5.c3", _D5_c3);
    _ifl_record_cond("D5.c4", _D5_c4);
    _ifl_record_cond("D5.c5", _D5_c5);
    _ifl_record_cond("D5.c6", _D5_c6);
    _ifl_record_cond("D5.c7", _D5_c7);
    int _D5_decision = (_D5_c1 && (_D5_c2) && (_D5_c3) && ((_D5_c4 && _D5_c5 && _D5_c6) || _D5_c7));
    _ifl_record_decision("D5", _D5_decision);
    if (_D5_decision) {

        /* D2: upward_preferred */
        int _D6_c1 = (inh_climb > Down_Separation);
        _ifl_record_cond("D6.c1", _D6_c1);
        int _D6_decision = (_D6_c1);
        _ifl_record_decision("D6", _D6_decision);
        if (_D6_decision) {

            /* D3: need_upward_RA && need_downward_RA → UNRESOLVED */
            int _D7_c1 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D7_c2 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D7_c3 = (Down_Separation >= alim);
            int _D7_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D7_c5 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D7_c6 = (Cur_Vertical_Sep >= 300);
            int _D7_c7 = (Down_Separation >= alim);
            int _D7_c8 = (Other_Tracked_Alt < Own_Tracked_Alt);
            _ifl_record_cond("D7.c1", _D7_c1);
            _ifl_record_cond("D7.c2", _D7_c2);
            _ifl_record_cond("D7.c3", _D7_c3);
            _ifl_record_cond("D7.c4", _D7_c4);
            _ifl_record_cond("D7.c5", _D7_c5);
            _ifl_record_cond("D7.c6", _D7_c6);
            _ifl_record_cond("D7.c7", _D7_c7);
            _ifl_record_cond("D7.c8", _D7_c8);
            int _D7_decision = (((!(_D7_c1) || ((_D7_c2) && !(_D7_c3))) && (_D7_c4)) && ((_D7_c5) && (_D7_c6) && (_D7_c7) && (_D7_c8)));
            _ifl_record_decision("D7", _D7_decision);
            if (_D7_decision) {
                return 0;
            } else {

                /* D4: need_upward_RA → UPWARD_RA */
                int _D8_c1 = (Own_Tracked_Alt < Other_Tracked_Alt);
                int _D8_c2 = (Own_Tracked_Alt < Other_Tracked_Alt);
                int _D8_c3 = (Down_Separation >= alim);
                int _D8_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
                _ifl_record_cond("D8.c1", _D8_c1);
                _ifl_record_cond("D8.c2", _D8_c2);
                _ifl_record_cond("D8.c3", _D8_c3);
                _ifl_record_cond("D8.c4", _D8_c4);
                int _D8_decision = ((!(_D8_c1) || ((_D8_c2) && !(_D8_c3))) && (_D8_c4));
                _ifl_record_decision("D8", _D8_decision);
                if (_D8_decision) {
                    return 1;
                } else {

                    /* D5: need_downward_RA → DOWNWARD_RA */
                    int _D9_c1 = (Own_Tracked_Alt < Other_Tracked_Alt);
                    int _D9_c2 = (Cur_Vertical_Sep >= 300);
                    int _D9_c3 = (Down_Separation >= alim);
                    int _D9_c4 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    _ifl_record_cond("D9.c1", _D9_c1);
                    _ifl_record_cond("D9.c2", _D9_c2);
                    _ifl_record_cond("D9.c3", _D9_c3);
                    _ifl_record_cond("D9.c4", _D9_c4);
                    int _D9_decision = ((_D9_c1) && (_D9_c2) && (_D9_c3) && (_D9_c4));
                    _ifl_record_decision("D9", _D9_decision);
                    if (_D9_decision) {
                        return 2;
                    } else {
                        return 0;
                    }
                }
            }

        } else {

            /* D6: need_upward_RA && need_downward_RA → UNRESOLVED */
            int _D10_c1 = (Other_Tracked_Alt < Own_Tracked_Alt);
            int _D10_c2 = (Cur_Vertical_Sep >= 300);
            int _D10_c3 = (Up_Separation >= alim);
            int _D10_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
            int _D10_c5 = (Other_Tracked_Alt < Own_Tracked_Alt);
            int _D10_c6 = (Other_Tracked_Alt < Own_Tracked_Alt);
            int _D10_c7 = (Up_Separation >= alim);
            int _D10_c8 = (Other_Tracked_Alt < Own_Tracked_Alt);
            _ifl_record_cond("D10.c1", _D10_c1);
            _ifl_record_cond("D10.c2", _D10_c2);
            _ifl_record_cond("D10.c3", _D10_c3);
            _ifl_record_cond("D10.c4", _D10_c4);
            _ifl_record_cond("D10.c5", _D10_c5);
            _ifl_record_cond("D10.c6", _D10_c6);
            _ifl_record_cond("D10.c7", _D10_c7);
            _ifl_record_cond("D10.c8", _D10_c8);
            int _D10_decision = (((_D10_c1) && (_D10_c2) && (_D10_c3) && (_D10_c4)) && ((!(_D10_c5) || ((_D10_c6) && (_D10_c7))) && (_D10_c8)));
            _ifl_record_decision("D10", _D10_decision);
            if (_D10_decision) {
                return 0;
            } else {

                /* D7: need_upward_RA → UPWARD_RA */
                int _D11_c1 = (Other_Tracked_Alt < Own_Tracked_Alt);
                int _D11_c2 = (Cur_Vertical_Sep >= 300);
                int _D11_c3 = (Up_Separation >= alim);
                int _D11_c4 = (Own_Tracked_Alt < Other_Tracked_Alt);
                _ifl_record_cond("D11.c1", _D11_c1);
                _ifl_record_cond("D11.c2", _D11_c2);
                _ifl_record_cond("D11.c3", _D11_c3);
                _ifl_record_cond("D11.c4", _D11_c4);
                int _D11_decision = ((_D11_c1) && (_D11_c2) && (_D11_c3) && (_D11_c4));
                _ifl_record_decision("D11", _D11_decision);
                if (_D11_decision) {
                    return 1;
                } else {

                    /* D8: need_downward_RA → DOWNWARD_RA */
                    int _D12_c1 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    int _D12_c2 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    int _D12_c3 = (Up_Separation >= alim);
                    int _D12_c4 = (Other_Tracked_Alt < Own_Tracked_Alt);
                    _ifl_record_cond("D12.c1", _D12_c1);
                    _ifl_record_cond("D12.c2", _D12_c2);
                    _ifl_record_cond("D12.c3", _D12_c3);
                    _ifl_record_cond("D12.c4", _D12_c4);
                    int _D12_decision = ((!(_D12_c1) || ((_D12_c2) && (_D12_c3))) && (_D12_c4));
                    _ifl_record_decision("D12", _D12_decision);
                    if (_D12_decision) {
                        return 2;
                    } else {
                        return 0;
                    }
                }
            }
        }
    }

    return 0;
}
