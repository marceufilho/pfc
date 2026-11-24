| kernel | loops | candidates (initial → remaining) | rounds | status | timeouts/unknown/errors | queries | probe splits | solver ms | admitted |
| --- | ---: | --- | ---: | --- | --- | ---: | ---: | ---: | --- |
| zilupair01 | 2 | 108 → 37, 108 → 34 | 5 | round-limit | 0/0/0 | 32 | 0 | 17555.7 | no (not-inductive) |
| zilupair02 | 2 | 75 → 23, 75 → 19 | 5 | fixpoint | 0/0/0 | 22 | 0 | 7270.5 | no (not-inductive) |
| zilupair03 | 2 | 147 → 47, 147 → 38 | 5 | round-limit | 0/0/0 | 42 | 0 | 24464.6 | no (not-inductive) |
| zilupair04 | 2 | 192 → 30, 192 → 19 | 5 | round-limit | 0/0/0 | 45 | 8 | 30655.0 | no (insufficient) |
| zilupair05 | 2 | 75 → 29, 75 → 37 | 5 | fixpoint | 0/0/0 | 23 | 0 | 8920.4 | yes |
| zilupair06 | 2 | 27 → 4, 27 → 6 | 4 | fixpoint | 0/0/0 | 9 | 0 | 1431.9 | yes |
| zilupair07 | 2 | 108 → 18, 108 → 16 | 5 | round-limit | 0/0/0 | 26 | 0 | 12374.6 | no (not-inductive) |
| zilupair08 | 2 | 192 → 14, 192 → 23 | 5 | round-limit | 0/0/0 | 61 | 8 | 60626.6 | no (not-inductive) |
| zilupair09 | 2 | 108 → 22, 108 → 19 | 5 | round-limit | 0/0/0 | 24 | 0 | 11004.1 | yes |
| zilupair10 | 2 | 108 → 21, 108 → 23 | 5 | fixpoint | 0/0/0 | 23 | 0 | 10829.4 | no (not-inductive) |
| zilupair11 | 2 | 75 → 34, 75 → 36 | 5 | fixpoint | 0/0/0 | 24 | 0 | 9210.2 | yes |
| zilupair12 | 2 | 147 → 50, 147 → 37 | 5 | round-limit | 0/0/0 | 42 | 0 | 24156.2 | no (not-inductive) |
| zilupair13 | 2 | 108 → 18, 108 → 22 | 5 | round-limit | 0/0/0 | 24 | 0 | 10915.6 | yes |
| zilupair14 | 2 | 108 → 35, 108 → 36 | 5 | round-limit | 0/0/0 | 43 | 11 | 28824.4 | no (not-inductive) |
| zilupair15 | 2 | 48 → 10, 48 → 14 | 5 | fixpoint | 0/0/0 | 13 | 0 | 3680.8 | yes |

B1 family: `c <= v`, `v <= w + c` over the loop's scalar variables and length parameters, c ∈ {−1, 0, 1}; at most 5 refinement rounds. A round limit is not a fixpoint; a round without removals but with undecided queries is inconclusive. Only a failed obligation with a model removes a candidate; the surviving conjunction is admitted or rejected by the unchanged frozen admission, which alone defines success. A probe over the frozen VC size cap is split in halves (recorded as probe splits); a single-candidate probe over the cap is an error.
