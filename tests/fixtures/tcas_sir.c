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
    if (High_Confidence
            && (Own_Tracked_Alt_Rate <= 600)
            && (Cur_Vertical_Sep > 600)
            && ((Other_Capability == 1
                  && Two_of_Three_Reports_Valid
                  && Other_RAC == 0)
                 || Other_Capability != 1)) {

        /* D2: upward_preferred = Inhibit_Biased_Climb() > Down_Separation */
        if ((Up_Separation + Climb_Inhibit * 100) > Down_Separation) {

            /* upward_preferred == true */

            /* D3: need_upward_RA && need_downward_RA → UNRESOLVED */
            if (
                ((!(Own_Tracked_Alt < Other_Tracked_Alt)
                  || ((Own_Tracked_Alt < Other_Tracked_Alt)
                      && !(Down_Separation >= Positive_RA_Alt_Thresh)))
                 && (Own_Tracked_Alt < Other_Tracked_Alt))
                &&
                ((Own_Tracked_Alt < Other_Tracked_Alt)
                 && (Cur_Vertical_Sep >= 300)
                 && (Down_Separation >= Positive_RA_Alt_Thresh)
                 && (Other_Tracked_Alt < Own_Tracked_Alt))
            ) {
                return 0; /* UNRESOLVED */
            } else {

                /* D4: need_upward_RA → UPWARD_RA */
                if (
                    (!(Own_Tracked_Alt < Other_Tracked_Alt)
                     || ((Own_Tracked_Alt < Other_Tracked_Alt)
                         && !(Down_Separation >= Positive_RA_Alt_Thresh)))
                    && (Own_Tracked_Alt < Other_Tracked_Alt)
                ) {
                    return 1; /* UPWARD_RA */
                } else {

                    /* D5: need_downward_RA → DOWNWARD_RA */
                    if (
                        (Own_Tracked_Alt < Other_Tracked_Alt)
                        && (Cur_Vertical_Sep >= 300)
                        && (Down_Separation >= Positive_RA_Alt_Thresh)
                        && (Other_Tracked_Alt < Own_Tracked_Alt)
                    ) {
                        return 2; /* DOWNWARD_RA */
                    } else {
                        return 0; /* UNRESOLVED */
                    }
                }
            }

        } else {

            /* upward_preferred == false */

            /* D6: need_upward_RA && need_downward_RA → UNRESOLVED */
            if (
                ((Other_Tracked_Alt < Own_Tracked_Alt)
                 && (Cur_Vertical_Sep >= 300)
                 && (Up_Separation >= Positive_RA_Alt_Thresh)
                 && (Own_Tracked_Alt < Other_Tracked_Alt))
                &&
                ((!(Other_Tracked_Alt < Own_Tracked_Alt)
                  || ((Other_Tracked_Alt < Own_Tracked_Alt)
                      && (Up_Separation >= Positive_RA_Alt_Thresh)))
                 && (Other_Tracked_Alt < Own_Tracked_Alt))
            ) {
                return 0; /* UNRESOLVED */
            } else {

                /* D7: need_upward_RA → UPWARD_RA */
                if (
                    (Other_Tracked_Alt < Own_Tracked_Alt)
                    && (Cur_Vertical_Sep >= 300)
                    && (Up_Separation >= Positive_RA_Alt_Thresh)
                    && (Own_Tracked_Alt < Other_Tracked_Alt)
                ) {
                    return 1; /* UPWARD_RA */
                } else {

                    /* D8: need_downward_RA → DOWNWARD_RA */
                    if (
                        (!(Other_Tracked_Alt < Own_Tracked_Alt)
                         || ((Other_Tracked_Alt < Own_Tracked_Alt)
                             && (Up_Separation >= Positive_RA_Alt_Thresh)))
                        && (Other_Tracked_Alt < Own_Tracked_Alt)
                    ) {
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
