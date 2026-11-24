#include "spec.h"

long count_to(long n)
{
    REQUIRES(0 <= n && n <= 100);
    ENSURES(RESULT == n);
    long i = 0;
    while (i < n) {
        INVARIANT(0 <= i && i <= n);
        i = i + 1;
    }
    return i;
}
