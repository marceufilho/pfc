| tool | budget 1 s | budget 2 s | budget 5 s | budget 10 s | budget 30 s | budget 60 s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| pfc | 62/62 | 62/62 | 62/62 | 62/62 | 62/62 | 62/62 |
| wp | 191/210 | 191/210 | 191/210 | 191/210 | 191/210 | 191/210 |

| kernel | tool | 1 s | 2 s | 5 s | 10 s | 30 s | 60 s |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| accumulate | pfc | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| accumulate | wp | 8/10 | 8/10 | 8/10 | 8/10 | 8/10 | 8/10 |
| binary_search | pfc | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| binary_search | wp | 17/17 | 17/17 | 17/17 | 17/17 | 17/17 | 17/17 |
| copy | pfc | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| copy | wp | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| count | pfc | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| count | wp | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 |
| dot | pfc | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| dot | wp | 10/14 | 10/14 | 10/14 | 10/14 | 10/14 | 10/14 |
| fill | pfc | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| fill | wp | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| find | pfc | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| find | wp | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| matvec | pfc | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 |
| matvec | wp | 23/28 | 23/28 | 23/28 | 23/28 | 23/28 | 23/28 |
| prefix_sum | pfc | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| prefix_sum | wp | 17/19 | 17/19 | 17/19 | 17/19 | 17/19 | 17/19 |
| replace | pfc | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| replace | wp | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| reverse | pfc | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| reverse | wp | 24/24 | 24/24 | 24/24 | 24/24 | 24/24 | 24/24 |
| saxpy | pfc | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| saxpy | wp | 14/18 | 14/18 | 14/18 | 14/18 | 14/18 | 14/18 |
| sum | pfc | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| sum | wp | 8/10 | 8/10 | 8/10 | 8/10 | 8/10 | 8/10 |
| swap_ranges | pfc | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 |
| swap_ranges | wp | 17/17 | 17/17 | 17/17 | 17/17 | 17/17 | 17/17 |

Proved fraction against solver budget (Figure 2 data). pfc counts required obligations proved by Z3 in a fresh process per budget; wp counts non-smoke WP goals valid under Alt-Ergo in a fresh container per budget. The curve is descriptive; a timeout is a row, never an error, and no result crosses budgets.
