# Experimental Design: Validating the Proof-Guided Compiler

Revision of 2026-11-05 (supersedes the draft of 2026-11-03). Author: Marcio Filho.

This document fixes, before any held-out data is collected, what would count as the compiler working and
what would count as it failing: the hypotheses, the baselines, the sample sizes, the statistics and the
figures. It is the prospective pre-registration. Its compiler-freeze draft and final evaluation protocol are
versioned separately; amendments must be disclosed before held-out outcomes are inspected. The implementation it evaluates is the one recorded in
[proof-led-compiler.md](proof-led-compiler.md), [docs/METODOLOGIA.md](docs/METODOLOGIA.md) and
[docs/FREEZE.md](docs/FREEZE.md); its current state is vc-v4 (ADR-0030) with the independent WP/RTE
oracle already executed once on the development corpus (ADR-0031).

Section 11 records the earlier revisions; section 14 records the pre-freeze reconciliation.

**Status at the compiler freeze.** This is a prospective protocol for a separate post-freeze evaluation
phase, not completed experiments or an already registered study. `freeze-v4` preserves this design and
the compiler baseline. Before `eval-v1`, fill the model/budget/profile settings and disclose amendments
with their reasons, before accessing held-out outcomes. The final protocol is frozen at `eval-v1`.
Starting the phase, new commands and new live-data populations require the applicable authorization;
this freeze task implements none of the harness. Existing G2 remains an architecture demonstration.
The quantified G2' proposal and language extensions remain future work; E1–E14 are a distinct, still
unexecuted study within the current subset, not prerequisites for `freeze-v4`.

## 1. Research questions and hypotheses

Four questions, each with a falsifiable hypothesis, a null, one primary metric and a threshold fixed now.
RQ1 and RQ2 are the primary outcomes; RQ3 and RQ4 are secondary. A threshold changed after data exists is
reported as a deviation, in the paper, with the original value.

| RQ | Hypothesis | Null | Primary metric | Pre-registered threshold |
| --- | --- | --- | --- | --- |
| **RQ1 Soundness.** Are the deleted checks really proved away? | H1: on inputs that satisfy `REQUIRES` *and execute without undefined behavior*, the verified build never diverges from the checked or plain build; every deletion cites all required path obligations; removing a precondition a deletion depends on restores the check | H0: at least one divergence, untraceable deletion, or a deletion that survives removal of its precondition | Differential failures; break-it sensitivity and specificity; injection rejection; mutation score | 0 failures in N = 1,000 inputs per kernel, with the exact binomial upper bound reported; sensitivity and specificity 100% under the definitions of E1.2; 100% injections rejected; mutation score ≥ 0.90 |
| **RQ2 Capability.** How much of what the specs promise does the verifier discharge, and does an independent verifier agree? | H2: ≥ 95% of required obligations prove within 10 s on the held-out kernels, and every pfc/WP disagreement is adjudicated to a named category | H0: < 95% proved, or a disagreement that cannot be classified | Proved / required; 2 × 2 agreement with WP before and after adjudication | 95%; zero unclassified disagreements |
| **RQ3 Proposer.** Does an LLM with counterexample feedback find inductive invariants more often than a non-LLM template baseline, on tasks withheld from local development? | H3a: success@5 of L1 exceeds B1 on the held-out set; H3b: feedback (L1) exceeds independent sampling (L2) | H0: no difference (paired, per function task) | success@5 per condition with 95% cluster-bootstrap CI; paired difference | Difference ≥ 0.20 with a 95% CI excluding 0, evaluated once on the sealed held-out set |
| **RQ4 Runtime** (reported, not claimed). What do the facts cost or buy? | Descriptive; H4: verified ≤ checked on every kernel | — | Median time ratios with bootstrap CI; deterministic instruction counts; vectorization remarks | None; the ratio and its interval are the result |

**Pre-registered prediction for RQ3.** On the development corpus the pinned model solved every invariant on
the first attempt, and every hand invariant of the six kernels lies inside the B1 template family. On loops
of that kind the expected outcome is L1 ≈ B1 ≈ 1.0 and L1 − L2 ≈ 0: a ceiling with little discriminating power. RQ3 is
therefore informative only if the held-out set contains loops whose invariants fall outside the B1 family.
Section 5 requires that by construction. An interval for L1 − B1 including zero is inconclusive, not evidence of equivalence. Report observed
successes and measured costs. A hand invariant outside the written template grammar does not establish
that no sufficient invariant exists within the family; the composition requirement is a design heuristic.

Two things are deliberately not hypotheses. The compiler is not claimed to be faster than plain C, and the
verifier is not compared for power against Frama-C: WP is the oracle that checks `vc.py`/`wp.py`, not a
competitor.

## 2. Threats to validity, stated first

The author who built the tool cannot also choose the test kernels after seeing the tool; training exposure cannot be ruled out for public or algorithmically familiar programs; random testing cannot pass for proof; and harness work done
after the freeze cannot be mistaken for tuning the compiler. Each threat names the experiment that
addresses it.

