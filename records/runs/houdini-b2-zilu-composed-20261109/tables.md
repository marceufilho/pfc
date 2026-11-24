| kernel | loops | candidates (initial → remaining) | rounds | status | timeouts/unknown/errors | queries | probe splits | solver ms | admitted |
| --- | ---: | --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| zilupair01 | 2 | 0 → 0, 0 → 0 | 1 | fixpoint | 0/0/0 | 2 | 0 | 36.6 | no (not-inductive) |
| zilupair02 | 2 | 32 → 16, 22 → 11 | 3 | fixpoint | 0/0/0 | 8 | 0 | 1246.5 | no (not-inductive) |
| zilupair03 | 2 | 40 → 21, 37 → 16 | 4 | fixpoint | 0/0/0 | 13 | 0 | 2621.7 | yes |
| zilupair04 | 2 | 40 → 12, 40 → 7 | 4 | fixpoint | 0/0/0 | 10 | 0 | 2316.9 | no (insufficient) |
| zilupair05 | 2 | 30 → 13, 40 → 29 | 4 | fixpoint | 0/0/0 | 11 | 0 | 2254.6 | yes |
| zilupair06 | 2 | 9 → 2, 9 → 3 | 3 | fixpoint | 0/0/0 | 4 | 0 | 227.1 | yes |
| zilupair07 | 2 | 29 → 7, 36 → 6 | 4 | fixpoint | 0/0/0 | 8 | 0 | 1380.4 | no (not-inductive) |
| zilupair08 | 2 | 0 → 0, 0 → 0 | 1 | fixpoint | 0/0/0 | 2 | 0 | 45.0 | no (not-inductive) |
| zilupair09 | 2 | 37 → 16, 32 → 9 | 4 | fixpoint | 0/0/0 | 11 | 0 | 1846.9 | yes |
| zilupair10 | 2 | 30 → 10, 0 → 0 | 3 | fixpoint | 0/0/0 | 5 | 0 | 493.2 | no (not-inductive) |
| zilupair11 | 2 | 40 → 30, 40 → 29 | 4 | fixpoint | 0/0/0 | 14 | 0 | 3030.6 | yes |
| zilupair12 | 2 | 40 → 21, 40 → 15 | 4 | fixpoint | 0/0/0 | 13 | 0 | 2731.1 | yes |
| zilupair13 | 2 | 32 → 8, 32 → 9 | 4 | fixpoint | 0/0/0 | 8 | 0 | 1431.6 | yes |
| zilupair14 | 2 | 40 → 20, 40 → 18 | 4 | fixpoint | 0/0/0 | 13 | 0 | 4816.4 | yes |
| zilupair15 | 2 | 19 → 5, 16 → 7 | 4 | fixpoint | 0/0/0 | 7 | 0 | 693.0 | yes |

B2 family: dynamic inference over loop-head states: `v == c`, `v == w + c`, `v == a*w + b` (a in {-2, -1, 2}), `c <= v`, `v <= c`, `v <= w + c` with observed extremes, `v == w + x`, `v == w - x`, `v == w * x`, and implications `cond => e` from call-free scalar branch conditions in the loop body (written as disjunctions); every literal of absolute value at most 8; at most 40 candidates per loop; 200 fuzz draws plus 200 profile draws when a profile binds; a loop with fewer than 20 observed head states gets no candidate; at most 5 refinement rounds. A round limit is not a fixpoint; a round without removals but with undecided queries is inconclusive. Only a failed obligation with a model removes a candidate; the surviving conjunction is admitted or rejected by the unchanged frozen admission, which alone defines success. A probe over the frozen VC size cap is split in halves (recorded as probe splits); a single-candidate probe over the cap is an error.
