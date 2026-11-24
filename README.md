# pfc

`pfc` is a proof-guided compiler for a restricted subset of C. It reads contracts (`REQUIRES`, `ENSURES`,
`INVARIANT`, `ASSERT`, `SEPARATED`), generates verification conditions, asks Z3, and emits C in three modes:
`plain` (no checks), `checked` (a trap before every array access, division and signed arithmetic operation) and
`verified` (`checked` without the checks that were proved, each removal citing its obligation).
`pfc_eval` is the evaluation harness: an independent reference interpreter and the experiments built on it.

A check is removed only when Z3 answers `unsat` for the obligation of that access. No proof object is produced;
loop invariants may come from an untrusted proposer and are admitted only by the checker.

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.11 or 3.12
- `clang-19` and `cpp` (`PFC_CLANG`, `PFC_CPP` select others)
- optional: `cvc5` (cross-check), Docker (Frama-C/WP sweep), `OPENAI_API_KEY` in the environment (LLM proposer)

## Install and try

```bash
make sync
uv run --locked pfc check kernels/sum/kernel.c                    # obligations and solver verdicts
uv run --locked pfc emit kernels/sum/kernel.c --mode verified     # C with proved checks removed and cited
uv run --locked pfc build kernels --mode verified --output build/local   # the output directory must be new
uv run --locked pfc test kernels                                  # 1000 inputs per kernel, three builds compared
make slice SLICE_ARGS=--dry-run                                   # remove a precondition: the check comes back
```

`uv run --locked pfc --help` lists every command; each has `--help` with an example and `--json`.

## Layout

| Path | Content |
| --- | --- |
| `pfc/` | the compiler (frontend, IR, verification conditions, Z3 adapter, authorization, emitter) |
| `include/spec.h` | the contract macros |
| `pfc_eval/` | the evaluation harness |
| `kernels/`, `examples/` | six kernels with drivers and input profiles; six small examples |
| `bench/external/` | ported third-party programs (see its README) |
| `heldout/` | 43 programs ported from two SV-COMP array families, sealed (`HELDOUT.sha256`) |
| `validation/frama-c*` | hand-written ACSL and container recipes for an independent Frama-C/WP comparison |
| `tests/` | unit, golden, negative and integration tests |

## Evaluation harness

```bash
uv run --locked python -m pfc_eval SUBCOMMAND --help
```

Every experiment writes a new directory (`--output`), refuses an existing one, and supports `--dry-run`.

| Subcommand | What it does |
| --- | --- |
| `differential` | plain, checked and verified binaries against the reference interpreter on generated inputs |
| `conjuncts` | removes each precondition conjunct alone; a check that depended on it must return |
| `inject` | false loop invariants from a fixed operator family; none may be admitted |
| `negative` | inputs that violate the contract or overflow; the expected trap must fire |
| `robustness` | semantics-preserving rewrites; verdicts and decisions must not change |
| `houdini` | template (`--family b1`) and dynamic (`--family b2`) invariant baselines |
| `proposer` | LLM proposer conditions (`--live` sends the program text; `--replay DIR` sends nothing) |
| `sweep` | solver time budgets for `pfc` and, with Docker, Frama-C/WP |
| `crosscheck` | every query answered again by cvc5 (`--cvc5 PATH`) |
| `mutate` | mutants of the compiler and of the harness against the test layers |
| `runtime` | timing of the three modes and fact ablations |
| `audit` | offline recomputation of a `pfc build` directory |
| `heldout` | `seal`, `verify`, `skeleton` for a held-out set |
| `tables` | summary tables from finished experiment directories |
| `doctor` | read-only check of the machine and the tree |

## Reproduce

Each command writes a new directory and prints a summary; none needs a model or the network.

| What | Command | Expected in the summary |
| --- | --- | --- |
| No invariant versus the written one, held-out set | `uv run --locked python tools/no_proposer.py heldout --output out/no-proposer` | `trivial_removed: 0`, `written_removed: 209` of `guards_total: 209` |
| What clang removes alone, held-out set | `uv run --locked python tools/clang_baseline.py heldout --output out/clang` | `removed_by_clang_o2: 70`, `survive_o2: 139` |
| Trivial invariant and counter rule, Zilu loops | `uv run --locked python tools/no_proposer.py bench/external/zilu --rule counter --output out/zilu` | `trivial_programs_accepted: 1`, `counter_programs_accepted: 0` of 30 |
| Differential test, held-out set | `uv run --locked python -m pfc_eval differential heldout --seed 0 --profile pfc_eval/profiles-heldout --output out/differential` | 43 programs, 1000 cases each, 0 failures |
| Hypothesis withdrawal, then the witnesses executed | `uv run --locked python -m pfc_eval conjuncts kernels bench/external/acsl --output out/conjuncts` then `uv run --locked python tools/witness_replay.py kernels bench/external/acsl --campaign out/conjuncts --output out/witnesses` | every witness stopped by the witnessed guard in both builds |
| False invariants | `uv run --locked python -m pfc_eval inject heldout --seed 0 --output out/inject` | no false candidate admitted |
| Template and dynamic baselines | `uv run --locked python -m pfc_eval houdini heldout --output out/b1` and `... houdini heldout --family b2 --seed 0 --profile pfc_eval/profiles-heldout --output out/b2` | programs admitted per baseline |

Add `--dry-run` to any `pfc_eval` command to preview it. The LLM condition (`proposer ... --live`) sends program
text to the model service and needs `OPENAI_API_KEY`.

## Records

`records/` holds the registered design, the amendment log and, for every run behind a reported result, its
configuration, summary and manifest. The raw runs are not published (see `records/README.md`).

## Tests

```bash
make check          # pytest, ruff, black --check, mypy
make freeze-check   # the compiler files match validation/freeze/protected.sha256
```

The integration tests need `clang-19`; one test needs the directory to be a git repository with a commit.

## Notes

- The compiler files listed in `validation/freeze/protected.sha256` are the frozen candidate `freeze-v5`; they are
  unchanged here. Comments that cite `ADR-…`, step or amendment numbers refer to development records that are not
  part of this repository.
- `verified` C is not a claim that arbitrary C is safe: the guarantee is relative to the contracts, the memory
  model and the correctness of the compiler and of Z3.
- Third-party material keeps its licence: `bench/external/zilu*` GPL-2.0, `bench/external/acsl` MIT,
  `heldout/` Apache-2.0 (SV-COMP). No licence has been chosen yet for the rest of the repository.
