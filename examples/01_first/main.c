#include <stdio.h>

long first(const long a[], long a_len);

int main(void)
{
    long values[2] = {7, 9};
    long result = first(values, 2);
    printf("%ld\n", result);
    return result != 7 || values[0] != 7 || values[1] != 9;
}
