#include "spec.h"

long overflow_add(long x, long y)
{
    REQUIRES(0 <= y);
    return x + y;
}
