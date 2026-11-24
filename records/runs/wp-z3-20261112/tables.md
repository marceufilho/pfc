| tool | budget 10 s | budget 30 s | budget 60 s |
| --- | ---: | ---: | ---: |
| wp | 192/210 | 192/210 | 192/210 |

| kernel | tool | 10 s | 30 s | 60 s |
| --- | --- | ---: | ---: | ---: |
| accumulate | wp | 8/10 | 8/10 | 8/10 |
| binary_search | wp | 17/17 | 17/17 | 17/17 |
| copy | wp | 12/12 | 12/12 | 12/12 |
| count | wp | 9/9 | 9/9 | 9/9 |
| dot | wp | 10/14 | 10/14 | 10/14 |
| fill | wp | 10/10 | 10/10 | 10/10 |
| find | wp | 10/10 | 10/10 | 10/10 |
| matvec | wp | 24/28 | 24/28 | 24/28 |
| prefix_sum | wp | 17/19 | 17/19 | 17/19 |
| replace | wp | 12/12 | 12/12 | 12/12 |
| reverse | wp | 24/24 | 24/24 | 24/24 |
| saxpy | wp | 14/18 | 14/18 | 14/18 |
| sum | wp | 8/10 | 8/10 | 8/10 |
| swap_ranges | wp | 17/17 | 17/17 | 17/17 |

Proved fraction against solver budget (Figure 2 data). pfc counts required obligations proved by Z3 in a fresh process per budget; wp counts non-smoke WP goals valid under Alt-Ergo in a fresh container per budget. The curve is descriptive; a timeout is a row, never an error, and no result crosses budgets.
