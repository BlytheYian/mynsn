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
    int alim = Alt_Layer_Value == 0 ? 400 :
               Alt_Layer_Value == 1 ? 500 :
               Alt_Layer_Value == 2 ? 640 : 740;

    /* Inhibit_Biased_Climb()（三元式決策節點）*/
    int inh_climb = Climb_Inhibit != 0 ? Up_Separation + 100 : Up_Separation;

    /* D1: enabled && ((tcas_equipped && intent_not_known) || !tcas_equipped) */
    if (High_Confidence
            && (Own_Tracked_Alt_Rate <= 600)
            && (Cur_Vertical_Sep > 600)
            && ((Other_Capability == 1
                  && Two_of_Three_Reports_Valid
                  && Other_RAC == 0)
                 || Other_Capability != 1)) {

        /* D2: upward_preferred */
        if (inh_climb > Down_Separation) {

            /* D3: need_upward_RA && need_downward_RA → UNRESOLVED */
            if (
                ((!(Own_Tracked_Alt < Other_Tracked_Alt)
                  || ((Own_Tracked_Alt < Other_Tracked_Alt)
                      && !(Down_Separation >= alim)))
                 && (Own_Tracked_Alt < Other_Tracked_Alt))
                &&
                ((Own_Tracked_Alt < Other_Tracked_Alt)
                 && (Cur_Vertical_Sep >= 300)
                 && (Down_Separation >= alim)
                 && (Other_Tracked_Alt < Own_Tracked_Alt))
            ) {
                return 0;
            } else {

                /* D4: need_upward_RA → UPWARD_RA */
                if (
                    (!(Own_Tracked_Alt < Other_Tracked_Alt)
                     || ((Own_Tracked_Alt < Other_Tracked_Alt)
                         && !(Down_Separation >= alim)))
                    && (Own_Tracked_Alt < Other_Tracked_Alt)
                ) {
                    return 1;
                } else {

                    /* D5: need_downward_RA → DOWNWARD_RA */
                    if (
                        (Own_Tracked_Alt < Other_Tracked_Alt)
                        && (Cur_Vertical_Sep >= 300)
                        && (Down_Separation >= alim)
                        && (Other_Tracked_Alt < Own_Tracked_Alt)
                    ) {
                        return 2;
                    } else {
                        return 0;
                    }
                }
            }

        } else {

            /* D6: need_upward_RA && need_downward_RA → UNRESOLVED */
            if (
                ((Other_Tracked_Alt < Own_Tracked_Alt)
                 && (Cur_Vertical_Sep >= 300)
                 && (Up_Separation >= alim)
                 && (Own_Tracked_Alt < Other_Tracked_Alt))
                &&
                ((!(Other_Tracked_Alt < Own_Tracked_Alt)
                  || ((Other_Tracked_Alt < Own_Tracked_Alt)
                      && (Up_Separation >= alim)))
                 && (Other_Tracked_Alt < Own_Tracked_Alt))
            ) {
                return 0;
            } else {

                /* D7: need_upward_RA → UPWARD_RA */
                if (
                    (Other_Tracked_Alt < Own_Tracked_Alt)
                    && (Cur_Vertical_Sep >= 300)
                    && (Up_Separation >= alim)
                    && (Own_Tracked_Alt < Other_Tracked_Alt)
                ) {
                    return 1;
                } else {

                    /* D8: need_downward_RA → DOWNWARD_RA */
                    if (
                        (!(Other_Tracked_Alt < Own_Tracked_Alt)
                         || ((Other_Tracked_Alt < Own_Tracked_Alt)
                             && (Up_Separation >= alim)))
                        && (Other_Tracked_Alt < Own_Tracked_Alt)
                    ) {
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
