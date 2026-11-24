#include <stdio.h>

long clamp(long x);

int main(void)
{
    long low = clamp(-7);
    long middle = clamp(42);
    long high = clamp(123);
    printf("%ld %ld %ld\n", low, middle, high);
    return low != 0 || middle != 42 || high != 100;
}
