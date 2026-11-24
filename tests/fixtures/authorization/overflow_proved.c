#include "spec.h"

long overflow_proved(long x, long y)
{
    REQUIRES(0 <= x && x < 1000 && 0 <= y && y < 1000);
    return x + y;
}
