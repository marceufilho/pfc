#include "spec.h"

long index_through_memory(const long a[], long a_len, const long b[], long b_len, long n, long i)
{
    REQUIRES(0 <= n && n <= a_len && n <= b_len && 0 <= i && i < n);
    long k = a[i];
    return b[k];
}
