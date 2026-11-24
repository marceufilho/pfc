#include "spec.h"
/* pfc-heldout-98ef9692b11b805ecffc9b64618c5d5f */

long max_3_n_u(const long A[], long A_len, long m, long n, long p)
{
    REQUIRES(0 < m && 0 < n && 0 < p && m <= A_len / p / n);
    long i = 0;
    long j = 0;
    long k = 0;
    long max = A[0];
    i = 0;
    while (i < m) {
        INVARIANT(0 <= i && i <= m);
        j = 0;
        while (j < n) {
            INVARIANT(0 <= j && j <= n);
            k = 0;
            while (k < p) {
                INVARIANT(0 <= k && k <= p);
                if (A[(i * n + j) * p + k] > max) {
                    max = A[(i * n + j) * p + k];
                }
                k = k + 1;
            }
            j = j + 1;
        }
        i = i + 1;
    }
    return max;
}
