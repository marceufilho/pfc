# Independent WP/RTE comparison

| case | class | result | non-overflow goals | overflow goals | smoke |
| --- | --- | --- | --- | --- | --- |
| sum | positive | conditional-agreement | {'valid': 7} | {'timeout': 2, 'valid': 1} | no-inconsistency-detected |
| dot | positive | conditional-agreement | {'valid': 9} | {'timeout': 4, 'valid': 1} | no-inconsistency-detected |
| saxpy | positive | conditional-agreement | {'valid': 13} | {'timeout': 4, 'valid': 1} | no-inconsistency-detected |
| prefix_sum | positive | conditional-agreement | {'valid': 15} | {'valid': 2, 'timeout': 2} | no-inconsistency-detected |
| matvec | positive | inconclusive | {'valid': 17, 'timeout': 1} | {'valid': 6, 'timeout': 4} | no-inconsistency-detected |
| binary_search | positive | conditional-agreement | {'valid': 10} | {'valid': 7} | no-inconsistency-detected |
| accumulate | positive | conditional-agreement | {'valid': 7} | {'timeout': 2, 'valid': 1} | no-inconsistency-detected |
| copy | positive | conditional-agreement | {'valid': 11} | {'valid': 1} | no-inconsistency-detected |
| count | positive | conditional-agreement | {'valid': 7} | {'valid': 2} | no-inconsistency-detected |
| fill | positive | conditional-agreement | {'valid': 9} | {'valid': 1} | no-inconsistency-detected |
| find | positive | conditional-agreement | {'valid': 9} | {'valid': 1} | no-inconsistency-detected |
| replace | positive | conditional-agreement | {'valid': 11} | {'valid': 1} | no-inconsistency-detected |
| reverse | positive | conditional-agreement | {'valid': 17} | {'valid': 7} | no-inconsistency-detected |
| swap_ranges | positive | conditional-agreement | {'valid': 16} | {'valid': 1} | no-inconsistency-detected |
| missing_bounds | control | control detected | {'valid': 6, 'timeout': 1} | {'timeout': 2, 'valid': 1} | no-inconsistency-detected |
| false_invariant | control | control detected | {'valid': 3, 'timeout': 2} | {'valid': 1} | inconsistent |
| bad_preservation | control | control detected | {'valid': 4, 'timeout': 1} | {'valid': 1} | no-inconsistency-detected |
| division_zero | control | control detected | {'timeout': 1, 'valid': 2} | {} | not-run |
| signed_overflow | control | control detected | {'valid': 1} | {'timeout': 1} | not-run |
| vacuous | control | control detected | {'valid': 3} | {} | inconsistent |

Conditional agreement is restricted to matched bounds/division and checked non-overflow support.
Unproved overflow is not a claim of UB freedom. Unproved smoke is not SAT. Unproved goals are not counterexamples.
See each wp.json, wp.stdout (full goal formulas), pfc.stdout and comparison.json. No result authorizes pfc removal.
