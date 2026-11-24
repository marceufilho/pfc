| kernel | loops | candidates (initial → remaining) | rounds | status | timeouts/unknown/errors | queries | probe splits | solver ms | admitted |
| --- | ---: | --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| zilu01 | 1 | 14 → 9 | 3 | fixpoint | 0/0/0 | 4 | 0 | 231.3 | yes |
| zilu02 | 1 | 10 → 3 | 3 | fixpoint | 0/0/0 | 4 | 0 | 92.5 | yes |
| zilu04 | 1 | 12 → 3 | 3 | fixpoint | 0/0/0 | 4 | 0 | 139.1 | no (insufficient) |
| zilu05 | 1 | 12 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 174.3 | yes |
| zilu06 | 1 | 40 → 7 | 3 | fixpoint | 0/0/0 | 5 | 0 | 931.1 | no (insufficient) |
| zilu08 | 1 | 9 → 5 | 3 | fixpoint | 0/0/0 | 4 | 0 | 122.2 | yes |
| zilu11 | 1 | 6 → 3 | 3 | fixpoint | 0/0/0 | 4 | 0 | 73.4 | yes |
| zilu12 | 1 | 18 → 6 | 3 | fixpoint | 0/0/0 | 4 | 0 | 363.5 | yes |
| zilu13 | 1 | 10 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 97.8 | yes |
| zilu14 | 1 | 2 → 1 | 2 | fixpoint | 0/0/0 | 3 | 0 | 17.4 | yes |
| zilu15 | 1 | 12 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 133.4 | no (insufficient) |
| zilu16 | 1 | 16 → 9 | 3 | fixpoint | 0/0/0 | 4 | 0 | 268.7 | no (insufficient) |
| zilu17 | 1 | 12 → 4 | 3 | fixpoint | 0/0/0 | 4 | 0 | 146.3 | yes |
| zilu18 | 1 | 12 → 7 | 3 | fixpoint | 0/0/0 | 4 | 0 | 186.2 | yes |
| zilu19 | 1 | 12 → 7 | 3 | fixpoint | 0/0/0 | 4 | 0 | 186.0 | yes |
| zilu20 | 1 | 9 → 5 | 3 | fixpoint | 0/0/0 | 4 | 0 | 123.6 | yes |
| zilu22 | 1 | 16 → 10 | 3 | fixpoint | 0/0/0 | 4 | 0 | 260.4 | yes |
| zilu23 | 1 | 4 → 4 | 1 | fixpoint | 0/0/0 | 2 | 0 | 31.3 | no (insufficient) |
| zilu24 | 1 | 12 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 124.9 | no (insufficient) |
| zilu25 | 1 | 1 → 0 | 2 | fixpoint | 0/0/0 | 3 | 0 | 11.7 | no (insufficient) |
| zilu26 | 1 | 5 → 1 | 2 | fixpoint | 0/0/0 | 3 | 0 | 38.2 | yes |
| zilu27 | 1 | 7 → 1 | 2 | fixpoint | 0/0/0 | 3 | 0 | 60.4 | no (insufficient) |
| zilu29 | 1 | 3 → 0 | 2 | fixpoint | 0/0/0 | 3 | 0 | 20.0 | no (insufficient) |
| zilu30 | 1 | 16 → 5 | 3 | fixpoint | 0/0/0 | 4 | 0 | 181.0 | yes |
| zilu32 | 1 | 16 → 10 | 3 | fixpoint | 0/0/0 | 4 | 0 | 561.4 | yes |
| zilu33 | 1 | 1 → 1 | 1 | fixpoint | 0/0/0 | 2 | 0 | 9.5 | yes |
| zilu34 | 1 | 15 → 8 | 3 | fixpoint | 0/0/0 | 4 | 0 | 249.6 | yes |
| zilu35 | 1 | 1 → 1 | 1 | fixpoint | 0/0/0 | 2 | 0 | 9.7 | yes |
| zilu36 | 1 | 20 → 12 | 3 | fixpoint | 0/0/0 | 4 | 0 | 351.5 | yes |
| zilu37 | 1 | 6 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 55.9 | yes |

B2 family: dynamic inference over loop-head states: `v == c`, `v == w + c`, `v == a*w + b` (a in {-2, -1, 2}), `c <= v`, `v <= c`, `v <= w + c` with observed extremes, `v == w + x`, `v == w - x`, `v == w * x`, and implications `cond => e` from call-free scalar branch conditions in the loop body (written as disjunctions); every literal of absolute value at most 8; at most 40 candidates per loop; 200 fuzz draws plus 200 profile draws when a profile binds; a loop with fewer than 20 observed head states gets no candidate; at most 5 refinement rounds. A round limit is not a fixpoint; a round without removals but with undecided queries is inconclusive. Only a failed obligation with a model removes a candidate; the surviving conjunction is admitted or rejected by the unchanged frozen admission, which alone defines success. A probe over the frozen VC size cap is split in halves (recorded as probe splits); a single-candidate probe over the cap is an error.
