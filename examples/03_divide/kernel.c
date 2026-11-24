#include "spec.h"

long divide(long numerator, long denominator)
{
    REQUIRES(numerator >= 0);
    REQUIRES(denominator > 0);
    return numerator / denominator;
}