| Threat | Kind | How it is controlled | Where |
| --- | --- | --- | --- |
| The VC generator is wrong and tests written by the same person miss it | Construct, internal | Independent oracle (handwritten ACSL, Frama-C/WP+RTE, different memory model and prover) on the same programs; mutation testing of `vc.py`, `wp.py`, `authorization.py`, `emit.py`; break-it on every conjunct | E1, E2 |
| Random differential testing proves nothing about absence | Conclusion | Report the exact binomial bound (0 failures in 990 seeded cases gives p ≤ 0.302% at 95%); call the differential test a smoke test; the proof is the evidence, the oracle its check | E1 |
| A differential test on an undefined execution proves nothing at all | Construct | Per-kernel value bounds that keep every intermediate defined, checked by the independent oracle during generation; no input with an overflowing intermediate is counted | E1.1 |
| Kernels were chosen because they verify | External, selection | Sealed held-out set written after the freeze tag, by the advisor or a colleague where possible; SHA-256 committed before any run; each name checked for subset expressibility before sealing; external corpora with rejections reported by rule. **Amendment 34 (2026-11-10):** the held-out set is instead selected mechanically from two external SV-COMP families (`array-tiling`, `array-multidimensional`) by a rule fixed before any run, ported by the author (external, not blinded), rejections recorded per task | E2, E3 |
| The proposer prompt was tuned on the kernels it is scored on | Internal | Development set for prompt design; prompt, model identifier, settings and K frozen at the evaluation tag; held-out evaluated once | E3 |
| The LLM has seen public benchmarks in training | Internal, construct | Three strata: ported public (Code2Inv/SV-COMP loops rewritten into the subset), obfuscated ported, private held-out; a public-minus-obfuscated gap flags memorization | E3 |
| LLM nondeterminism and silent model updates | Conclusion | Pinned model identifier, settings recorded per model, R = 5 runs, every response cached and archived so the paper reproduces offline; variance reported | E3 |
| Small n: roughly 12–20 loops | Conclusion | Cluster bootstrap by kernel, Wilson intervals, exact tests; power statement; no claim finer than the interval | E3, §7 |
| Runtime noise swamps small effects | Conclusion | Three interleaved blocks of 10 runs; paired bootstrap on ratios; deterministic instruction counts as a second measure; vectorization remarks as a categorical witness | E4 |
| Results depend on the solver budget | Construct | Timeout sweep 1–60 s reported as a curve for both pfc/Z3 and WP/Alt-Ergo | E2 |
| The "checked" baseline approximates what safe languages emit | Construct | Stated as a defined semantics (trap on every index and division), not as Rust or Swift; no cross-language claim | E4, §10 |
| Author is implementer and evaluator | Internal | Two tags: `freeze-v4` for the compiler core, `eval-v1` for the harness; the trusted files are byte-identical between them; every number comes from `make report` at `eval-v1` | §9 |
| The subset is small, so generality is unknown | External | Stated as the main limitation; external corpora give a first estimate of how much real code the subset admits | §10 |

## 3. Experiment 1 — Soundness: are the proofs load-bearing?

Four tests, none statistical in the usual sense: a single failure refutes H1. Statistics add an honest
statement of what zero failures does and does not establish.

**E1.1 Differential test.** For each kernel and each of the three builds, draw N = 1,000 inputs from
`REQUIRES` by rejection sampling with a fixed seed: 10 fixed boundary cases (including n = 0, n = a_len,
n = a_len − 1, rows = 0, cols = 1) plus 990 seeded cases. Sizes are log-uniform in [1, 2^16]. **Values are
bounded per kernel so that every intermediate of the mathematical oracle fits in the target `long`**: for
`sum`, `prefix_sum`, `saxpy` and the ports, |value| ≤ 2^40; for `dot` and `matvec`, |value| ≤ 2^20 so that
products and their sums over 2^16 terms stay below 2^57. The independent oracle evaluates each case before
it is counted and rejects any case with an overflowing intermediate; rejections are logged. Compare return
value and every array element pairwise among plain, checked and verified, and against the oracle. A
timeout, crash or incomplete output is a failure, never agreement. *Analysis:* zero failures bound the
per-input disagreement probability relative to this generator at p ≤ 0.302% (exact binomial on the 990
seeded cases, 95% one-sided); the rule-of-three value 3/990 is reported beside it. The bound assumes independent draws from the documented accepted-input distribution, excluding fixed
boundary cases. It is not a bound over all inputs or a certificate that the generator/compiler is correct;
proof results remain conditional on the stated trusted implementation.

**E1.2 Break-it, with sensitivity and specificity.** For each kernel and each conjunct of each `REQUIRES`,
drop that conjunct alone and rerun verification and emission. Because contract text enters every obligation
identity, "changed" and "unchanged" are defined on **per-site verdicts and emitted C**, never on obligation
IDs. *Sensitivity:* for every conjunct some guard depends on, that guard's site must lose its proof and the
guard must reappear in the emitted C (expected 100%). For a pure `SEPARATED` conjunct the pre-registered
dependent consequence is withdrawal of the restrict fact, per ADR-0017, not a bounds failure; it is counted
in a separate column. *Specificity:* for every conjunct nothing depends on, every site verdict and the
emitted executable C must be identical (expected 100%). Both are reported, because a tool that restores
checks on every perturbation is demonstrating fragility, not dependence.

