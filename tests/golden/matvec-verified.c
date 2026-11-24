/* pfc verified; requires the declared entry/memory contract and defined C arithmetic. */
/* Overflow traps guard + - * and unary - unless their obligation is proved; division guards cover 0 and MIN/-1; verified checks pure-scalar REQUIRES at entry. Proof citations authorize only their individual guards. */
#ifndef __clang__
#error "pfc output requires clang"
#endif
_Static_assert(__CHAR_BIT__ == 8 && sizeof(int) * 8 == 32 && sizeof(long) * 8 == 64, "pfc target widths mismatch");
_Static_assert(__INT_MAX__ == 2147483647 && __LONG_MAX__ == 9223372036854775807L, "pfc target ranges mismatch");

long matvec(const long matrix[], long matrix_len, const long x[], long x_len, long y[], long y_len, long rows, long cols)
{
    (void)matrix;
    (void)matrix_len;
    (void)x;
    (void)x_len;
    (void)y;
    (void)y_len;
    (void)rows;
    (void)cols;
    // entry check 1: REQUIRES kernels/matvec/kernel.c:6:5 conjunct 1
    _Bool pfc_entry_1 = ((long)0 <= rows);
    if (!pfc_entry_1) __builtin_trap();
    // entry check 2: REQUIRES kernels/matvec/kernel.c:6:5 conjunct 2
    _Bool pfc_entry_2 = ((long)0 < cols);
    if (!pfc_entry_2) __builtin_trap();
    // entry check 3: REQUIRES kernels/matvec/kernel.c:6:5 conjunct 3
    _Bool pfc_entry_3 = ((long)0 <= matrix_len);
    if (!pfc_entry_3) __builtin_trap();
    // entry check 4: REQUIRES kernels/matvec/kernel.c:6:5 conjunct 4
    _Bool pfc_entry_4 = (rows <= (matrix_len / cols));
    if (!pfc_entry_4) __builtin_trap();
    // entry check 5: REQUIRES kernels/matvec/kernel.c:7:5 conjunct 1
    _Bool pfc_entry_5 = (cols <= x_len);
    if (!pfc_entry_5) __builtin_trap();
    // entry check 6: REQUIRES kernels/matvec/kernel.c:7:5 conjunct 2
    _Bool pfc_entry_6 = (rows <= y_len);
    if (!pfc_entry_6) __builtin_trap();
    // entry check skipped: REQUIRES kernels/matvec/kernel.c:8:5 conjunct 1 (separation predicate has no C evaluation)
    // entry check skipped: REQUIRES kernels/matvec/kernel.c:9:5 conjunct 1 (separation predicate has no C evaluation)
    long pfc_tmp_1 = 0;
    long i = pfc_tmp_1;
    (void)i;
    while (1) {
        int pfc_tmp_2 = (i < rows);
        if (!(pfc_tmp_2)) break;
        long pfc_tmp_3 = 0;
        long s = pfc_tmp_3;
        (void)s;
        long pfc_tmp_4 = 0;
        long j = pfc_tmp_4;
        (void)j;
        while (1) {
            int pfc_tmp_5 = (j < cols);
            if (!(pfc_tmp_5)) break;
            // proved: obligation overflow:6c61e1e7516b197ac16c233a643bbe72e66e2d2f10d27776d240dac1d75d2eae
            long pfc_tmp_6 = (i * cols);
            // proved: obligation overflow:63c738e55c9e5b5833851daf077ab6c0e6482b5065c10e11686378bc7fd6e1f5
            long pfc_tmp_7 = (pfc_tmp_6 + j);
            // proved: obligation bounds:3e7bbeec4c06d586f6d638fcdc464e2782372236082689a238c87c9785cd0665
            long pfc_tmp_8 = matrix[pfc_tmp_7];
            // proved: obligation bounds:1f30de795f5b6d49db3dc8f32c165ef74ecaca9a2432d8bb737b6a5226813e86
            long pfc_tmp_9 = x[j];
            long pfc_tmp_10;
            if (__builtin_mul_overflow(pfc_tmp_8, pfc_tmp_9, &pfc_tmp_10)) __builtin_trap();
            long pfc_tmp_11;
            if (__builtin_add_overflow(s, pfc_tmp_10, &pfc_tmp_11)) __builtin_trap();
            s = pfc_tmp_11;
            long pfc_tmp_12 = 1;
            // proved: obligation overflow:6c8c4ab9f7a5f5468524c5516a6f8a23c46953bf1a33e919bedaaa84a7323570
            long pfc_tmp_13 = (j + pfc_tmp_12);
            j = pfc_tmp_13;
        }
        // proved: obligation bounds:5e9b72d931f683a7ccea16838b4c1f54f53722398f8c2101ed84f0baeb955126
        y[i] = s;
        long pfc_tmp_14 = 1;
        // proved: obligation overflow:d76a2cc80c0980731db2f8b6de303b1be3c98595b97fb2d8ed544238f0b30583
        long pfc_tmp_15 = (i + pfc_tmp_14);
        i = pfc_tmp_15;
    }
    return rows;
}
