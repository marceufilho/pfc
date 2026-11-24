/* pfc checked; requires the declared entry/memory contract and defined C arithmetic. */
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
    long pfc_tmp_1 = 0;
    long i = pfc_tmp_1;
    (void)i;
    while (1) {
        int pfc_tmp_2 = (i < n);
        if (!(pfc_tmp_2)) break;
        if (!(0 <= i && i < x_len)) __builtin_trap();
        long pfc_tmp_3 = x[i];
        long pfc_tmp_4;
        if (__builtin_mul_overflow(alpha, pfc_tmp_3, &pfc_tmp_4)) __builtin_trap();
        if (!(0 <= i && i < y_len)) __builtin_trap();
        long pfc_tmp_5 = y[i];
        long pfc_tmp_6;
        if (__builtin_add_overflow(pfc_tmp_4, pfc_tmp_5, &pfc_tmp_6)) __builtin_trap();
        if (!(0 <= i && i < y_len)) __builtin_trap();
        y[i] = pfc_tmp_6;
        long pfc_tmp_7 = 1;
        long pfc_tmp_8;
        if (__builtin_add_overflow(i, pfc_tmp_7, &pfc_tmp_8)) __builtin_trap();
        i = pfc_tmp_8;
    }
    return n;
}
