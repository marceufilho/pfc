#include "spec.h"

long branch_one_proved(const long a[], long a_len, long n, long i, long j)
{
    REQUIRES(0 <= n && n <= a_len && 0 <= i && i < n);
    long s = a[i];
    if (j < n) {
        s = a[j];
    }
    return s;
}
