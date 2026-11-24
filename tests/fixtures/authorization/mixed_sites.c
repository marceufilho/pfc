#include "spec.h"

long mixed_sites(const long a[], long a_len, long n, long i)
{
    REQUIRES(0 <= n && n <= a_len && 0 <= i && i < n);
    return a[a[i]];
}
