# What clang removes from the checked build on its own (post hoc)

Programs: 43; guard sites: 209.

| Flags | Guards still reachable | Removed by clang |
| --- | ---: | ---: |
| -std=c11 -O2 -Wall -Wextra -Werror | 139 | 70 |
| -std=c11 -O3 -march=native -Wall -Wextra -Werror | 139 | 70 |
