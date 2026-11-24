# Independent WP/RTE comparison protocol

Recorded before the first oracle run, 2026-11-05. This is a finite validation experiment, not a proof of pfc.

Population: the six kernels in `kernels/` and the eight safety ports in `bench/external/acsl/`.
The ACSL in `sources/` is handwritten from the source and semantic specification, not generated from pfc
IR or VCs. An independent C-AST comparison will check executable bodies after removing annotations;
that check does not validate the ACSL. No upstream ACSL-by-Example proof is transferred to a changed port.

Target: x86-64, C int32/long64. Explicit memory validity represents pfc's external caller allocation
obligation; it is not an extra assumption about n fitting a_len. Full-region separation follows the
existing SEPARATED contracts. No content bound, sortedness or accumulator-overflow precondition is added.
Function/loop assigns describe actual writes and are checked by WP. The declared loop invariants match
the source. Explicit named bounds/division assertions identify semantic properties corresponding to pfc
obligations; they are not counted as additional independent evidence alongside duplicate RTE goals.

Tool: official Frama-C 32.1 Docker image, digest
`sha256:4f0b7a2bd600c4daabfdcdfa393a3c18301e0db18f3b2739f1d9650193462d36`.
Read installed help/manual and record all backend versions before fixing the executable run configuration.
Pilot runs resolve syntax, tool interfaces and frame specification; preserve their logs separately. They
are not the final evidence. The final manifest records fixed options, timeouts, source hashes and tool
versions. No per-kernel timeout escalation or hidden retry is permitted in the recorded final run.
Execution is local inside the container with networking disabled; only curated fixtures/output are mounted.

For each positive case, preserve all WP per-goal verdicts, SMT attempts, RTE alarms, smoke tests, stdout,
stderr and exit status. Separate explicit bounds/division properties, invariant/frame support, memory RTE
and overflow RTE. Unproved data-only arithmetic does not falsify the conditional bounds comparison.
Do not report whole-program absence of undefined behavior when any relevant RTE property is unproved.
Record potential supporting assumptions in the comparison and compare property meanings rather than
requiring equal VC counts. A proof under mismatched assumptions is not agreement.

Negative controls: omitted n<=a_len, false invariant, non-preserving increment, divisor zero possibility,
signed overflow possibility, and contradictory entry requirements. They must not be summarized as
successful safety proofs. Smoke proving false detects inconsistency; failing to prove false does not
establish SAT and is not equivalent to pfc's entry-consistency gate.

If a non-overflow supporting goal or compared safety goal is unproved, classify that case inconclusive
pending reconciliation. Solver timeout/unknown/failed is not a concrete C counterexample. Report
parse/tool errors separately and fail on missing goals or malformed/incomplete reports. Do not count an
empty report, solver exit 0 or JSON passed=true alone as validation.

The main comparison is the scope of this step. Language extensions and discriminating G2' remain future
work; optional corpus screening must not be conflated with proof validation. Git commits/tags belong to
the user under AGENTS.md. A clean-container reproduction of an identified source archive can be prepared
before that action, but must not be called a tagged fresh-machine reproduction.
