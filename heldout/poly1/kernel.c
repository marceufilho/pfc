#include "spec.h"
/* pfc-heldout-8fc1191d50780849ad7cea85bf0cadc8 */

long poly1(long a[], long a_len, long SIZE)
{
    REQUIRES(SIZE > 1 && SIZE < 100000L && SIZE <= a_len);
    long i = 0;
    i = 0;
    while (i < SIZE) {
        INVARIANT(0 <= i && i <= SIZE);
        a[i] = i * i;
        i = i + 1;
    }
    return i;
}
