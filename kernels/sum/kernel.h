#ifndef PFC_SUM_KERNEL_H
#define PFC_SUM_KERNEL_H

/* The caller supplies a readable region and representable intermediate sums.
 * See docs/sum-contract.md for the interface and its verification limits.
 */
long sum(const long a[], long a_len, long n);

#endif