**E1.3 Injection with executed counterexamples where reachable.** For each loop, generate 20 candidate
mutations systematically: off-by-one at each bound, reversed inequality, a dropped conjunct, a conjunct over
the wrong variable, plus the literal `false` and the curated near-miss. Classify candidates using an independently justified violating state or reference argument. Dropping a
conjunct can leave a valid invariant; report false, valid-but-insufficient, valid-and-sufficient and
unclassified candidates separately. The 100% rejection criterion applies to independently established
false candidates, with the actual denominator disclosed rather than forcing twenty negatives. **Counterexample reproduction is restricted to models that denote entry states**:
initiation failures and bounds failures whose model binds only parameters and entry-derived values. Those
are reconstructed conservatively, run against the checked binary, and the trap or invariant violation must
occur (expected 100% of reproducible models). Preservation counterexamples denote arbitrary loop states that
need not be reachable; they are reported as abstract, with their count, and are not a reproduction target.
A model classified as reproducible that does not reproduce requires investigation of encoding,
reconstruction and harness semantics; it is not automatically a compiler encoding defect.

**E1.4 Mutation testing.** Generate mutants of `vc.py`, `wp.py`, `authorization.py` and `emit.py` with a
fixed operator set (relational boundary `<`/`<=`, constant ±1, negated condition, deleted statement, swapped
operands), target 30–50 mutants, operator list and seed committed. Run the test pyramid in layers against
each: unit, golden, differential, break-it, injection, and the WP oracle **only for mutants that survive the
first five layers** (the oracle run costs minutes per mutant). Kills obtained only by golden-file
comparison are reported in their own column, since a golden catches any text change and says nothing about
semantics. *Metric:* mutation score = killed / (total − equivalent) with a Wilson 95% interval; each
survivor is classified by hand as equivalent or a real gap; real gaps get a new test or are listed as a
limitation. Target ≥ 0.90.

*Outputs.* Table II (per kernel: N, rejected-for-overflow count, failures, bound; break-it sensitivity,
specificity and separation consequences; injections rejected, reproducible, reproduced) and Figure 6
(mutant × test-layer kill matrix). *Amendments 19, 22 and 23 (2026-11-09, `paper/AMENDMENTS.md`): E1.2, E1.3,
E1.5 and the pfc budget sweep also run on the ported and composed public strata as additional, non pre-registered
evidence, and Table II(b) pools by stratum; the witness and refutation sampler assigns `REQUIRES` equalities after
sampling; an entry model whose head violation is observed before the interpreter's step budget counts as reproduced.*

## 4. Experiment 2 — Verifier capability and the oracle

**Corpora.**

1. *Development* (6 kernels: `sum`, `dot`, `saxpy`, `prefix_sum`, `matvec`, `binary_search`). Used while
   building; results reported, no evidential weight for RQ2 or RQ3.
2. *Held-out* (≥ 6 kernels, target 10). Written after `freeze-v4`, by the advisor or a colleague where
   possible, otherwise by the author from the fixed name list in §5. **Superseded by amendment 34 (2026-11-10,
   ADR-0057): the set is every true-verdict task of SV-COMP `c/array-tiling` and `c/array-multidimensional`,
   ported by the fixed rules of `pfc_eval/svcomp_arrays.py`; the composition rule is reported, not required;
   tasks the frozen compiler or `admit()` rejects are recorded in `heldout/PROVENANCE.json`.** **Every name is checked against the
   subset before sealing** (`pfc ir` must accept the signature pattern); a name the subset cannot express
   is replaced from the reserve list before the hash is committed, and the replacement is recorded. The set
   is sealed by SHA-256 committed before the first run, and run once.
3. *External*. The eight ACSL-by-Example ports already in `bench/external/acsl/`; the subset-admissible
   fraction of a fixed list of SV-COMP ReachSafety-Loops and Code2Inv tasks, with admission rate by
   rejection rule. The screening already performed (20 Code2Inv tasks, 20 rejected) is reported as the
   first data point.

**Metrics.** Per kernel and obligation kind (initiation, preservation, bounds, division, callee
precondition, postcondition, overflow reported separately; entry-consistency queries counted separately
from obligations): total, proved, failed, timed out at 10 s; solver time median and maximum; sites
emitted and sites deleted. The *timeout sweep* reruns every obligation at 1, 2, 5, 10, 30 and 60 s and plots
proved fraction against budget for pfc/Z3 **and** for WP/Alt-Ergo on the same functions (Figure 2). The
cache keys include the timeout, so no cached result crosses budgets.

