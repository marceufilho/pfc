#include "spec.h"

long requires_division(const long a[], long a_len, long rows, long cols)
{
    REQUIRES(0 < cols && rows <= a_len / cols && 0 <= rows);
    return rows * cols;
}
