
#include <stdbool.h>
bool classify(int x) {
    if (x > 100) {
        return true;
    } else if (x > 50) {
        return true;
    } else if (x > 10) {
        return true;
    } else {
        return false;
    }
}
