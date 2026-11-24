#include <stdio.h>

long divide(long numerator, long denominator);

int main(void)
{
    long result = divide(21, 3);
    printf("%ld\n", result);
    return result != 7;
}