**Oracle protocol.** The protocol executed in ADR-0031 is kept: **handwritten ACSL**, not exported from
pfc, since an export would retest the translation under test. Frama-C 32.1 WP+RTE+smoke, Typed memory,
Alt-Ergo as the primary prover, the digest-pinned image, no network, 10 s per query. The held-out kernels
receive ACSL written by the same procedure (same `REQUIRES`, explicit validity and separation, `assigns`,
`terminates \false`, named bounds/division assertions, no content or accumulator bounds). A secondary
run using a separately identified image with Z3 installed,
with Z3 added to WP's prover list is reported as a separate column, because it tests whether a gap is
prover capability; it does not replace the Alt-Ergo column, which is the independent one. For every
compared property the outcome pair (pfc proved or not, WP valid or not) enters a 2 × 2 table. Every
off-diagonal cell is adjudicated by hand into one of: bug in pfc, bug in the ACSL, WP/prover
incompleteness (e.g. nonlinear arithmetic Alt-Ergo does not automate), pfc incompleteness. The before and
after tables are both published. The acceptance criterion is zero *unclassified* disagreements. The
existing `matvec` matrix-bound timeout remains **inconclusive: cause unestablished**. Nonlinear
automation is a plausible explanation, not an established cause; see
[docs/ORACLE-FRAMA-C.md](docs/ORACLE-FRAMA-C.md). Preserve its raw result in every table. An unresolved
cause is reported explicitly and does not satisfy the original zero-unclassified criterion. A stronger
diagnosis requires separate evidence; it cannot replace the observed timeout.

**Analysis.** Proportions with Wilson 95% intervals; no significance test. The held-out proportion is the
number the abstract quotes.

*Outputs.* Table I (corpus summary), Table III (verifier results by corpus and kind; oracle 2 × 2 before and
after), Figure 2 (proved fraction vs budget, two tools).

## 5. Experiment 3 — The proposer

**Unit of analysis.** One function task, potentially containing several loops. Existing `admit()` checks
a complete invariant list and the complete program. Strip all handwritten invariants; a run succeeds
only when that API accepts, including entry consistency, required dependencies, relevant arithmetic and
removal of all executable guards. Use K = 5 complete proposals and 10 s per query. Per-loop diagnostics
are secondary; correlated loops are not independent successes. E3 samples task/runs and clusters by kernel.

**Conditions.**

| Condition | What it is | Role |
| --- | --- | --- |
| B0 Hand | The author's invariant | Ceiling; shows the loop is provable |
| B1 Template | Candidates: `c <= v`, `v <= w + c` over the loop's scalar variables and length parameters, c ∈ {−1, 0, 1}; Houdini-style elimination of candidates refuted by initiation or preservation; at most five refinement rounds, reported as bounded rather than a guaranteed fixpoint. Family fixed here and not widened after sealing | Deterministic non-LLM baseline |
| L1 LLM + feedback | Prompt holds the function, the failing obligation and the counterexample; each rejection feeds the next attempt | The proposed method |
| L2 LLM, no feedback | K independent samples from the same prompt without counterexamples | Ablation for H3b |
| L3 Open-weight model + feedback | Same as L1 through an OpenAI-compatible local endpoint, model and weights hash recorded | Robustness, cost, offline fallback |

**Held-out composition requirement.** At least half of the held-out loops must have a hand invariant (B0)
**outside the B1 family**: a non-unit stride (`i <= n + stride − 1`), a constant beyond ±1, a relation over
three variables (`lo + count == i`), or a disjunction. The author of the held-out set is told the B1 family
and this requirement, nothing about the model. Fixed name list, each with its reserve: `strided_sum`
(stride ≥ 2), `compact`/`copy_if` (write index ≤ read index), `partition`, `argmax`, `two_sum_sorted`
(two pointers), `running_max`, `transpose`, `insertion_sort` inner loop, `reverse`, `count_if`,
`max_element`, `pairwise_diff` (i and i+1), `window_sum` (fixed width ≥ 2). `histogram` and `rotate` from
the earlier draft are removed: the first needs a quantified content precondition, the second needs `%`,
and the subset rejects both.

**Protocol.** Prompt designed on development loops only, frozen at `eval-v1` with model identifier and
settings. *Amendment 8 (2026-11-05, pre-held-out, `paper/AMENDMENTS.md`): the instruction names the task's proof
goals read from the program and the overflow requirement the admission imposes on it (protocol
`pfc-eval-proposer-v3`); the public ported stratum informed this change and
counts as development data for the prompt.* *Amendments 20 and 21 (2026-11-09): the public strata were rerun
with R = 5 for L1 and L2 and the one-run campaigns superseded in `paper/evidence.json`; B1 also runs on the public
strata, and its probe is split in halves above the frozen VC size cap, with splits recorded.* **Sampling settings are recorded per model**: temperature 0.7 where the API accepts it, otherwise
omitted and the omission recorded (the current pinned model rejects temperature). Each condition runs
R = 5 times per function task, with seeds where honoured and distinct sampling identities otherwise;
every request and response is cached and archived under the research-transcript flag. Held-out tasks
receive one campaign per condition, with those R runs, in
that configuration; no re-prompting after seeing held-out results. Rejected proposals are classified (not
inductive, too weak for the obligation, out-of-scope variable, unparseable, protocol error; *amendment 15,
2026-11-06: plus abstained, a well-formed reply with an empty invariant list*).

