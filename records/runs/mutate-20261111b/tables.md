# Mutation kill matrix (E1.4)

| group | mutants | killed | golden-only kills | survivors | score (Wilson 95%) | score without golden-only |
| --- | ---: | ---: | ---: | --- | --- | --- |
| compiler | 40 | 39 | 1 | vc-10 | 0.975 [0.871, 0.996] | 0.950 [0.835, 0.986] |
| harness | 20 | 18 | 0 | proposer-01, proposer-05 | 0.900 [0.699, 0.972] | 0.900 [0.699, 0.972] |

Target: score ≥ 0.9; equivalent mutants are hand-classified in the step report.

## compiler

| mutant | unit | golden | differential | breakit | injection | oracle |
| --- | --- | --- | --- | --- | --- | --- |
| authorization-01 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-02 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-03 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-04 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-05 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-06 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-07 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-08 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-09 | killed | not_run | not_run | not_run | not_run | not_run |
| authorization-10 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-01 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-02 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-03 | passed | killed | passed | passed | passed | not_run |
| emit-04 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-05 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-06 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-07 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-08 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-09 | killed | not_run | not_run | not_run | not_run | not_run |
| emit-10 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-01 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-02 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-03 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-04 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-05 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-06 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-07 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-08 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-09 | killed | not_run | not_run | not_run | not_run | not_run |
| vc-10 | passed | passed | passed | passed | passed | passed |
| wp-01 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-02 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-03 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-04 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-05 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-06 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-07 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-08 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-09 | killed | not_run | not_run | not_run | not_run | not_run |
| wp-10 | killed | not_run | not_run | not_run | not_run | not_run |

## harness

| mutant | unit | integration |
| --- | --- | --- |
| interpret-01 | killed | not_run |
| interpret-02 | killed | not_run |
| interpret-03 | killed | not_run |
| interpret-04 | killed | not_run |
| interpret-05 | killed | not_run |
| proposer-01 | passed | passed |
| proposer-02 | killed | not_run |
| proposer-03 | killed | not_run |
| proposer-04 | killed | not_run |
| proposer-05 | passed | passed |
| stats-01 | killed | not_run |
| stats-02 | killed | not_run |
| stats-03 | killed | not_run |
| stats-04 | killed | not_run |
| stats-05 | killed | not_run |
| tables-01 | killed | not_run |
| tables-02 | killed | not_run |
| tables-03 | killed | not_run |
| tables-04 | killed | not_run |
| tables-05 | killed | not_run |
