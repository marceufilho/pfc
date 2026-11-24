# What clang removes from the checked build on its own (post hoc)

Programs: 14; guard sites: 31.

| Flags | Guards still reachable | Removed by clang |
| --- | ---: | ---: |
| -std=c11 -O2 -Wall -Wextra -Werror | 21 | 10 |
| -std=c11 -O3 -march=native -Wall -Wextra -Werror | 21 | 10 |
