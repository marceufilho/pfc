#include "spec.h"

long overflow_mul(long x, long y)
{
    REQUIRES(0 <= y);
    return x * y;
}
