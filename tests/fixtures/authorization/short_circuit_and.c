#include "spec.h"

long short_circuit_and(const long a[], long a_len, long n, long i)
{
    REQUIRES(0 <= n && n <= a_len);
    if (i < n && a[i] > 0) {
        return 1;
    }
    return 0;
}
