#include <stdio.h>

long zero_prefix(long a[], long a_len, long n);

int main(void)
{
    long values[4] = {1, 2, 3, 4};
    long result = zero_prefix(values, 4, 3);
    printf("%ld: %ld %ld %ld %ld\n", result, values[0], values[1], values[2], values[3]);
    return result != 3 || values[0] != 0 || values[1] != 0 || values[2] != 0 || values[3] != 4;
}
