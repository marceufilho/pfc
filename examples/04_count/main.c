#include <stdio.h>

long count_to(long n);

int main(void)
{
    long result = count_to(5);
    printf("%ld\n", result);
    return result != 5 || count_to(0) != 0 || count_to(100) != 100;
}
