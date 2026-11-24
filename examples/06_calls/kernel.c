#include "spec.h"

long increment(long x)
{
    REQUIRES(0 <= x && x < 10);
    ENSURES(RESULT == x + 1);
    return x + 1;
}

long increment_twice(long x)
{
    REQUIRES(0 <= x && x < 9);
    ENSURES(RESULT == x + 2);
    long y = increment(x);
    return increment(y);
}
