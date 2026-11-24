/* pfc plain; requires the declared entry/memory contract and defined C arithmetic. */
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
            long pfc_tmp_6 = (i * cols);
            long pfc_tmp_7 = (pfc_tmp_6 + j);
            long pfc_tmp_8 = matrix[pfc_tmp_7];
            long pfc_tmp_9 = x[j];
            long pfc_tmp_10 = (pfc_tmp_8 * pfc_tmp_9);
            long pfc_tmp_11 = (s + pfc_tmp_10);
            s = pfc_tmp_11;
            long pfc_tmp_12 = 1;
            long pfc_tmp_13 = (j + pfc_tmp_12);
            j = pfc_tmp_13;
        }
        y[i] = s;
        long pfc_tmp_14 = 1;
        long pfc_tmp_15 = (i + pfc_tmp_14);
        i = pfc_tmp_15;
    }
    return rows;
}
