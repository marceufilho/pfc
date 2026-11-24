| kernel | loops | candidates (initial → remaining) | rounds | status | timeouts/unknown/errors | queries | solver ms | admitted |
| --- | ---: | --- | ---: | --- | --- | ---: | ---: | --- |
| accumulate | 1 | 48 → 12 | 2 | fixpoint | 0/0/0 | 4 | 593.3 | yes |
| copy | 1 | 48 → 18 | 2 | fixpoint | 0/0/0 | 4 | 688.0 | yes |
| count | 1 | 75 → 20 | 4 | fixpoint | 0/0/0 | 8 | 2334.4 | yes |
| fill | 1 | 48 → 12 | 2 | fixpoint | 0/0/0 | 4 | 553.6 | yes |
| find | 1 | 48 → 12 | 2 | fixpoint | 0/0/0 | 4 | 560.2 | yes |
| replace | 1 | 75 → 12 | 2 | fixpoint | 0/0/0 | 6 | 1625.6 | yes |
| reverse | 1 | 48 → 20 | 2 | fixpoint | 0/0/0 | 4 | 1202.6 | yes |
| swap_ranges | 1 | 48 → 18 | 2 | fixpoint | 0/0/0 | 4 | 722.3 | yes |
| binary_search | 1 | 75 → 20 | 3 | fixpoint | 0/0/0 | 7 | 3213.4 | yes |
| dot | 1 | 75 → 18 | 3 | fixpoint | 0/0/0 | 8 | 2452.3 | yes |
| matvec | 2 | 108 → 30, 192 → 46 | 5 | round-limit | 0/0/0 | 37 | 28538.7 | no (not-inductive) |
| prefix_sum | 1 | 27 → 15 | 2 | fixpoint | 0/0/0 | 4 | 475.5 | yes |
| saxpy | 1 | 75 → 18 | 2 | fixpoint | 0/0/0 | 6 | 1415.2 | yes |
| sum | 1 | 48 → 12 | 3 | fixpoint | 0/0/0 | 5 | 944.2 | yes |

B1 family: `c <= v`, `v <= w + c` over the loop's scalar variables and length parameters, c ∈ {−1, 0, 1}; at most 5 refinement rounds. A round limit is not a fixpoint; a round without removals but with undecided queries is inconclusive. Only a failed obligation with a model removes a candidate; the surviving conjunction is admitted or rejected by the unchanged frozen admission, which alone defines success.
