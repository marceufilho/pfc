| kernel | loops | candidates | false (admitted) | valid+sufficient | valid, insufficient | unclassified | unparseable | entry models reproduced / reconstructible | unreconstructible | abstract models | discrepancies | passed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| accumulate | 1 | 18 | 9 (0) | 5 | 4 | 0 | 0 | 8/8 | 0 | 5 | 0 | yes |
| copy | 1 | 18 | 7 (0) | 6 | 5 | 0 | 0 | 6/6 | 0 | 6 | 0 | yes |
| count | 1 | 20 | 8 (0) | 6 | 6 | 0 | 0 | 6/6 | 0 | 8 | 0 | yes |
| fill | 1 | 18 | 9 (0) | 5 | 4 | 0 | 0 | 8/8 | 0 | 5 | 0 | yes |
| find | 1 | 18 | 9 (0) | 5 | 4 | 0 | 0 | 8/8 | 0 | 5 | 0 | yes |
| replace | 1 | 20 | 11 (0) | 5 | 4 | 0 | 0 | 10/10 | 0 | 5 | 0 | yes |
| reverse | 1 | 20 | 10 (0) | 6 | 4 | 0 | 0 | 7/7 | 0 | 7 | 0 | yes |
| swap_ranges | 1 | 18 | 7 (0) | 6 | 5 | 0 | 0 | 6/6 | 0 | 6 | 0 | yes |
| binary_search | 1 | 20 | 12 (0) | 2 | 6 | 0 | 0 | 11/11 | 0 | 7 | 0 | yes |
| dot | 1 | 20 | 10 (0) | 6 | 4 | 0 | 0 | 7/7 | 0 | 7 | 0 | yes |
| matvec | 2 | 40 | 20 (0) | 8 | 12 | 0 | 0 | 3/3 | 5 | 24 | 0 | yes |
| prefix_sum | 1 | 16 | 7 (0) | 5 | 4 | 0 | 0 | 6/6 | 0 | 5 | 0 | yes |
| saxpy | 1 | 20 | 11 (0) | 5 | 4 | 0 | 0 | 10/10 | 0 | 5 | 0 | yes |
| sum | 1 | 19 | 10 (0) | 5 | 4 | 0 | 0 | 6/6 | 0 | 8 | 0 | yes |

Classes are independent of the solver: `false` has a reachable violating state found by the interpreter; the 100% rejection criterion applies to that class with the shown denominator. Entry models are reconstructed and must reproduce; preservation and loop-state models are abstract, not reproduction targets. An entry model whose array lengths exceed the bounded reconstruction profile is unreconstructible and excluded from the denominator, never relabelled.
