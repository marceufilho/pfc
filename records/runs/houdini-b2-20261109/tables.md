| kernel | loops | candidates (initial → remaining) | rounds | status | timeouts/unknown/errors | queries | probe splits | solver ms | admitted |
| --- | ---: | --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| accumulate | 1 | 6 → 6 | 1 | fixpoint | 0/0/0 | 2 | 0 | 38.3 | yes |
| copy | 1 | 9 → 9 | 1 | fixpoint | 0/0/0 | 2 | 0 | 60.8 | yes |
| count | 1 | 11 → 10 | 2 | fixpoint | 0/0/0 | 3 | 0 | 191.0 | yes |
| fill | 1 | 6 → 6 | 1 | fixpoint | 0/0/0 | 2 | 0 | 32.6 | yes |
| find | 1 | 6 → 6 | 1 | fixpoint | 0/0/0 | 2 | 0 | 34.4 | yes |
| replace | 1 | 6 → 6 | 1 | fixpoint | 0/0/0 | 2 | 0 | 57.4 | yes |
| reverse | 1 | 10 → 7 | 2 | fixpoint | 0/0/0 | 3 | 0 | 171.3 | yes |
| swap_ranges | 1 | 9 → 9 | 1 | fixpoint | 0/0/0 | 2 | 0 | 73.8 | yes |
| binary_search | 1 | 10 → 10 | 1 | fixpoint | 0/0/0 | 2 | 0 | 163.0 | yes |
| dot | 1 | 9 → 9 | 1 | fixpoint | 0/0/0 | 2 | 0 | 66.7 | yes |
| matvec | 2 | 12 → 11, 17 → 17 | 2 | fixpoint | 0/0/0 | 5 | 0 | 658.5 | yes |
| prefix_sum | 1 | 6 → 6 | 1 | fixpoint | 0/0/0 | 2 | 0 | 49.8 | yes |
| saxpy | 1 | 9 → 9 | 1 | fixpoint | 0/0/0 | 2 | 0 | 75.2 | yes |
| sum | 1 | 6 → 5 | 2 | fixpoint | 0/0/0 | 3 | 0 | 52.8 | yes |

B2 family: dynamic inference over loop-head states: `v == c`, `v == w + c`, `v == a*w + b` (a in {-2, -1, 2}), `c <= v`, `v <= c`, `v <= w + c` with observed extremes, `v == w + x`, `v == w - x`, `v == w * x`, and implications `cond => e` from call-free scalar branch conditions in the loop body (written as disjunctions); every literal of absolute value at most 8; at most 40 candidates per loop; 200 fuzz draws plus 200 profile draws when a profile binds; a loop with fewer than 20 observed head states gets no candidate; at most 5 refinement rounds. A round limit is not a fixpoint; a round without removals but with undecided queries is inconclusive. Only a failed obligation with a model removes a candidate; the surviving conjunction is admitted or rejected by the unchanged frozen admission, which alone defines success. A probe over the frozen VC size cap is split in halves (recorded as probe splits); a single-candidate probe over the cap is an error.
