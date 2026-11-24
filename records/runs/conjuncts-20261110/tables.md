| kernel | conjuncts | sensitivity (guard restored / safety-witnessed) | specificity (identical / unwitnessed) | SEPARATED restrict withdrawn | annotation-dependent | anomalies | all as expected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| accumulate | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| copy | 4 | 2/2 | n/a | 1/1 | 1 | 0 | yes |
| count | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| fill | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| find | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| replace | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| reverse | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| swap_ranges | 4 | 2/2 | n/a | 1/1 | 1 | 0 | yes |
| binary_search | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| dot | 3 | 2/2 | n/a | 0/0 | 1 | 0 | yes |
| matvec | 8 | 3/3 | 1/1 | 2/2 | 2 | 0 | yes |
| prefix_sum | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |
| saxpy | 4 | 2/2 | n/a | 1/1 | 1 | 0 | yes |
| sum | 2 | 1/1 | n/a | 0/0 | 1 | 0 | yes |

Expected consequences come from a seeded small-domain witness search with the reference interpreter; absence of a witness is finite evidence, not proof of independence. Annotation-dependent conjuncts are those whose only witness falsifies a proof annotation or makes contract arithmetic undefined.
