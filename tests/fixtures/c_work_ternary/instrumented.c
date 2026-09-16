#include "ifl_probe.h"

#include <stdbool.h>
int classify(int layer, int sep) {
    int alim = _ifl_probe_ternary("D1", "D1.c1", (layer == 0), (400), (layer == 1 ? 500 : 640));
    int _D3_c1 = (sep >= alim);
    _ifl_record_cond("D3.c1", _D3_c1);
    int _D3_decision = (_D3_c1);
    _ifl_record_decision("D3", _D3_decision);
    if (_D3_decision) {
        return 1;
    }
    return 0;
}
