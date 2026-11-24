#include "spec.h"

long zero_prefix(long a[], long a_len, long n)
{
    REQUIRES(0 <= n && n <= a_len);
    ENSURES(RESULT == n);
    for (long i = 0; i < n; i = i + 1) {
        INVARIANT(0 <= i && i <= n);
        a[i] = 0;
    }
    return n;
}
