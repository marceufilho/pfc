#include "spec.h"

long overflow_neg_min(long x)
{
    REQUIRES(x <= 0);
    return -x;
}