**Metrics.** success@K per function task (fraction of R runs that succeed), success@k curves, attempts to first
success, wall time and tokens per success. Mean success@5 per condition with a 95% cluster bootstrap
(B = 2,000, seeded, resampling kernels). Paired differences L1 − B1 and L1 − L2 with the same bootstrap and
an exact sign test on per-task majority outcomes. Three planned contrasts (L1 − B1, L1 − L2, L1 − L3) with
Holm correction; nothing else is tested.

**Contamination check.** Public tasks cannot be run as published: the subset rejects `int main`,
nondeterministic inputs and `int` state. Three strata therefore are: *ported public* (30 Code2Inv or
SV-COMP loops rewritten as `long` functions with explicit lengths, the port script committed), *obfuscated
ported* (α-renamed, constants perturbed, loop direction flipped where semantics allow), and *private
held-out*. Porting itself weakens the memorization test; the paper says so. A ported-minus-obfuscated gap
above 0.15 with an interval excluding zero is reported as transformation sensitivity, not proof of
memorization: transformations can also change
reasoning difficulty. Report all strata; the private held-out stratum supplies the headline by design,
regardless of the measured gap.

**Power.** No numerical power calculation has been performed. A bootstrap method and twelve tasks do
not themselves establish a detectable effect. Retain the 0.20 practical threshold without promising
power to detect it. Before `eval-v1`, record a sensitivity calculation with explicit paired distributions
and clustering assumptions, without using held-out outcomes.

*Outputs.* Table IV, Table VIII (failure classes), Figure 3 (success@k curves), Figure 4 (per-task paired
differences, forest plot), Figure 7 (strata).

## 6. Experiment 4 — Runtime effect, reported not claimed

**Design.** Per kernel: plain, checked, verified, and the ablations verified-minus-delete,
verified-minus-restrict, verified-minus-assume. All binaries from the same clang with the existing
`BENCH_FLAGS` (`-std=c11 -O3 -march=native -Wall -Wextra -Werror`), flags recorded. Inputs of 2^24
elements, 50 repetitions inside `main`. Three interleaved blocks of 10 runs after 3 warm-ups (A B C, A B C,
…). **Governor pinned to `performance` and the machine otherwise idle; if the governor cannot be set, the
run is exploratory and labelled so, as the existing bench code already does.**

**Measures.** Wall time per run (hyperfine). Instructions retired per run from `perf stat -e instructions`
where perf is available; **otherwise `valgrind --tool=cachegrind` simulated instruction counts where supported**,
with repeatability checked for the recorded binary, input and environment, with the tool named in the table. Vectorization remarks per
build (`-Rpass=loop-vectorize`, `-Rpass-missed=loop-vectorize`) as a categorical witness.

**Analysis.** Ratios verified/plain, checked/plain, verified/checked on the log scale, paired by block;
95% bootstrap over blocks and runs (B = 2,000, seeded). Instruction ratios beside time ratios; when they
disagree, report and investigate both. Retired instructions, simulated instructions and elapsed time
measure different quantities; one does not override the other. Any fact that slows a kernel is
reported with the same prominence as one that speeds it. No significance test.

**What this can and cannot say.** It can say what the checks cost in this subset and what the facts bought
back, with intervals. It cannot say anything about Rust, Swift or any safe language, and it cannot support
a faster-than-C claim.

*Outputs.* Table V, Figure 5.

## 7. Statistical methodology

- **Intervals over p-values.** Proportions use Wilson intervals; differences and ratios use a seeded
  nonparametric bootstrap with B = 2,000. Nested observations resample the outer unit; an iid bootstrap is
  reported as a sensitivity check.
- **Paired comparisons** on the same loops or runs; the difference is computed per unit, then bootstrapped.
- **Tests** only in E3, only the three planned contrasts, Holm-corrected, with an exact sign test as a
  companion. An interval including zero is inconclusive, not evidence of no effect.
- **Effect sizes.** Risk differences for success rates; log-ratios for times and instruction counts.
- **Seeds and repeats.** Every stochastic component has a recorded seed and is repeated.
- **Pre-registration.** This document is the pre-registration. Deviations are listed in the paper with the
  original value and the reason.
- **Negative results stay**, with the same table and figure they would have had.
- **Provenance.** Every number comes from `make report` on saved evidence at `eval-v1`.

## 8. Harness work required before `eval-v1`

These are planned additions between tags. `validation/freeze/protected.sha256` covers every existing
`pfc` module, frontend headers and locked configuration, including admission and the current adapter.
New `pfc_eval/` modules (root package; ADR-0033 moved the harness out of `pfc/eval/`) and approved standalone tools call frozen APIs; protected modules cannot import
them. Existing regression/oracle assets have a separate baseline inventory. See ADR-0032.

