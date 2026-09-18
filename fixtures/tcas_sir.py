def alt_sep_test(
    Cur_Vertical_Sep: int,          # int Cur_Vertical_Sep;  (global)
    High_Confidence: bool,          # bool High_Confidence;  (global)
    Two_of_Three_Reports_Valid: bool,  # bool Two_of_Three_Reports_Valid; (global)
    Own_Tracked_Alt: int,           # int Own_Tracked_Alt;   (global)
    Own_Tracked_Alt_Rate: int,      # int Own_Tracked_Alt_Rate; (global)
    Other_Tracked_Alt: int,         # int Other_Tracked_Alt; (global)
    Positive_RA_Alt_Thresh: int,    # int Positive_RA_Alt_Thresh; (global, one of {400, 500, 640, 740})
    Up_Separation: int,             # int Up_Separation;     (global)
    Down_Separation: int,           # int Down_Separation;   (global)
    Other_RAC: int,                 # int Other_RAC;         (global)
    Other_Capability: int,          # int Other_Capability;  (global)
    Climb_Inhibit: int,             # int Climb_Inhibit;     (global, 0 or 1)
) -> int:
    """
    Fully inlined alt_sep_test() from Siemens/SIR TCAS benchmark.
    Returns: 0=UNRESOLVED, 1=UPWARD_RA, 2=DOWNWARD_RA

    Inlining map:
      ALIM()                 -> Positive_RA_Alt_Thresh  (one of {400, 500, 640, 740})
      Inhibit_Biased_Climb() -> (Up_Separation + Climb_Inhibit * 100)
      Own_Below_Threat()     -> Own_Tracked_Alt < Other_Tracked_Alt
      Own_Above_Threat()     -> Other_Tracked_Alt < Own_Tracked_Alt
    """
    # -- enabled = High_Confidence
    # --         && (Own_Tracked_Alt_Rate <= OLEV)
    # --         && (Cur_Vertical_Sep > MAXALTDIFF);
    # -- tcas_equipped = Other_Capability == TCAS_TA;
    # -- intent_not_known = Two_of_Three_Reports_Valid && Other_RAC == NO_INTENT;
    # -- if (enabled && ((tcas_equipped && intent_not_known) || !tcas_equipped))
    if (High_Confidence                                          # enabled: High_Confidence
            and (Own_Tracked_Alt_Rate <= 600)                   # enabled: Own_Tracked_Alt_Rate <= OLEV(600)
            and (Cur_Vertical_Sep > 600)                        # enabled: Cur_Vertical_Sep > MAXALTDIFF(600)
            and ((Other_Capability == 1                         # tcas_equipped: Other_Capability == TCAS_TA(1)
                  and Two_of_Three_Reports_Valid                # intent_not_known: Two_of_Three_Reports_Valid
                  and Other_RAC == 0)                           # intent_not_known: Other_RAC == NO_INTENT(0)
                 or Other_Capability != 1)):                    # !tcas_equipped

        # -- upward_preferred = Inhibit_Biased_Climb() > Down_Separation;
        # -- Inhibit_Biased_Climb(): return (Climb_Inhibit ? Up_Separation + NOZCROSS : Up_Separation)
        if (Up_Separation + Climb_Inhibit * 100) > Down_Separation:  # upward_preferred

            # upward_preferred == True
            # -- Non_Crossing_Biased_Climb() upward branch:
            # --   result = !(Own_Below_Threat())
            # --         || (Own_Below_Threat() && !(Down_Separation >= ALIM()))
            # -- need_upward_RA = Non_Crossing_Biased_Climb() && Own_Below_Threat()
            # -- Non_Crossing_Biased_Descend() upward branch:
            # --   result = Own_Below_Threat() && (Cur_Vertical_Sep >= MINSEP) && (Down_Separation >= ALIM())
            # -- need_downward_RA = Non_Crossing_Biased_Descend() && Own_Above_Threat()
            # -- if (need_upward_RA && need_downward_RA)
            if (
                    ((not (Own_Tracked_Alt < Other_Tracked_Alt)                             # NCBC_up: !(Own_Below_Threat())
                      or ((Own_Tracked_Alt < Other_Tracked_Alt)                             # NCBC_up: Own_Below_Threat()
                          and not (Down_Separation >= Positive_RA_Alt_Thresh)))             # NCBC_up: !(Down_Separation >= ALIM())
                     and (Own_Tracked_Alt < Other_Tracked_Alt))                             # need_upward_RA: && Own_Below_Threat()
                    and
                    ((Own_Tracked_Alt < Other_Tracked_Alt)                                  # NCBD_up: Own_Below_Threat()
                     and (Cur_Vertical_Sep >= 300)                                          # NCBD_up: Cur_Vertical_Sep >= MINSEP(300)
                     and (Down_Separation >= Positive_RA_Alt_Thresh)                        # NCBD_up: Down_Separation >= ALIM()
                     and (Other_Tracked_Alt < Own_Tracked_Alt))                             # need_downward_RA: && Own_Above_Threat()
            ):
                return 0  # alt_sep = UNRESOLVED (need_upward_RA && need_downward_RA)

            # -- else if (need_upward_RA)
            elif (
                    (not (Own_Tracked_Alt < Other_Tracked_Alt)                              # NCBC_up: !(Own_Below_Threat())
                     or ((Own_Tracked_Alt < Other_Tracked_Alt)                              # NCBC_up: Own_Below_Threat()
                         and not (Down_Separation >= Positive_RA_Alt_Thresh)))              # NCBC_up: !(Down_Separation >= ALIM())
                    and (Own_Tracked_Alt < Other_Tracked_Alt)                               # need_upward_RA: && Own_Below_Threat()
            ):
                return 1  # alt_sep = UPWARD_RA

            # -- else if (need_downward_RA)
            elif (
                    (Own_Tracked_Alt < Other_Tracked_Alt)                                   # NCBD_up: Own_Below_Threat()
                    and (Cur_Vertical_Sep >= 300)                                           # NCBD_up: Cur_Vertical_Sep >= MINSEP(300)
                    and (Down_Separation >= Positive_RA_Alt_Thresh)                         # NCBD_up: Down_Separation >= ALIM()
                    and (Other_Tracked_Alt < Own_Tracked_Alt)                               # need_downward_RA: && Own_Above_Threat()
            ):
                return 2  # alt_sep = DOWNWARD_RA

            else:
                return 0  # alt_sep = UNRESOLVED

        else:
            # upward_preferred == False
            # -- Non_Crossing_Biased_Climb() downward branch:
            # --   result = Own_Above_Threat() && (Cur_Vertical_Sep >= MINSEP) && (Up_Separation >= ALIM())
            # -- need_upward_RA = Non_Crossing_Biased_Climb() && Own_Below_Threat()
            # -- Non_Crossing_Biased_Descend() downward branch:
            # --   result = !(Own_Above_Threat()) || (Own_Above_Threat() && (Up_Separation >= ALIM()))
            # -- need_downward_RA = Non_Crossing_Biased_Descend() && Own_Above_Threat()
            # -- if (need_upward_RA && need_downward_RA)
            if (
                    ((Other_Tracked_Alt < Own_Tracked_Alt)                                  # NCBC_down: Own_Above_Threat()
                     and (Cur_Vertical_Sep >= 300)                                          # NCBC_down: Cur_Vertical_Sep >= MINSEP(300)
                     and (Up_Separation >= Positive_RA_Alt_Thresh)                          # NCBC_down: Up_Separation >= ALIM()
                     and (Own_Tracked_Alt < Other_Tracked_Alt))                             # need_upward_RA: && Own_Below_Threat()
                    and
                    ((not (Other_Tracked_Alt < Own_Tracked_Alt)                             # NCBD_down: !(Own_Above_Threat())
                      or ((Other_Tracked_Alt < Own_Tracked_Alt)                             # NCBD_down: Own_Above_Threat()
                          and (Up_Separation >= Positive_RA_Alt_Thresh)))                   # NCBD_down: Up_Separation >= ALIM()
                     and (Other_Tracked_Alt < Own_Tracked_Alt))                             # need_downward_RA: && Own_Above_Threat()
            ):
                return 0  # alt_sep = UNRESOLVED (need_upward_RA && need_downward_RA)

            # -- else if (need_upward_RA)
            elif (
                    (Other_Tracked_Alt < Own_Tracked_Alt)                                   # NCBC_down: Own_Above_Threat()
                    and (Cur_Vertical_Sep >= 300)                                           # NCBC_down: Cur_Vertical_Sep >= MINSEP(300)
                    and (Up_Separation >= Positive_RA_Alt_Thresh)                           # NCBC_down: Up_Separation >= ALIM()
                    and (Own_Tracked_Alt < Other_Tracked_Alt)                               # need_upward_RA: && Own_Below_Threat()
            ):
                return 1  # alt_sep = UPWARD_RA

            # -- else if (need_downward_RA)
            elif (
                    (not (Other_Tracked_Alt < Own_Tracked_Alt)                              # NCBD_down: !(Own_Above_Threat())
                     or ((Other_Tracked_Alt < Own_Tracked_Alt)                              # NCBD_down: Own_Above_Threat()
                         and (Up_Separation >= Positive_RA_Alt_Thresh)))                    # NCBD_down: Up_Separation >= ALIM()
                    and (Other_Tracked_Alt < Own_Tracked_Alt)                               # need_downward_RA: && Own_Above_Threat()
            ):
                return 2  # alt_sep = DOWNWARD_RA

            else:
                return 0  # alt_sep = UNRESOLVED

    return 0  # alt_sep = UNRESOLVED (not enabled)
