
#include <stdbool.h>
int classify(int layer, int sep) {
    int alim = layer == 0 ? 400 : layer == 1 ? 500 : 640;
    if (sep >= alim) {
        return 1;
    }
    return 0;
}
