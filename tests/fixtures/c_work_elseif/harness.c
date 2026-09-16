#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "ifl_probe.h"

int _ifl_test_id;

bool classify(int x);

int main(int argc, char* argv[]) {
    if (argc < 3) return 1;
    _ifl_test_id = atoi(argv[1]);
    int x = (int)atoi(argv[2]);
    classify(x);
    return 0;
}
