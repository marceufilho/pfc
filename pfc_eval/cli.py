"""Argument parsing for the standalone evaluation entry point (roadmap E1, architecture D8).

The approved subcommand list is fixed here. Every writing command requires a new output
directory and offers a non-writing preview. Every subcommand has its handler (roadmap E3–E13).
`pfc/cli.py` is frozen and untouched.
"""

import argparse
from collections.abc import Callable, Sequence

PROG = "python -m pfc_eval"
SUBCOMMANDS: dict[str, str] = {
    "differential": "E3",
    "conjuncts": "E4",
    "inject": "E5",
    "houdini": "E6",
    "proposer": "E10",
    "sweep": "E8",
    "robustness": "E7",
    "mutate": "E9",
    "runtime": "E11",
    "heldout": "E12",
    "tables": "E13",
    "doctor": "E14b",
    "crosscheck": "E19",
    "negative": "E23",
    "audit": "E23",
}
Handler = Callable[[argparse.Namespace], int]


def _computational(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--clang", default=None, help="clang executable (default: PFC_CLANG or clang-19)")
    parser.add_argument("--cpp", default=None, help="cpp executable (default: PFC_CPP or cpp)")
    parser.add_argument("--timeout-ms", type=int, default=None, help="Z3 timeout per obligation")


def _writing(parser: argparse.ArgumentParser, *, seed: bool = False) -> None:
    parser.add_argument("--output", required=True, help="new evidence directory; existing paths are refused")
    parser.add_argument("--dry-run", action="store_true", help="preview the schedule; write nothing")
    if seed:
        parser.add_argument("--seed", type=int, default=0, help="seed of every stochastic component")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Evaluation harness for the frozen pfc compiler; grants no proof authority.",
        epilog="Example: python -m pfc_eval differential kernels --output out/differential",
    )
    sub = parser.add_subparsers(dest="command", metavar="SUBCOMMAND", required=True)

    def add(name: str, purpose: str, example: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=purpose, description=purpose, epilog=f"Example: {PROG} {example}")

    p = add(
        "differential",
        "REQUIRES-driven inputs, interpreter oracle, three-build comparison.",
        "differential kernels --output out/differential --seed 0",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument("--profile", default=None, help="versioned JSON generation profile")
    _writing(p, seed=True)
    _computational(p)

    p = add(
        "conjuncts",
        "Per-conjunct REQUIRES ablation with verdict and emitted-C comparison.",
        "conjuncts kernels --output out/conjuncts",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument(
        "--reconcile",
        default=None,
        metavar="SEALED_DIR",
        help="recompute restored guard sites of a sealed conjuncts campaign into the new output (no rerun)",
    )
    _writing(p)
    _computational(p)

    p = add(
        "inject",
        "Systematic false-invariant injection and counterexample reproduction classes.",
        "inject kernels --output out/inject --seed 0",
    )
    p.add_argument("directories", nargs="+")
    _writing(p, seed=True)
    _computational(p)

    p = add(
        "houdini",
        "B1 template or B2 dynamic-inference baseline through the frozen admission path.",
        "houdini kernels --output out/houdini",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="refinement rounds (default: the pre-registered 5; another value is a disclosed amendment)",
    )
    p.add_argument(
        "--family",
        choices=("b1", "b2"),
        default="b1",
        help="candidate family: b1 templates (default) or b2 dynamic inference over interpreter states",
    )
    p.add_argument("--profile", default=None, help="generation profiles directory for b2 state sampling")
    _writing(p, seed=True)
    _computational(p)

    p = add(
        "proposer",
        "LLM conditions L1/L2/L3 over function tasks; transcripts cached for replay.",
        "proposer heldout --output out/proposer-L1 --condition L1 --runs 5 --live",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument("--condition", choices=("L1", "L2", "L3"), required=True)
    p.add_argument("--runs", type=int, default=5, help="independent runs per function task")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="send requests; key only from the environment")
    mode.add_argument("--replay", metavar="DIR", help="recheck archived transcripts with zero requests")
    p.add_argument("--base-url", default=None, help="L3 only: OpenAI-compatible endpoint, recorded in evidence")
    p.add_argument(
        "--model", default="gpt-6-astra", help="model identifier recorded in evidence (default: gpt-6-astra)"
    )
    p.add_argument(
        "--reasoning-effort",
        default="low",
        choices=("omit", "none", "low", "medium", "high"),
        help="reasoning effort where the model exposes it; 'omit' sends none (default: low)",
    )
    p.add_argument("--temperature", type=float, default=None, help="sampling temperature; omitted when absent")
    _writing(p, seed=True)
    _computational(p)

    p = add(
        "sweep",
        "Solver-budget curve for pfc/Z3 and WP/Alt-Ergo.",
        "sweep kernels --output out/sweep --budgets 1,2,5,10,30,60",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument("--budgets", default="1,2,5,10,30,60", help="seconds per obligation, comma separated")
    p.add_argument("--tools", default="pfc,wp", help="comma-separated subset of pfc,wp (WP needs the pinned image)")
    p.add_argument(
        "--prover",
        choices=("alt-ergo", "z3"),
        default=None,
        help="WP prover override for an adjudication run (default: the sealed oracle's alt-ergo)",
    )
    p.add_argument(
        "--image",
        default=None,
        metavar="SHA256",
        help="pinned derived Frama-C image (sha256:<id> or repo@sha256:<digest>) carrying the chosen prover",
    )
    _writing(p)
    _computational(p)

    p = add(
        "robustness",
        "Semantics-preserving variants and verdict/C equality.",
        "robustness kernels --output out/robustness --seed 0 --variants 10",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument("--variants", type=int, default=10)
    _writing(p, seed=True)
    _computational(p)

    p = add(
        "mutate",
        "Source mutants of the trusted files in isolated copies; layered kill matrix.",
        "mutate --output out/mutate --seed 0",
    )
    _writing(p, seed=True)
    p.add_argument(
        "--targets",
        choices=("all", "compiler", "harness", "extended"),
        default="all",
        help="mutant group to run (default: all = compiler + harness; extended = smt, invariants, lower)",
    )
    p.add_argument("--per-file", type=int, default=None, help="seeded compiler mutants per file (default: 10)")
    p.add_argument("--harness-per-file", type=int, default=None, help="seeded harness mutants per file (default: 5)")
    p.add_argument(
        "--all",
        action="store_true",
        help="whole distinct population under the extended operator family (disclosed extension; ignores per-file)",
    )

    p = add(
        "crosscheck",
        "Advisory second-solver (cvc5) check of every obligation exported from the Z3 encoding.",
        "crosscheck kernels --cvc5 /opt/cvc5/bin/cvc5 --output out/crosscheck",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument("--cvc5", default=None, help="cvc5 binary installed by the user (never downloaded)")
    p.add_argument("--logic", default=None, help="SMT-LIB logic declared in the export (default: QF_AUFNIA)")
    _writing(p)
    _computational(p)

    p = add(
        "runtime",
        "Interleaved runtime blocks, instruction counters, vectorization remarks.",
        "runtime kernels --output out/runtime",
    )
    p.add_argument("directories", nargs="+")
    _writing(p, seed=True)
    _computational(p)
    p.add_argument("--elements", type=int, default=None, help="array elements per run (default: 2^24)")
    p.add_argument("--repetitions", type=int, default=None, help="kernel repetitions inside main (default: 50)")
    p.add_argument("--blocks", type=int, default=None, help="interleaved blocks (default: 3)")
    p.add_argument("--runs", type=int, default=None, help="timed runs per block (default: 10)")
    p.add_argument("--warmups", type=int, default=None, help="warm-up runs per variant (default: 3)")
    p.add_argument("--cpu", type=int, default=None, help="pin every timed process to this CPU with taskset")
    p.add_argument(
        "--trap-ablation",
        action="store_true",
        help="also time verified C with its overflow traps rewritten as plain operations (exploratory ablation)",
    )

    p = add(
        "heldout",
        "Seal the held-out corpus (canaries, then hashes), verify it before any run, or print profile skeletons.",
        "heldout verify heldout",
    )
    p.add_argument("action", choices=("seal", "verify", "skeleton"))
    p.add_argument("directory")
    p.add_argument(
        "--untouched",
        action="store_true",
        help="seal only: explicit statement that no proposer, Houdini, oracle or differential run read the set",
    )
    p.add_argument("--cpp", default=None, help="cpp executable for the acceptance check (default: cpp)")
    p.add_argument("--clang", default=None, help="clang whose integer layout the guard-site count uses")
    p.add_argument(
        "--profiles",
        default=None,
        help="profiles directory (seal: default pfc_eval/profiles-heldout; skeleton: write here, never overwrite)",
    )
    p.add_argument(
        "--composition",
        choices=("required", "reported"),
        default="required",
        help="seal only: enforce the B1 composition rule (default) or only record its fraction (external set)",
    )
    p.add_argument(
        "--prior-check",
        action="append",
        default=None,
        help="seal only: a deterministic selection check that preceded sealing (repeatable; recorded in SEALING.json)",
    )

    p = add(
        "negative",
        "Expected-trap campaign: precondition violations against verified, overflowing inputs against checked.",
        "negative kernels --output out/negative --seed 0",
    )
    p.add_argument("directories", nargs="+")
    p.add_argument("--profile", default=None, help="profiles directory (default: pfc_eval/profiles)")
    p.add_argument("--cases", type=int, default=None, help="rejected inputs per class and kernel (default: 20)")
    _writing(p, seed=True)
    _computational(p)

    p = add(
        "audit",
        "Audit record of pfc build directories: offline solver replay, re-emission and an independent site map.",
        "audit build/all-verified --output out/audit",
    )
    p.add_argument("directories", nargs="+", metavar="BUILD_DIR")
    _writing(p)
    _computational(p)

    p = add("tables", "Render summary tables and figure data from sealed evidence directories.", "tables bench/eval/*")
    p.add_argument("directories", nargs="+")
    p.add_argument("--output", default=None, help="write the files here instead of printing the Markdown tables")

    p = add(
        "doctor",
        "Preflight of this machine (governor, counters, image, key presence, tags, "
        "inventories, sealed held-out set); writes nothing.",
        "doctor --heldout heldout",
    )
    p.add_argument("--heldout", default="heldout", help="sealed held-out directory to verify (default: heldout)")
    p.add_argument("--json", action="store_true", help="structured report on stdout")
    return parser


def _differential(arguments: argparse.Namespace) -> int:
    from pfc_eval import differential

    return differential.command(arguments)


def _conjuncts(arguments: argparse.Namespace) -> int:
    from pfc_eval import conjuncts

    return conjuncts.command(arguments)


def _inject(arguments: argparse.Namespace) -> int:
    from pfc_eval import inject

    return inject.command(arguments)


def _houdini(arguments: argparse.Namespace) -> int:
    from pfc_eval import houdini

    return houdini.command(arguments)


def _robustness(arguments: argparse.Namespace) -> int:
    from pfc_eval import robustness

    return robustness.command(arguments)


def _sweep(arguments: argparse.Namespace) -> int:
    from pfc_eval import sweep

    return sweep.command(arguments)


def _mutate(arguments: argparse.Namespace) -> int:
    from pfc_eval import mutate

    return mutate.command(arguments)


def _proposer(arguments: argparse.Namespace) -> int:
    from pfc_eval import proposer

    return proposer.command(arguments)


def _crosscheck(arguments: argparse.Namespace) -> int:
    from pfc_eval import crosscheck

    return crosscheck.command(arguments)


def _runtime(arguments: argparse.Namespace) -> int:
    from pfc_eval import runtime

    return runtime.command(arguments)


def _heldout(arguments: argparse.Namespace) -> int:
    from pfc_eval import heldout

    return heldout.command(arguments)


def _negative(arguments: argparse.Namespace) -> int:
    from pfc_eval import negative

    return negative.command(arguments)


def _audit(arguments: argparse.Namespace) -> int:
    from pfc_eval import audit

    return audit.command(arguments)


def _tables(arguments: argparse.Namespace) -> int:
    from pfc_eval import tables

    return tables.command(arguments)


def _doctor(arguments: argparse.Namespace) -> int:
    from pfc_eval import doctor

    return doctor.command(arguments)


HANDLERS: dict[str, Handler] = {
    "differential": _differential,
    "conjuncts": _conjuncts,
    "inject": _inject,
    "houdini": _houdini,
    "robustness": _robustness,
    "sweep": _sweep,
    "mutate": _mutate,
    "proposer": _proposer,
    "runtime": _runtime,
    "crosscheck": _crosscheck,
    "negative": _negative,
    "audit": _audit,
    "heldout": _heldout,
    "tables": _tables,
    "doctor": _doctor,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run in process; 0 success, 1 operational failure or unimplemented step, 2 usage error."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exit_status:
        return int(exit_status.code or 0)
    return HANDLERS[arguments.command](arguments)
