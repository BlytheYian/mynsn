#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

int classify(int layer, int sep);

int main(int argc, char* argv[]) {
    if (argc < 4) return 1;
    _ifl_test_id = atoi(argv[1]);
    int layer = (int)atoi(argv[2]);
    int sep = (int)atoi(argv[3]);
    classify(layer, sep);
    return 0;
}
