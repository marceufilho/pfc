#include "spec.h"

long write_then_trap(long a[], long a_len, long x)
{
    REQUIRES(2 <= a_len);
    a[0] = 7;
    long y = x + 1;
    a[1] = y;
    return y;
}
