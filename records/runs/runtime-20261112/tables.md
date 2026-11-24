# Runtime effect (design §6; reported, not claimed)

Governor: powersave; pinned CPU: None -> run label **exploratory**; instruction counter: unavailable; timed runs: 1260; warm-ups: 126.

| kernel | variant | median ms | IQR ms | vectorized | median instructions |
| --- | --- | ---: | ---: | --- | ---: |
| binary_search | checked | 122.709 | 8.471 | False | n/a |
| binary_search | plain | 123.499 | 6.049 | False | n/a |
| binary_search | verified | 120.637 | 6.170 | False | n/a |
| binary_search | without-assume | 118.822 | 5.483 | False | n/a |
| binary_search | without-delete | 122.262 | 7.178 | False | n/a |
| binary_search | without-overflow-traps | 122.308 | 8.547 | False | n/a |
| binary_search | without-restrict | 119.138 | 5.999 | False | n/a |
| dot | checked | 800.548 | 15.470 | False | n/a |
| dot | plain | 627.913 | 16.838 | True | n/a |
| dot | verified | 684.831 | 20.301 | False | n/a |
| dot | without-assume | 678.490 | 18.763 | False | n/a |
| dot | without-delete | 731.642 | 14.277 | False | n/a |
| dot | without-overflow-traps | 626.367 | 15.561 | True | n/a |
| dot | without-restrict | 678.989 | 15.807 | False | n/a |
| matvec | checked | 936.756 | 12.647 | True | n/a |
| matvec | plain | 500.284 | 24.869 | True | n/a |
| matvec | verified | 620.811 | 14.813 | False | n/a |
| matvec | without-assume | 618.409 | 17.181 | False | n/a |
| matvec | without-delete | 889.978 | 18.301 | False | n/a |
| matvec | without-overflow-traps | 520.984 | 16.093 | True | n/a |
| matvec | without-restrict | 618.562 | 16.300 | False | n/a |
| prefix_sum | checked | 2194.715 | 16.021 | False | n/a |
| prefix_sum | plain | 972.051 | 20.190 | False | n/a |
| prefix_sum | verified | 2206.244 | 12.717 | False | n/a |
| prefix_sum | without-assume | 2205.541 | 14.523 | False | n/a |
| prefix_sum | without-delete | 2203.352 | 10.394 | False | n/a |
| prefix_sum | without-overflow-traps | 966.377 | 15.226 | False | n/a |
| prefix_sum | without-restrict | 2206.265 | 12.428 | False | n/a |
| saxpy | checked | 811.950 | 24.999 | False | n/a |
| saxpy | plain | 683.562 | 12.243 | True | n/a |
| saxpy | verified | 737.637 | 12.128 | False | n/a |
| saxpy | without-assume | 737.811 | 19.882 | False | n/a |
| saxpy | without-delete | 754.937 | 20.298 | False | n/a |
| saxpy | without-overflow-traps | 685.111 | 19.745 | True | n/a |
| saxpy | without-restrict | 738.452 | 19.862 | False | n/a |
| sum | checked | 607.940 | 11.593 | False | n/a |
| sum | plain | 470.274 | 22.987 | True | n/a |
| sum | verified | 540.311 | 17.691 | False | n/a |
| sum | without-assume | 538.500 | 14.610 | False | n/a |
| sum | without-delete | 538.970 | 17.645 | False | n/a |
| sum | without-overflow-traps | 469.082 | 17.920 | True | n/a |
| sum | without-restrict | 537.535 | 19.938 | False | n/a |

