/* pfc checked; requires the declared entry/memory contract and defined C arithmetic. */
/* Overflow traps guard + - * and unary - unless their obligation is proved; division guards cover 0 and MIN/-1; verified checks pure-scalar REQUIRES at entry. Proof citations authorize only their individual guards. */
#ifndef __clang__
#error "pfc output requires clang"
#endif
_Static_assert(__CHAR_BIT__ == 8 && sizeof(int) * 8 == 32 && sizeof(long) * 8 == 64, "pfc target widths mismatch");
_Static_assert(__INT_MAX__ == 2147483647 && __LONG_MAX__ == 9223372036854775807L, "pfc target ranges mismatch");

long dot(const long a[], long a_len, const long b[], long b_len, long n)
{
    (void)a;
    (void)a_len;
    (void)b;
    (void)b_len;
    (void)n;
    long pfc_tmp_1 = 0;
    long s = pfc_tmp_1;
    (void)s;
    long pfc_tmp_2 = 0;
    long i = pfc_tmp_2;
    (void)i;
    while (1) {
        int pfc_tmp_3 = (i < n);
        if (!(pfc_tmp_3)) break;
        if (!(0 <= i && i < a_len)) __builtin_trap();
        long pfc_tmp_4 = a[i];
        if (!(0 <= i && i < b_len)) __builtin_trap();
        long pfc_tmp_5 = b[i];
        long pfc_tmp_6;
        if (__builtin_mul_overflow(pfc_tmp_4, pfc_tmp_5, &pfc_tmp_6)) __builtin_trap();
        long pfc_tmp_7;
        if (__builtin_add_overflow(s, pfc_tmp_6, &pfc_tmp_7)) __builtin_trap();
        s = pfc_tmp_7;
        long pfc_tmp_8 = 1;
        long pfc_tmp_9;
        if (__builtin_add_overflow(i, pfc_tmp_8, &pfc_tmp_9)) __builtin_trap();
        i = pfc_tmp_9;
    }
    return s;
}
