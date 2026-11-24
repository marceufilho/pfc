# Independent Frama-C/WP + RTE comparison

This validation compares the six pfc kernels and eight existing safety ports with independently written
ACSL. It is outside the pfc compiler package and cannot grant authorization to remove a guard.

## Reproduce

Install Docker and the locked project environment first. Pull the pinned official image:

```bash
docker pull framac/frama-c@sha256:4f0b7a2bd600c4daabfdcdfa393a3c18301e0db18f3b2739f1d9650193462d36
uv run --locked python tools/frama_oracle.py --output out/oracle --dry-run
uv run --locked python tools/frama_oracle.py --output out/oracle
```

Choose a new destination; existing paths are refused. Actual execution uses `--pull=never`,
`--network=none`, two CPU cores and 2 GiB per container. The sources mount read-only; only the new
per-case output directory is writable. Neither the home directory nor API credentials are mounted.
Downloading the image needs network; proof execution does not.

Toolchain: Frama-C 32.1 (Germanium), Qed/Alt-Ergo 2.5.4, Why3 1.8.2. pfc uses its locked Z3 and clang-19.
The machine model is x86_64 with int32/long64. Each prover query gets 10 seconds; smoke queries get
2 seconds; at most two proof processes run concurrently. The outer per-case process cap is 300 seconds.
No retry or per-case escalation is hidden. Future timings/verdicts may vary with resources; preserve the run.

## Files and interpretation

- `PROTOCOL.md`: population, semantic correspondence and interpretation recorded before the oracle run.
- `sources/`: 14 handwritten ACSL transcriptions. The executable C is checked against the original by
  `tests/test_frama_oracle.py`, ignoring only empty statements left by contract macro erasure.
- `controls/`: six negative controls; they are not part of the positive 14-function denominator.
- `manifest.json`: source hashes, original paths, comparison labels, image digest and fixed final options.
- `site-map.json`: manually reviewed label → original `[line,column]` correspondence.

Every case saves `wp.json`, complete formulas in `wp.stdout`, diagnostics and command/exit/timing records.
Positive cases also save pfc's original JSON and `comparison.json`. `summary.json` and `tables.md` are
produced from those records. The final checksum manifest includes every saved file. An exit 0 from Frama-C
alone is not success; all named properties and non-overflow support must be valid for conditional agreement.
An ordinary WP failed/unknown/timeout goal is **unproved**, not a refutation. A smoke goal proved false
signals inconsistent assumptions or an unreachable path; inconclusive smoke does not establish SAT.

`terminates \false` explicitly requests partial correctness rather than adding a termination requirement.
Explicit valid/valid_read regions represent the same external allocation obligation already assumed by pfc.
Assigns clauses describe and check frame conditions. No array-content or accumulator bound was added.
Typed memory and pointer validity remain different abstractions: this is corroboration on matched
properties, not a proof that the two complete models are equivalent.

Data-only overflow goals remain unproved and are reported separately. Named assertions may help later
RTE goals in WP; their validity and all non-overflow supporting obligations are therefore required together.
Do not count a named bounds proof and its RTE consequence as two independent confirmations of one site.

Manual tests (Docker is unnecessary for the ordinary test file):

```bash
uv run --locked pytest -q tests/test_frama_oracle.py
uv run --locked mypy tools/frama_oracle.py
```

Expected: 31 tests pass. The adapter handles malformed/missing evidence, smoke polarity, failed support,
nonzero exits, dry-run and output preservation. The 14 body-identity cases do not independently verify ACSL.
