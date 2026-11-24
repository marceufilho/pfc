#ifndef PFC_SPEC_H
#define PFC_SPEC_H

/* Annotation transport only. See docs/decisions/0004-contract-macros-and-sum.md. */
#if defined(SPEC_KEEP) && defined(VERIFIED)
#error "SPEC_KEEP and VERIFIED are mutually exclusive"
#endif

#ifdef SPEC_KEEP

/* Compiler-owned markers: deliberately declared without runtime definitions. */
void __spec_requires(_Bool condition);
void __spec_ensures(_Bool condition);
void __spec_invariant(_Bool condition);
void __spec_assert(_Bool condition);
_Bool __spec_separated(const void *left, long left_len, const void *right, long right_len);

#define REQUIRES(condition) __spec_requires((condition))
#define ENSURES(condition) __spec_ensures((condition))
#define INVARIANT(condition) __spec_invariant((condition))
#define ASSERT(condition) __spec_assert((condition))
#define SEPARATED(left, left_len, right, right_len) __spec_separated((left), (left_len), (right), (right_len))

#else

/* Plain/checked input and VERIFIED erase annotations without evaluating them.
 * VERIFIED alone is not proof: only the future checker/emitter may add assumptions.
 */
#define REQUIRES(condition)
#define ENSURES(condition)
#define INVARIANT(condition)
#define ASSERT(condition)
#define SEPARATED(left, left_len, right, right_len)

#endif

#endif
