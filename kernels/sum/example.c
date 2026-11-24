#include <stdio.h>
#include <string.h>

#include "kernel.h"

enum { SUM_DEMO_LENGTH = 16 };

int main(int argc, char *argv[])
{
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        puts("usage: sum-demo [--help]\n"
             "Run the fixed 16-element sum example (values 1 through 16).\n"
             "Example: ./sum-demo");
        return 0;
    }
    if (argc != 1) {
        fputs("usage: sum-demo [--help]\n", stderr);
        return 2;
    }

    long values[SUM_DEMO_LENGTH];
    for (long i = 0; i < SUM_DEMO_LENGTH; i = i + 1) {
        values[i] = i + 1;
    }

    /* The maximum partial sum is 136, representable on every conforming C11 long. */
    printf("%ld\n", sum(values, SUM_DEMO_LENGTH, SUM_DEMO_LENGTH));
    return 0;
}