| kernel | ratio | time (geometric, 95% bootstrap) | instructions |
| --- | --- | --- | --- |
| binary_search | checked/plain | 0.984 [0.956, 1.011] | n/a |
| binary_search | verified/plain | 0.966 [0.942, 0.989] | n/a |
| binary_search | verified/checked | 0.982 [0.965, 1.000] | n/a |
| binary_search | without-delete/verified | 1.008 [0.992, 1.027] | n/a |
| binary_search | without-restrict/verified | 0.999 [0.980, 1.021] | n/a |
| binary_search | without-assume/verified | 0.998 [0.979, 1.017] | n/a |
| binary_search | without-overflow-traps/verified | 1.008 [0.990, 1.026] | n/a |
| binary_search | without-overflow-traps/plain | 0.973 [0.947, 0.996] | n/a |
| dot | checked/plain | 1.276 [1.262, 1.292] | n/a |
| dot | verified/plain | 1.091 [1.080, 1.103] | n/a |
| dot | verified/checked | 0.855 [0.847, 0.862] | n/a |
| dot | without-delete/verified | 1.065 [1.057, 1.072] | n/a |
| dot | without-restrict/verified | 0.995 [0.984, 1.007] | n/a |
| dot | without-assume/verified | 0.998 [0.987, 1.013] | n/a |
| dot | without-overflow-traps/verified | 0.918 [0.907, 0.928] | n/a |
| dot | without-overflow-traps/plain | 1.002 [0.990, 1.013] | n/a |
| matvec | checked/plain | 1.853 [1.825, 1.878] | n/a |
| matvec | verified/plain | 1.227 [1.204, 1.250] | n/a |
| matvec | verified/checked | 0.662 [0.655, 0.670] | n/a |
| matvec | without-delete/verified | 1.433 [1.415, 1.450] | n/a |
| matvec | without-restrict/verified | 1.000 [0.984, 1.017] | n/a |
| matvec | without-assume/verified | 1.000 [0.986, 1.015] | n/a |
| matvec | without-overflow-traps/verified | 0.845 [0.832, 0.857] | n/a |
| matvec | without-overflow-traps/plain | 1.036 [1.016, 1.057] | n/a |
| prefix_sum | checked/plain | 2.252 [2.237, 2.265] | n/a |
| prefix_sum | verified/plain | 2.264 [2.247, 2.278] | n/a |
| prefix_sum | verified/checked | 1.005 [1.002, 1.008] | n/a |
| prefix_sum | without-delete/verified | 1.000 [0.997, 1.003] | n/a |
| prefix_sum | without-restrict/verified | 1.000 [0.996, 1.004] | n/a |
| prefix_sum | without-assume/verified | 1.001 [0.998, 1.003] | n/a |
| prefix_sum | without-overflow-traps/verified | 0.440 [0.438, 0.443] | n/a |
| prefix_sum | without-overflow-traps/plain | 0.997 [0.989, 1.004] | n/a |
| saxpy | checked/plain | 1.192 [1.182, 1.203] | n/a |
| saxpy | verified/plain | 1.078 [1.069, 1.086] | n/a |
| saxpy | verified/checked | 0.904 [0.894, 0.914] | n/a |
| saxpy | without-delete/verified | 1.021 [1.012, 1.031] | n/a |
| saxpy | without-restrict/verified | 1.004 [0.995, 1.012] | n/a |
| saxpy | without-assume/verified | 1.002 [0.992, 1.012] | n/a |
| saxpy | without-overflow-traps/verified | 0.928 [0.919, 0.937] | n/a |
| saxpy | without-overflow-traps/plain | 1.001 [0.991, 1.011] | n/a |
| sum | checked/plain | 1.275 [1.253, 1.294] | n/a |
| sum | verified/plain | 1.139 [1.118, 1.163] | n/a |
| sum | verified/checked | 0.894 [0.884, 0.906] | n/a |
| sum | without-delete/verified | 0.998 [0.984, 1.012] | n/a |
| sum | without-restrict/verified | 0.998 [0.983, 1.013] | n/a |
| sum | without-assume/verified | 0.995 [0.980, 1.009] | n/a |
| sum | without-overflow-traps/verified | 0.876 [0.861, 0.892] | n/a |
| sum | without-overflow-traps/plain | 0.998 [0.973, 1.024] | n/a |
