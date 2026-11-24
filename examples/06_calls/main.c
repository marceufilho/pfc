#include <stdio.h>

long increment_twice(long x);

int main(void)
{
    long result = increment_twice(3);
    printf("%ld\n", result);
    return result != 5;
}
