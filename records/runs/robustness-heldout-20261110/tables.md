| kernel | variants (rename/reorder/swap) | brittle | divergent | renamed C identical |
| --- | --- | ---: | ---: | ---: |
| add_2_n_u | 10 (5/1/4) | 0 | 0 | 5/5 |
| add_3_n_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| copy_2_u | 10 (5/1/4) | 0 | 0 | 5/5 |
| copy_3_n_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| copy_partial_2_n_u | 10 (5/1/4) | 0 | 0 | 5/5 |
| copy_partial_3_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| diff_2_n_u | 10 (5/1/4) | 0 | 0 | 5/5 |
| diff_3_n_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| init_2_n_u | 10 (5/1/4) | 0 | 0 | 5/5 |
| init_3_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| init_4_n_u | 10 (4/3/3) | 0 | 0 | 4/4 |
| init_non_constant_2_n_u | 10 (5/1/4) | 0 | 0 | 5/5 |
| init_non_constant_3_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| max_2_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| max_3_n_u | 10 (4/3/3) | 0 | 0 | 4/4 |
| mbpr2 | 10 (4/3/3) | 0 | 0 | 4/4 |
| mbpr3 | 10 (4/3/3) | 0 | 0 | 4/4 |
| mbpr4 | 10 (4/3/3) | 0 | 0 | 4/4 |
| min_2_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| min_3_n_u | 10 (4/3/3) | 0 | 0 | 4/4 |
| nr2 | 10 (4/2/4) | 0 | 0 | 4/4 |
| nr3 | 10 (4/2/4) | 0 | 0 | 4/4 |
| nr4 | 10 (4/2/4) | 0 | 0 | 4/4 |
| nr5 | 10 (4/2/4) | 0 | 0 | 4/4 |
| pnr2 | 10 (4/3/3) | 0 | 0 | 4/4 |
| pnr3 | 10 (4/3/3) | 0 | 0 | 4/4 |
| poly1 | 10 (8/0/2) | 0 | 0 | 8/8 |
| poly2 | 10 (6/0/4) | 0 | 0 | 6/6 |
| pr2 | 10 (4/2/4) | 0 | 0 | 4/4 |
| pr3 | 10 (4/3/3) | 0 | 0 | 4/4 |
| pr4 | 10 (4/3/3) | 0 | 0 | 4/4 |
| pr5 | 10 (4/3/3) | 0 | 0 | 4/4 |
| rev_2_n_u | 10 (5/1/4) | 0 | 0 | 5/5 |
| rev_3_u | 10 (4/2/4) | 0 | 0 | 4/4 |
| revcpyswp2 | 10 (6/1/3) | 0 | 0 | 6/6 |
| rew | 10 (5/2/3) | 0 | 0 | 5/5 |
| rewnif | 10 (7/0/3) | 0 | 0 | 7/7 |
| rewnifrev | 10 (10/0/0) | 0 | 0 | 10/10 |
| rewnifrev2 | 10 (8/0/2) | 0 | 0 | 8/8 |
| rewrev | 10 (7/2/1) | 0 | 0 | 7/7 |
| skipped | 10 (5/0/5) | 0 | 0 | 5/5 |
| tcpy | 10 (9/0/1) | 0 | 0 | 9/9 |
| transpose_u | 10 (5/1/4) | 0 | 0 | 5/5 |

Brittle: required verdicts, per-site decisions or restrict facts differ from the original's under the location mapping (renamed C is compared only after the inverse renaming). Divergent: the interpreter observed different behavior on the same fuzzed inputs, which would be a transformation bug. Finite comparisons are empirical validation, not a proof of equivalence.
