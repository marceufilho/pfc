| kernel | variants (rename/reorder/swap) | brittle | divergent | renamed C identical |
| --- | --- | ---: | ---: | ---: |
| accumulate | 10 (8/0/2) | 0 | 0 | 8/8 |
| copy | 10 (9/0/1) | 0 | 0 | 9/9 |
| count | 10 (7/0/3) | 0 | 0 | 7/7 |
| fill | 10 (9/0/1) | 0 | 0 | 9/9 |
| find | 10 (8/0/2) | 0 | 0 | 8/8 |
| replace | 10 (8/0/2) | 0 | 0 | 8/8 |
| reverse | 10 (9/0/1) | 0 | 0 | 9/9 |
| swap_ranges | 10 (9/0/1) | 0 | 0 | 9/9 |
| binary_search | 10 (6/1/3) | 0 | 0 | 6/6 |
| dot | 10 (6/1/3) | 0 | 0 | 6/6 |
| matvec | 10 (5/1/4) | 0 | 0 | 5/5 |
| prefix_sum | 10 (7/0/3) | 0 | 0 | 7/7 |
| saxpy | 10 (7/0/3) | 0 | 0 | 7/7 |
| sum | 10 (7/1/2) | 0 | 0 | 7/7 |

Brittle: required verdicts, per-site decisions or restrict facts differ from the original's under the location mapping (renamed C is compared only after the inverse renaming). Divergent: the interpreter observed different behavior on the same fuzzed inputs, which would be a transformation bug. Finite comparisons are empirical validation, not a proof of equivalence.
