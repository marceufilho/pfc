# No-proposer control (post hoc)

Programs: 30; executable guard sites: 0.

| Condition | Guard sites removed | Programs with every site removed |
| --- | ---: | ---: |
| trivial invariant `1` at every loop | 0 | 30 |
| invariant written in the file | 0 | 30 |

## Admission (every required obligation, frozen admit())

| Invariant source | Programs admitted | Loops matched by the rule |
| --- | ---: | ---: |
| trivial invariant `1` at every loop | 1 of 30 | |
| counter rule | 0 of 30 | 9 of 30 |

Counter rule (fixed before the run): Syntactic counter bound, one candidate per loop in preorder, no solver and no execution. A while/for loop matches when (a) its condition, conversions aside, is a single comparison `i OP e` with OP in {<, <=, >, >=, !=} (or the mirrored `e OP i`, read as `i OP' e`; the left operand is tried as the counter first) where `i` is a scalar local or parameter and `e` has no array read, no call, no occurrence of `i` and no variable assigned inside the loop; (b) the loop (body at any depth, plus a for update) assigns `i` exactly once, at the top level of the body or as the for update, as `i = i + c` or `i = i - c` with a positive literal `c`; (c) the initial value `i0` is the for initializer when it sets `i`, otherwise the last top-level initializer or assignment of `i` before the loop in the enclosing block, with no nested statement assigning `i` after it, and `i0` is a literal (possibly negated) or an expression with no array read, no call, no occurrence of `i` and no variable assigned inside the loop or between that statement and the loop. Candidate, increasing counter (`i + c`): `<` or `!=` gives `i0 <= i && i <= e`; `<=` gives `i0 <= i && i <= e + 1` when c == 1, else `i0 <= i`. Decreasing counter (`i - c`): `>` or `!=` gives `e <= i && i <= i0`; `>=` gives `e - 1 <= i && i <= i0` when c == 1, else `i <= i0`. Any other loop (including an increasing counter under `>`/`>=` or a decreasing one under `<`/`<=`, a conjunction as condition, or a counter whose initial value comes only from REQUIRES) does not match and gets `1`. Texts use pfc_eval.expressions.render. The candidate is only a proposal: the frozen admit() decides.
