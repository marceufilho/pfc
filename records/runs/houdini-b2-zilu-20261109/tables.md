| kernel | loops | candidates (initial → remaining) | rounds | status | timeouts/unknown/errors | queries | probe splits | solver ms | admitted |
| --- | ---: | --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| zilu01 | 1 | 14 → 9 | 3 | fixpoint | 0/0/0 | 4 | 0 | 227.1 | yes |
| zilu02 | 1 | 10 → 3 | 3 | fixpoint | 0/0/0 | 4 | 0 | 89.2 | yes |
| zilu04 | 1 | 12 → 3 | 3 | fixpoint | 0/0/0 | 4 | 0 | 127.3 | no (insufficient) |
| zilu05 | 1 | 12 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 166.7 | yes |
| zilu06 | 1 | 40 → 7 | 3 | fixpoint | 0/0/0 | 5 | 0 | 907.3 | no (insufficient) |
| zilu08 | 1 | 9 → 5 | 3 | fixpoint | 0/0/0 | 4 | 0 | 116.2 | yes |
| zilu11 | 1 | 6 → 3 | 3 | fixpoint | 0/0/0 | 4 | 0 | 69.2 | yes |
| zilu12 | 1 | 18 → 6 | 3 | fixpoint | 0/0/0 | 4 | 0 | 351.3 | yes |
| zilu13 | 1 | 10 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 95.9 | yes |
| zilu14 | 1 | 2 → 1 | 2 | fixpoint | 0/0/0 | 3 | 0 | 17.0 | yes |
| zilu15 | 1 | 12 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 129.0 | no (insufficient) |
| zilu16 | 1 | 16 → 9 | 3 | fixpoint | 0/0/0 | 4 | 0 | 260.4 | no (insufficient) |
| zilu17 | 1 | 12 → 4 | 3 | fixpoint | 0/0/0 | 4 | 0 | 145.1 | yes |
| zilu18 | 1 | 12 → 7 | 3 | fixpoint | 0/0/0 | 4 | 0 | 184.2 | yes |
| zilu19 | 1 | 12 → 7 | 3 | fixpoint | 0/0/0 | 4 | 0 | 183.9 | yes |
| zilu20 | 1 | 9 → 5 | 3 | fixpoint | 0/0/0 | 4 | 0 | 120.9 | yes |
| zilu22 | 1 | 16 → 10 | 3 | fixpoint | 0/0/0 | 4 | 0 | 255.1 | yes |
| zilu23 | 1 | 4 → 4 | 1 | fixpoint | 0/0/0 | 2 | 0 | 29.7 | no (insufficient) |
| zilu24 | 1 | 12 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 120.6 | no (insufficient) |
| zilu25 | 1 | 1 → 0 | 2 | fixpoint | 0/0/0 | 3 | 0 | 10.8 | no (insufficient) |
| zilu26 | 1 | 5 → 1 | 2 | fixpoint | 0/0/0 | 3 | 0 | 35.9 | yes |
| zilu27 | 1 | 7 → 1 | 2 | fixpoint | 0/0/0 | 3 | 0 | 58.3 | no (insufficient) |
| zilu29 | 1 | 3 → 0 | 2 | fixpoint | 0/0/0 | 3 | 0 | 18.6 | no (insufficient) |
| zilu30 | 1 | 16 → 5 | 3 | fixpoint | 0/0/0 | 4 | 0 | 175.0 | yes |
| zilu32 | 1 | 16 → 10 | 3 | fixpoint | 0/0/0 | 4 | 0 | 549.3 | yes |
| zilu33 | 1 | 1 → 1 | 1 | fixpoint | 0/0/0 | 2 | 0 | 9.4 | yes |
| zilu34 | 1 | 15 → 8 | 3 | fixpoint | 0/0/0 | 4 | 0 | 244.5 | yes |
| zilu35 | 1 | 1 → 1 | 1 | fixpoint | 0/0/0 | 2 | 0 | 9.4 | yes |
| zilu36 | 1 | 20 → 13 | 3 | fixpoint | 0/0/0 | 4 | 0 | 356.4 | yes |
| zilu37 | 1 | 6 → 4 | 2 | fixpoint | 0/0/0 | 3 | 0 | 56.0 | yes |

B2 family: dynamic inference over loop-head states: `v == c`, `v == w + c`, `v == a*w + b` (a in {-2, -1, 2}), `c <= v`, `v <= c`, `v <= w + c` with observed extremes, `v == w + x`, `v == w - x`, `v == w * x`, and implications `cond => e` from call-free scalar branch conditions in the loop body (written as disjunctions); every literal of absolute value at most 8; at most 40 candidates per loop; 200 fuzz draws plus 200 profile draws when a profile binds; a loop with fewer than 20 observed head states gets no candidate; at most 5 refinement rounds. A round limit is not a fixpoint; a round without removals but with undecided queries is inconclusive. Only a failed obligation with a model removes a candidate; the surviving conjunction is admitted or rejected by the unchanged frozen admission, which alone defines success. A probe over the frozen VC size cap is split in halves (recorded as probe splits); a single-candidate probe over the cap is an error.
