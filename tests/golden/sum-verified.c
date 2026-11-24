/* pfc verified; requires the declared entry/memory contract and defined C arithmetic. */
/* Overflow traps guard + - * and unary - unless their obligation is proved; division guards cover 0 and MIN/-1; verified checks pure-scalar REQUIRES at entry. Proof citations authorize only their individual guards. */
#ifndef __clang__
#error "pfc output requires clang"
#endif
_Static_assert(__CHAR_BIT__ == 8 && sizeof(int) * 8 == 32 && sizeof(long) * 8 == 64, "pfc target widths mismatch");
_Static_assert(__INT_MAX__ == 2147483647 && __LONG_MAX__ == 9223372036854775807L, "pfc target ranges mismatch");

long sum(const long a[], long a_len, long n)
{
    (void)a;
    (void)a_len;
    (void)n;
    // entry check 1: REQUIRES kernels/sum/kernel.c:6:5 conjunct 1
    _Bool pfc_entry_1 = ((long)0 <= n);
    if (!pfc_entry_1) __builtin_trap();
    // entry check 2: REQUIRES kernels/sum/kernel.c:6:5 conjunct 2
    _Bool pfc_entry_2 = (n <= a_len);
    if (!pfc_entry_2) __builtin_trap();
    long pfc_tmp_1 = 0;
    long s = pfc_tmp_1;
    (void)s;
    long pfc_tmp_2 = 0;
    long i = pfc_tmp_2;
    (void)i;
    while (1) {
        int pfc_tmp_3 = (i < n);
        if (!(pfc_tmp_3)) break;
        // proved: obligation bounds:ab87597f0e8d017c22a8648187f0fcceec24e12c0fd37b4f8e7b8a20b95ec5b0
        long pfc_tmp_4 = a[i];
        long pfc_tmp_5;
        if (__builtin_add_overflow(s, pfc_tmp_4, &pfc_tmp_5)) __builtin_trap();
        s = pfc_tmp_5;
        long pfc_tmp_6 = 1;
        // proved: obligation overflow:3c630fca199ca5a4f0b43d27683379e5a170ae5f30dd03169ca155c71ff5129c
        long pfc_tmp_7 = (i + pfc_tmp_6);
        i = pfc_tmp_7;
    }
    return s;
}
