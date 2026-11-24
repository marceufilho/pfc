/* pfc verified; requires the declared entry/memory contract and defined C arithmetic. */
/* Overflow traps guard + - * and unary - unless their obligation is proved; division guards cover 0 and MIN/-1; verified checks pure-scalar REQUIRES at entry. Proof citations authorize only their individual guards. */
#ifndef __clang__
#error "pfc output requires clang"
#endif
_Static_assert(__CHAR_BIT__ == 8 && sizeof(int) * 8 == 32 && sizeof(long) * 8 == 64, "pfc target widths mismatch");
_Static_assert(__INT_MAX__ == 2147483647 && __LONG_MAX__ == 9223372036854775807L, "pfc target ranges mismatch");

long prefix_sum(long a[], long a_len, long n)
{
    (void)a;
    (void)a_len;
    (void)n;
    // entry check 1: REQUIRES kernels/prefix_sum/kernel.c:5:5 conjunct 1
    _Bool pfc_entry_1 = ((long)0 <= n);
    if (!pfc_entry_1) __builtin_trap();
    // entry check 2: REQUIRES kernels/prefix_sum/kernel.c:5:5 conjunct 2
    _Bool pfc_entry_2 = (n <= a_len);
    if (!pfc_entry_2) __builtin_trap();
    long pfc_tmp_1 = 0;
    int pfc_tmp_2 = (n == pfc_tmp_1);
    if (pfc_tmp_2) {
        long pfc_tmp_3 = 0;
        return pfc_tmp_3;
    }
    long pfc_tmp_4 = 1;
    long i = pfc_tmp_4;
    (void)i;
    while (1) {
        int pfc_tmp_5 = (i < n);
        if (!(pfc_tmp_5)) break;
        // proved: obligation bounds:0705c90ecc50a0088650465f4c53f0b1e858b4d540ee7fec5a3f8a66e6afb83e
        long pfc_tmp_6 = a[i];
        long pfc_tmp_7 = 1;
        // proved: obligation overflow:ee5df130ac276ea7d2b4f4a0d1a64ab5bd1b37bbf8bfbdc15ca399ec439e0440
        long pfc_tmp_8 = (i - pfc_tmp_7);
        // proved: obligation bounds:fdc110d66d340165702e6fdd9999c4a0c7e456ff0b1299e565a6236d4ed2a98a
        long pfc_tmp_9 = a[pfc_tmp_8];
        long pfc_tmp_10;
        if (__builtin_add_overflow(pfc_tmp_6, pfc_tmp_9, &pfc_tmp_10)) __builtin_trap();
        // proved: obligation bounds:47f0aae748fe5173376d2542ea3f5482d1609dcc8ae75d048904e8998211692d
        a[i] = pfc_tmp_10;
        long pfc_tmp_11 = 1;
        // proved: obligation overflow:e4ceedfc1c6b09fc79448ea35f58ad3d617e3deddbab0bac538c990b76a22436
        long pfc_tmp_12 = (i + pfc_tmp_11);
        i = pfc_tmp_12;
    }
    return n;
}
