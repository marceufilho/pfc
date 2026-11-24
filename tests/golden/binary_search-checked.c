/* pfc checked; requires the declared entry/memory contract and defined C arithmetic. */
/* Overflow traps guard + - * and unary - unless their obligation is proved; division guards cover 0 and MIN/-1; verified checks pure-scalar REQUIRES at entry. Proof citations authorize only their individual guards. */
#ifndef __clang__
#error "pfc output requires clang"
#endif
_Static_assert(__CHAR_BIT__ == 8 && sizeof(int) * 8 == 32 && sizeof(long) * 8 == 64, "pfc target widths mismatch");
_Static_assert(__INT_MAX__ == 2147483647 && __LONG_MAX__ == 9223372036854775807L, "pfc target ranges mismatch");

long binary_search(const long a[], long a_len, long n, long needle)
{
    (void)a;
    (void)a_len;
    (void)n;
    (void)needle;
    long pfc_tmp_1 = 0;
    long lo = pfc_tmp_1;
    (void)lo;
    long pfc_tmp_2 = 1;
    long pfc_tmp_3;
    if (__builtin_sub_overflow(n, pfc_tmp_2, &pfc_tmp_3)) __builtin_trap();
    long hi = pfc_tmp_3;
    (void)hi;
    while (1) {
        int pfc_tmp_4 = (lo <= hi);
        if (!(pfc_tmp_4)) break;
        long pfc_tmp_5;
        if (__builtin_sub_overflow(hi, lo, &pfc_tmp_5)) __builtin_trap();
        long pfc_tmp_6 = 2;
        if (!(pfc_tmp_6 != 0 && !(pfc_tmp_5 == (-9223372036854775807L - 1L) && pfc_tmp_6 == -1))) __builtin_trap();
        long pfc_tmp_7 = (pfc_tmp_5 / pfc_tmp_6);
        long pfc_tmp_8;
        if (__builtin_add_overflow(lo, pfc_tmp_7, &pfc_tmp_8)) __builtin_trap();
        long mid = pfc_tmp_8;
        (void)mid;
        if (!(0 <= mid && mid < a_len)) __builtin_trap();
        long pfc_tmp_9 = a[mid];
        long value = pfc_tmp_9;
        (void)value;
        int pfc_tmp_10 = (value < needle);
        if (pfc_tmp_10) {
            long pfc_tmp_11 = 1;
            long pfc_tmp_12;
            if (__builtin_add_overflow(mid, pfc_tmp_11, &pfc_tmp_12)) __builtin_trap();
            lo = pfc_tmp_12;
        } else {
            int pfc_tmp_13 = (needle < value);
            if (pfc_tmp_13) {
                long pfc_tmp_14 = 1;
                long pfc_tmp_15;
                if (__builtin_sub_overflow(mid, pfc_tmp_14, &pfc_tmp_15)) __builtin_trap();
                hi = pfc_tmp_15;
            } else {
                return mid;
            }
        }
    }
    int pfc_tmp_16 = (-1);
    long pfc_tmp_17 = pfc_tmp_16;
    return pfc_tmp_17;
}