| Item | Status at `freeze-v4` | Work |
| --- | --- | --- |
| E1.1 generator with log-uniform sizes and per-kernel defined-arithmetic bounds | Corpus uses lengths ≤ 64, values ±100 | New reviewed profile; oracle rejection of overflowing cases |
| E1.2 per-conjunct break-it with verdict/C-based change definition | Drops whole `REQUIRES` nodes | Conjunct splitting; separation column |
| E1.3 systematic injection generator and entry-state reproduction | Literal false + curated near-miss; conservative replay | Generator; reproducibility classifier |
| E1.4 mutation runner, 30–50 mutants, layered kill matrix | 3 hand mutants | Mutator with fixed operator set and seed |
| E2 timeout sweep for pfc and WP | Not implemented | Driver over existing CLI/oracle |
| E2 held-out ACSL | 14 handwritten files | ≥ 6 more, same procedure |
| E3 B1 Houdini template baseline | Not implemented | Candidate enumeration over `admit()` |
| E3 L3 open-weight endpoint | Existing adapter/settings fixed to curated protocols | New evaluation-only transport/settings; unchanged admission consumes candidate text; no secret in argv |
| E3 Code2Inv/SV-COMP port and obfuscation scripts | Screening only | Port script, obfuscation script |
| E4 interleaved blocks, cachegrind fallback, remarks capture | Three warmups, ten runs, perf unavailable | Block scheduler; counter fallback |

Estimated effort: three to four weeks.

## 9. Sequencing and reproducibility package

1. Tag `freeze-v4` on the current tree, after the review in [docs/FREEZE.md](docs/FREEZE.md). This is the
   compiler under evaluation.
2. Implement §8. Commit this document at `freeze-v4` and again at `eval-v1`.
3. Finalize settings and disclose protocol amendments. Generate canaries, hash the final held-out bytes
   and commit the inventory before evaluation. Only documented parsing/eligibility checks precede sealing;
   no proposer or proof feedback may guide selection.
4. Tag `eval-v1` with the final protocol and inventories. Verify all protected/baseline hashes against
   `freeze-v4` and publish the full path diff; new harness files are listed separately.
5. Run everything once; write the Deviations paragraph.

Checklist:

- [ ] `freeze-v4` and `eval-v1` tags with commit hashes cited in the paper
- [ ] Protected/baseline hashes identical across tags; additions reviewed and full diff published
- [ ] `HELDOUT.sha256` committed before the held-out kernels; expressibility check recorded
- [ ] Frama-C image digest (already pinned in `validation/frama-c/manifest.json`) and the clean-validation
      image digest in the paper; cvc5 is out of scope and not promised
- [ ] `logs/llm/` with every prompt and response, model identifier and per-model settings; replay offline
- [ ] Seeds for input generation, bootstrap, run order and LLM sampling in `bench/SEEDS.md`
- [ ] `make paper` runs `make report` and rebuilds every figure and table; LaTeX includes them
- [ ] `bench/ENV.md` with CPU model, governor, kernel, clang, flags, counter tool, block timestamps
- [ ] Mutant set, injected invariants, port and obfuscation scripts committed
- [ ] Tagged clone reproduced on another physical machine (the clean-container run of 20f is same-host)
- [ ] Archive of the tag plus `bench/results/` and `logs/` with a DOI
- [ ] One-page `REPRODUCE.md`

## 10. What this design can and cannot establish

It can establish: that the loop closes end to end with an untrusted proposer and a trusted checker;
soundness evidence of a stated strength (proof, independent oracle agreement on matched properties,
mutation score, random testing demoted to a smoke test with a bound); whether an LLM with feedback beats a
fixed template baseline on template-hard unseen loops, with an interval; and what checks cost and facts buy
back in this subset.

It cannot establish: generality beyond the subset; anything about Rust, Swift or Lean; a faster-than-C
result; transfer across model versions; superiority over Frama-C or Dafny; or absence of undefined behavior,
since accumulator overflow is reported, not proved, on both sides of the oracle.

The results that would still make a good paper: L1 ≈ B1 on template-hard loops is a useful negative; a
soundness bug found by the oracle or a mutant is reported with the fix and both tables. The only outcome
this design cannot absorb is a soundness failure that is not found, which is why E1 has four independent
tests and the oracle is not optional.

## 11. Revisions from the 2026-11-03 draft

