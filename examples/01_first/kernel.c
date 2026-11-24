#include "spec.h"

long first(const long a[], long a_len)
{
    REQUIRES(a_len > 0);
    return a[0];
}
