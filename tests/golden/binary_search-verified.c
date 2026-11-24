/* pfc verified; requires the declared entry/memory contract and defined C arithmetic. */
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
    // entry check 1: REQUIRES kernels/binary_search/kernel.c:5:5 conjunct 1
    _Bool pfc_entry_1 = ((long)0 <= n);
    if (!pfc_entry_1) __builtin_trap();
    // entry check 2: REQUIRES kernels/binary_search/kernel.c:5:5 conjunct 2
    _Bool pfc_entry_2 = (n <= a_len);
    if (!pfc_entry_2) __builtin_trap();
    long pfc_tmp_1 = 0;
    long lo = pfc_tmp_1;
    (void)lo;
    long pfc_tmp_2 = 1;
    // proved: obligation overflow:b3bd8448e0180736c7a9bfb7e4e1596ab03b7cbe2a4825a91db40c1f68f5b6a3
    long pfc_tmp_3 = (n - pfc_tmp_2);
    long hi = pfc_tmp_3;
    (void)hi;
    while (1) {
        int pfc_tmp_4 = (lo <= hi);
        if (!(pfc_tmp_4)) break;
        // proved: obligation overflow:66d193976b783514f9a11a38b9744fa395fd60c46d54cd3319b97ee8b650ad3d
        long pfc_tmp_5 = (hi - lo);
        long pfc_tmp_6 = 2;
        // proved: obligation division:e31eaef4d8051f06215c4f86f2896b624e639bdb117b7ac651f1c23f76b8b317
        long pfc_tmp_7 = (pfc_tmp_5 / pfc_tmp_6);
        // proved: obligation overflow:f73fd583f8317e1bd9ec3b926d5c28c65e4761e12484935c7ea06ac0436b8c5e
        long pfc_tmp_8 = (lo + pfc_tmp_7);
        long mid = pfc_tmp_8;
        (void)mid;
        // proved: obligation bounds:9d39870e5bab19b1faa7180f60138eb988650ca20d1c7afad7d5b97599f396f0
        long pfc_tmp_9 = a[mid];
        long value = pfc_tmp_9;
        (void)value;
        int pfc_tmp_10 = (value < needle);
        if (pfc_tmp_10) {
            long pfc_tmp_11 = 1;
            // proved: obligation overflow:b1c84e00cb7858c8dc81410a42671881f583367caf2167537be2821a57dcea1a
            long pfc_tmp_12 = (mid + pfc_tmp_11);
            lo = pfc_tmp_12;
        } else {
            int pfc_tmp_13 = (needle < value);
            if (pfc_tmp_13) {
                long pfc_tmp_14 = 1;
                // proved: obligation overflow:abe24d288d6c2d87681237820c996b57f2094c5466ecd8856478c08cc489c734
                long pfc_tmp_15 = (mid - pfc_tmp_14);
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
