/* pfc verified; requires the declared entry/memory contract and defined C arithmetic. */
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
    // entry check 1: REQUIRES kernels/dot/kernel.c:5:5 conjunct 1
    _Bool pfc_entry_1 = ((long)0 <= n);
    if (!pfc_entry_1) __builtin_trap();
    // entry check 2: REQUIRES kernels/dot/kernel.c:5:5 conjunct 2
    _Bool pfc_entry_2 = (n <= a_len);
    if (!pfc_entry_2) __builtin_trap();
    // entry check 3: REQUIRES kernels/dot/kernel.c:5:5 conjunct 3
    _Bool pfc_entry_3 = (n <= b_len);
    if (!pfc_entry_3) __builtin_trap();
    long pfc_tmp_1 = 0;
    long s = pfc_tmp_1;
    (void)s;
    long pfc_tmp_2 = 0;
    long i = pfc_tmp_2;
    (void)i;
    while (1) {
        int pfc_tmp_3 = (i < n);
        if (!(pfc_tmp_3)) break;
        // proved: obligation bounds:9db04f46db6827197b91da1d7cdc0a4714ffd5412a563beb5ee2f02adbbe7051
        long pfc_tmp_4 = a[i];
        // proved: obligation bounds:932739284126cb63b634c39afd5424bb4d9df89f479f6bdbd45a5a8bf0ea8efe
        long pfc_tmp_5 = b[i];
        long pfc_tmp_6;
        if (__builtin_mul_overflow(pfc_tmp_4, pfc_tmp_5, &pfc_tmp_6)) __builtin_trap();
        long pfc_tmp_7;
        if (__builtin_add_overflow(s, pfc_tmp_6, &pfc_tmp_7)) __builtin_trap();
        s = pfc_tmp_7;
        long pfc_tmp_8 = 1;
        // proved: obligation overflow:a551ebd20520a91931c714f67ef6020b15fd065895c092855c2a548a610e4a47
        long pfc_tmp_9 = (i + pfc_tmp_8);
        i = pfc_tmp_9;
    }
    return s;
}
