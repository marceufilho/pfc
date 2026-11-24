| kernel | precondition cases | verified traps | skipped-conjunct cases | overflow cases | checked traps | verified traps | disagreements | operational failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| accumulate | 20 | 20 | 0 | 20 | 20 | 20 | 0 | 0 |
| copy | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| count | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| fill | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| find | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| replace | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| reverse | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| swap_ranges | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| binary_search | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| dot | 20 | 20 | 0 | 20 | 20 | 20 | 0 | 0 |
| matvec | 20 | 20 | 0 | 20 | 20 | 20 | 0 | 0 |
| prefix_sum | 20 | 20 | 0 | 20 | 20 | 20 | 0 | 0 |
| saxpy | 20 | 20 | 0 | 20 | 20 | 20 | 0 | 0 |
| sum | 20 | 20 | 0 | 20 | 20 | 20 | 0 | 0 |

Expected traps: overflow cases trap in both instrumented builds; precondition violations trap in verified when a rendered conjunct is false; skipped-conjunct cases are the disclosed gap. SIGILL on Linux/x86 is the trap. Agreement never counts a compile, timeout or other-signal outcome.