| Change | Reason |
| --- | --- |
| E1.1 values bounded per kernel; overflowing cases rejected by the oracle | ±2^40 values with 2^16-element `dot`/`matvec` overflow `long` in every build; agreement on undefined executions is not evidence, and the project's own rules forbid using it |
| E1.2 "changed" defined on site verdicts and emitted C; `SEPARATED` consequence pre-registered | Contract text enters obligation IDs, so IDs always change; pure separation withdraws a restrict fact (ADR-0017), not a bounds proof |
| E1.3 reproduction restricted to entry-state models; preservation models reported as abstract | Preservation counterexamples need not be reachable; a 100% reproduction target was unmeetable by construction |
| E1.4 oracle only on surviving mutants; golden-only kills in a separate column | Oracle cost per mutant; goldens kill any text change |
| E2 handwritten ACSL instead of `pfc export-acsl`; Alt-Ergo primary, Z3-in-WP secondary; sweep on both tools | ADR-0031 rejected export as retesting the trusted translation; the pinned image ships Alt-Ergo only; the matvec timeout has no established root cause |
| RQ2 criterion "zero unclassified disagreements" | Retained; matvec remains inconclusive with cause unestablished |
| Held-out list: `histogram` and `rotate` removed; expressibility check before sealing; ≥ half template-hard | Both are frontend-rejected (quantified contents, `%`); development evidence shows a ceiling for template-family invariants |
| RQ3 pre-registered prediction added; B1 family frozen | Live runs solved every development and probe invariant on the first attempt; the comparison is informative only off the ceiling |
| Temperature recorded per model, omitted where rejected | The pinned model does not accept temperature |
| L3 via OpenAI-compatible local endpoint | Single adapter with fixed base URL today |
| Contamination strata renamed "ported public" | 20/20 Code2Inv tasks are rejected as published |
| E4 cachegrind fallback; governor requirement explicit | perf unavailable and governor `powersave` on the development machine |
| Two tags with trusted-file hash identity | Separates harness work from the compiler under evaluation |
| cvc5 removed from the container checklist | Out of scope per CLAUDE.md |
| Added §8 harness inventory | Makes the gap between design and implementation explicit before the freeze |

## 12. Methodological sources and amendments after the literature check (2026-11-05)

Primary sources consulted for the evaluation method, separate from the compiler literature in
`docs/references.md`:

- Biderman et al., *Lessons from the Trenches on Reproducible Evaluation of Language Models* (2024):
  publish exact prompts and code at a named commit, report the task/codebase version, share raw outputs, report
  uncertainty over runs, inspect outputs qualitatively before full scale (`docs/references.md` 97).
- Reuel et al., *BetterBench* (NeurIPS 2024): across 24 benchmarks, 17 provide no script that replicates the
  published numbers and 14 never evaluate the same model more than once; contamination safeguards are rare;
  documented limitations are a criterion of the checklist (`docs/references.md` 98).
- van der Kouwe et al., *Benchmarking Crimes* (2018; EuroS&P 2019 as *SoK: Benchmarking Flaws in Systems
  Security*): A1 unreported degradation, B4 no significance, B5 incorrect averaging across scores, C3 same
  data for calibration and validation, D1 missing baseline, E4 components not tested incrementally, F1/F2
  missing platform and versions, F4 relative numbers only (`docs/references.md` 99).
- Chen and Furia, *Robustness Testing of Intermediate Verifiers* (2018): equivalent program variants built by
  mutation and metamorphic rules must verify identically; 16 of 135 Boogie programs exposed brittleness.
- Matton et al., *On Leakage of Code Generation Evaluation Datasets* (2024) and the ClassEval-Obf renaming
  study (2025): held-out sets and semantics-preserving obfuscation as contamination probes.
- InvBench/Quokka (2025): LLM-based invariant synthesis does not yet clearly beat the symbolic baseline
  UAutomizer; Loopy (FMCAD 2024): Houdini raised GPT-4 success by 30.7%, so a template baseline is not a
  straw man. The 2025 study *LLM for Loop Invariant Generation and Fixing* evaluates no non-LLM baseline,
  which this design treats as the gap to avoid.
- Registered Reports tracks at ESEM and MSR, and the 2026 OSF template proposal: protocol and analysis fixed
  before data; only confirmatory studies receive in-principle acceptance; deviations disclosed.
- Grace et al., Anthropic, *Demystifying Evals for AI Agents* (9 Jan 2026; `docs/references.md` 100): evaluation
  harness distinct from the agent harness;
  isolated trials from a clean state; several trials per task; capability evals start at a low pass rate and
  graduate into regression suites once they saturate.
- Böckeler, *Harness Engineering for Coding Agent Users* (martinfowler.com, 2026) and Hashimoto, *My AI
  Adoption Journey* (2026): computational (deterministic) sensors before inferential ones; a harness is built
  from observed failures; "if sensors never fire, is that a sign of high quality or inadequate detection?"
- Husain, *Your AI Product Needs Evals* (2024): error analysis on real traces before automated metrics;
  binary labels first; no generic metrics.

Amendments adopted from this reading, pre-registered here:

