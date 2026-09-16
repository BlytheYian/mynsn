#include "ifl_probe.h"

#include <stdbool.h>
bool classify(int x) {
    int _D1_c1 = (x > 100);
    _ifl_record_cond("D1.c1", _D1_c1);
    int _D1_decision = (_D1_c1);
    _ifl_record_decision("D1", _D1_decision);
    if (_D1_decision) {
        return true;
    } else {
        int _D2_c1 = (x > 50);
        _ifl_record_cond("D2.c1", _D2_c1);
        int _D2_decision = (_D2_c1);
        _ifl_record_decision("D2", _D2_decision);
        if (_D2_decision) {
        return true;
    } else {
        int _D3_c1 = (x > 10);
        _ifl_record_cond("D3.c1", _D3_c1);
        int _D3_decision = (_D3_c1);
        _ifl_record_decision("D3", _D3_decision);
        if (_D3_decision) {
        return true;
    } else {
        return false;
    }
    }
    }
}
