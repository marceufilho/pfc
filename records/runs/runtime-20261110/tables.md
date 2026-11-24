# Runtime effect (design §6; reported, not claimed)

Governor: powersave; pinned CPU: None -> run label **exploratory**; instruction counter: unavailable; timed runs: 1080; warm-ups: 108.

| kernel | variant | median ms | IQR ms | vectorized | median instructions |
| --- | --- | ---: | ---: | --- | ---: |
| binary_search | checked | 150.655 | 20.605 | False | n/a |
| binary_search | plain | 156.139 | 19.665 | False | n/a |
| binary_search | verified | 148.817 | 16.312 | False | n/a |
| binary_search | without-assume | 146.178 | 14.939 | False | n/a |
| binary_search | without-delete | 146.593 | 23.907 | False | n/a |
| binary_search | without-restrict | 147.851 | 25.714 | False | n/a |
| dot | checked | 1012.105 | 165.874 | False | n/a |
| dot | plain | 746.488 | 91.773 | True | n/a |
| dot | verified | 857.827 | 126.072 | False | n/a |
| dot | without-assume | 887.880 | 76.304 | False | n/a |
| dot | without-delete | 927.208 | 131.533 | False | n/a |
| dot | without-restrict | 877.064 | 99.179 | False | n/a |
| matvec | checked | 1193.789 | 126.506 | True | n/a |
| matvec | plain | 631.026 | 56.130 | True | n/a |
| matvec | verified | 765.803 | 72.716 | False | n/a |
| matvec | without-assume | 761.039 | 122.695 | False | n/a |
| matvec | without-delete | 1077.481 | 88.524 | False | n/a |
| matvec | without-restrict | 761.850 | 95.892 | False | n/a |
| prefix_sum | checked | 2612.796 | 225.055 | False | n/a |
| prefix_sum | plain | 1208.522 | 134.416 | False | n/a |
| prefix_sum | verified | 2553.276 | 124.365 | False | n/a |
| prefix_sum | without-assume | 2488.626 | 261.082 | False | n/a |
| prefix_sum | without-delete | 2516.691 | 162.460 | False | n/a |
| prefix_sum | without-restrict | 2501.291 | 150.574 | False | n/a |
| saxpy | checked | 1070.424 | 147.989 | False | n/a |
| saxpy | plain | 823.346 | 131.499 | True | n/a |
| saxpy | verified | 967.188 | 111.420 | False | n/a |
| saxpy | without-assume | 950.945 | 217.604 | False | n/a |
| saxpy | without-delete | 994.812 | 116.774 | False | n/a |
| saxpy | without-restrict | 980.585 | 103.965 | False | n/a |
| sum | checked | 696.391 | 89.861 | False | n/a |
| sum | plain | 580.746 | 149.266 | True | n/a |
| sum | verified | 623.285 | 44.300 | False | n/a |
| sum | without-assume | 636.526 | 74.389 | False | n/a |
| sum | without-delete | 632.393 | 62.595 | False | n/a |
| sum | without-restrict | 632.933 | 116.196 | False | n/a |

| kernel | ratio | time (geometric, 95% bootstrap) | instructions |
| --- | --- | --- | --- |
| binary_search | checked/plain | 0.956 [0.912, 0.990] | n/a |
| binary_search | verified/plain | 0.955 [0.915, 0.995] | n/a |
| binary_search | verified/checked | 0.999 [0.964, 1.034] | n/a |
| binary_search | without-delete/verified | 1.003 [0.967, 1.039] | n/a |
| binary_search | without-restrict/verified | 1.020 [0.979, 1.064] | n/a |
| binary_search | without-assume/verified | 0.986 [0.958, 1.016] | n/a |
| dot | checked/plain | 1.379 [1.334, 1.428] | n/a |
| dot | verified/plain | 1.184 [1.119, 1.260] | n/a |
| dot | verified/checked | 0.858 [0.823, 0.900] | n/a |
| dot | without-delete/verified | 1.060 [1.020, 1.103] | n/a |
| dot | without-restrict/verified | 1.011 [0.961, 1.066] | n/a |
| dot | without-assume/verified | 1.015 [0.945, 1.094] | n/a |
| matvec | checked/plain | 1.851 [1.753, 1.962] | n/a |
| matvec | verified/plain | 1.161 [1.086, 1.230] | n/a |
| matvec | verified/checked | 0.627 [0.603, 0.650] | n/a |
| matvec | without-delete/verified | 1.408 [1.378, 1.439] | n/a |
| matvec | without-restrict/verified | 1.016 [0.983, 1.053] | n/a |
| matvec | without-assume/verified | 1.045 [1.005, 1.091] | n/a |
| prefix_sum | checked/plain | 2.167 [2.114, 2.221] | n/a |
| prefix_sum | verified/plain | 2.130 [2.059, 2.197] | n/a |
| prefix_sum | verified/checked | 0.983 [0.958, 1.010] | n/a |
| prefix_sum | without-delete/verified | 0.999 [0.979, 1.020] | n/a |
| prefix_sum | without-restrict/verified | 0.981 [0.948, 1.011] | n/a |
| prefix_sum | without-assume/verified | 0.975 [0.943, 1.007] | n/a |
| saxpy | checked/plain | 1.289 [1.245, 1.341] | n/a |
| saxpy | verified/plain | 1.132 [1.091, 1.174] | n/a |
| saxpy | verified/checked | 0.878 [0.849, 0.907] | n/a |
| saxpy | without-delete/verified | 1.030 [1.005, 1.054] | n/a |
| saxpy | without-restrict/verified | 1.005 [0.976, 1.034] | n/a |
| saxpy | without-assume/verified | 1.064 [1.010, 1.130] | n/a |
| sum | checked/plain | 1.189 [1.116, 1.262] | n/a |
| sum | verified/plain | 1.051 [0.993, 1.105] | n/a |
| sum | verified/checked | 0.885 [0.858, 0.916] | n/a |
| sum | without-delete/verified | 1.013 [0.987, 1.037] | n/a |
| sum | without-restrict/verified | 1.029 [1.000, 1.060] | n/a |
| sum | without-assume/verified | 1.016 [0.990, 1.045] | n/a |
