/* pfc verified; requires the declared entry/memory contract and defined C arithmetic. */
/* Overflow traps guard + - * and unary - unless their obligation is proved; division guards cover 0 and MIN/-1; verified checks pure-scalar REQUIRES at entry. Proof citations authorize only their individual guards. */
#ifndef __clang__
#error "pfc output requires clang"
#endif
_Static_assert(__CHAR_BIT__ == 8 && sizeof(int) * 8 == 32 && sizeof(long) * 8 == 64, "pfc target widths mismatch");
_Static_assert(__INT_MAX__ == 2147483647 && __LONG_MAX__ == 9223372036854775807L, "pfc target ranges mismatch");

long saxpy(long alpha, const long x[], long x_len, long y[], long y_len, long n)
{
    (void)alpha;
    (void)x;
    (void)x_len;
    (void)y;
    (void)y_len;
    (void)n;
    // entry check 1: REQUIRES kernels/saxpy/kernel.c:5:5 conjunct 1
    _Bool pfc_entry_1 = ((long)0 <= n);
    if (!pfc_entry_1) __builtin_trap();
    // entry check 2: REQUIRES kernels/saxpy/kernel.c:5:5 conjunct 2
    _Bool pfc_entry_2 = (n <= x_len);
    if (!pfc_entry_2) __builtin_trap();
    // entry check 3: REQUIRES kernels/saxpy/kernel.c:5:5 conjunct 3
    _Bool pfc_entry_3 = (n <= y_len);
    if (!pfc_entry_3) __builtin_trap();
    // entry check skipped: REQUIRES kernels/saxpy/kernel.c:6:5 conjunct 1 (separation predicate has no C evaluation)
    long pfc_tmp_1 = 0;
    long i = pfc_tmp_1;
    (void)i;
    while (1) {
        int pfc_tmp_2 = (i < n);
        if (!(pfc_tmp_2)) break;
        // proved: obligation bounds:f8407e60a8e0021642740d7224b00c13edc5e7bbfd08dd9d76b6be9ce525b35f
        long pfc_tmp_3 = x[i];
        long pfc_tmp_4;
        if (__builtin_mul_overflow(alpha, pfc_tmp_3, &pfc_tmp_4)) __builtin_trap();
        // proved: obligation bounds:6320224256badeede76305a881314bf3cfa8dcca762a01ee8533e71a6e41a22c
        long pfc_tmp_5 = y[i];
        long pfc_tmp_6;
        if (__builtin_add_overflow(pfc_tmp_4, pfc_tmp_5, &pfc_tmp_6)) __builtin_trap();
        // proved: obligation bounds:c278ea2f8e9d021e7e96a1824413cafcc7a24af4df8fe8b4ef4ead4a1f39bae1
        y[i] = pfc_tmp_6;
        long pfc_tmp_7 = 1;
        // proved: obligation overflow:fe80bca5255f9583de6a120fa0f71ac1d9bfe46a0893b5ae71f0da0a95b0db46
        long pfc_tmp_8 = (i + pfc_tmp_7);
        i = pfc_tmp_8;
    }
    return n;
}
