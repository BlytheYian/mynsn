#ifndef IFL_PROBE_H
#define IFL_PROBE_H

#include <stdio.h>

/* 由測試 harness 在每次函數呼叫前設定 */
extern int _ifl_test_id;

static inline void _ifl_record_cond(const char* cond_id, int value) {
    fprintf(stdout, "PROBE|%d|%s|%d\n", _ifl_test_id, cond_id, value);
    fflush(stdout);
}

static inline void _ifl_record_decision(const char* decision_id, int value) {
    fprintf(stdout, "PROBE_DEC|%d|%s|%d\n", _ifl_test_id, decision_id, value);
    fflush(stdout);
}

#endif /* IFL_PROBE_H */
