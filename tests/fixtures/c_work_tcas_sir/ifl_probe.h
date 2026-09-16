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

/* 三元式（? :）專用探針：記錄條件值並回傳對應分支的值 */
static inline int _ifl_probe_ternary(
        const char* decision_id, const char* cond_id,
        int cond_val, int true_val, int false_val) {
    _ifl_record_cond(cond_id, cond_val);
    _ifl_record_decision(decision_id, cond_val);
    return cond_val ? true_val : false_val;
}

#endif /* IFL_PROBE_H */