1. **Replication script is mandatory.** `make paper` regenerates every table and figure from saved evidence
   at `eval-v1`; the paper is not submitted without it (BetterBench's most-missing item).
2. **E1.5 Metamorphic robustness.** The obfuscation script of E3 is also applied to the development and
   held-out kernels: α-renaming, declaration reordering and commutative operand swaps produce variants that
   are compared using a saved site mapping. Renaming need not preserve C bytes. Compare mapped verdicts
   and observable behavior; normalize C only under a specified normalization. Reorder declarations only
   when initialization, scope, effects and evaluation order are preserved. Finite tests are empirical
   validation, not a proof of transformation equivalence. Separate verdict brittleness from divergence.
3. **Canary in the held-out files.** Each held-out kernel carries a unique random identifier in a comment so
   future training-set inclusion can be checked; the identifiers are listed in `HELDOUT.sha256`'s companion
   file (BetterBench contamination safeguard).
4. **Regression versus capability framing.** The development corpus is the regression suite and is expected
   near 100%; the held-out set is the capability evaluation. A held-out condition at 100% is reported as a
   ceiling, not as a capability result (Anthropic).
5. **E4 reporting rules from the benchmarking-crimes list.** Absolute medians beside ratios (F4), geometric
   aggregation only (B5), platform and versions (F1/F2), every slowdown reported (A1), each fact ablated
   separately (E4). No aggregate across kernels without the per-kernel rows (F3).

All graders in this design are computational: Z3 verdicts, compiled binaries, WP goals, byte comparisons.
No LLM-as-judge is used. Computational graders still require independent expected results, negative
controls and error analysis; determinism alone does not establish correctness.

## 13. Optional study: model comparison and cost efficiency

Status: **conditional, pre-registered here, run only if time allows after E14.** The primary evaluation uses
the single pinned configuration of §5. This section fixes how additional models would be compared so that,
if the study is run, it is confirmatory rather than tuning on the test set. If it is not run, the paper says
so in one sentence and nothing in RQ1–RQ4 changes.

### 13.1 Three rules

1. **Confirmatory or exploratory is decided before the held-out data is touched.** The model set, settings
   and cost metric below are frozen at `eval-v1`. Any model added after a held-out result has been seen is
   exploratory and is labelled so in every table. The held-out set is never used to pick a model and then
   quoted as that model's result. A second split, `heldout-B`, sealed with the same procedure as §5 and
   `evaluation-architecture.md` D5, is reserved for any later model selection; the primary split is used
   once per pre-registered model.
2. **The ceiling is expected; cost is the discriminating axis.** On this subset every frontier model is
   expected to reach success@5 ≈ 1.0 on template-family loops. The study therefore reports a Pareto front of
   success against cost per model and effort level, with intervals, rather than a single winner. Cost is
   defined in §13.3 with tokens as the primary unit.
3. **Protocol parity.** One frozen prompt for every model; any per-model deviation is recorded. Temperature
   and reasoning effort are recorded per model and omitted where the API rejects them. Model identifiers are
   pinned; absence of a dated snapshot is stated per model. R = 5 runs per model and effort level, every
   response cached and archived so the chapter replays offline. No prompt edit between models.

### 13.2 Conditions

The reference condition is L1 of §5 (pinned current model, frozen prompt, feedback on). Each additional
model is run as L1 with its own settings. Reasoning effort, where a model exposes it, is a separate
condition (low, medium, high), never averaged. Vendors other than the current one are reached either through
the OpenAI-compatible endpoint of roadmap E10 or through one adapter per service, as the project rules
require; the adapter used is recorded. The model list is appended to this section before `eval-v1`
with identifiers, snapshot status and settings; models not listed there are exploratory.

### 13.3 Metrics

Per model and effort level, over the held-out loops and R runs:

- success@5 with cluster-bootstrap 95% interval (as §5);
- attempts to first success;
- input, output and reasoning tokens per solved loop (primary cost unit);
- wall time per solved loop, request latency median and maximum;
- dollars per solved loop from a dated price sheet stored in the evidence directory; prices are secondary
  because they change, token counts are fixed for a recorded response but depend on tokenizer and
  accounting conventions; they are not universal units of compute or money;
- rejection classes as Table VIII, per model.

### 13.4 Analysis

Planned contrasts of each pre-registered model against the reference on success@5, Holm-corrected, with the
exact sign test as companion. Cost differences reported as log-ratios of tokens per solved loop with
bootstrap intervals. The headline figure is the success-versus-tokens front with one point per model and
effort level and interval bars on both axes. With about 12 loops the success intervals will be wide; the
paper states that precision of cost and success differences must be measured rather than promised in advance.

### 13.5 Outputs and provenance

Table IX (models, settings, snapshot status, success@5, attempts, tokens, time, dollars, date of price
sheet) and Figure 8 (Pareto front). Evidence directories `bench/eval/models-<date>/<model>/` sealed as
every other experiment; transcripts under the research-transcript flag; `make paper` regenerates both.

### 13.6 Deviations specific to this study

A model deprecated between pre-registration and execution is reported as not run, with its identifier.
A vendor whose API rejects the frozen prompt format receives a minimal format adaptation, saved as a diff
and disclosed; a semantic change to the prompt disqualifies the condition from the confirmatory table.

## 14. Pre-freeze reconciliation (20g / ADR-0032)

This revision expands the protected inventory, retains matvec's unresolved cause, separates compiler freeze
from prospective evaluation, and reconciles entry-point, oracle-interface and sealing requirements.
It corrects false-invariant denominators, unsupported power/equivalence claims, the function-level admission
unit and metamorphic byte-identity claims. Existing thresholds are not weakened to force success.
The draft is development-informed and cannot retrospectively preregister existing data. See
[20g](docs/steps/20g-freeze-reconciliation.md) and [remaining actions](docs/FREEZE-TODO.md).
