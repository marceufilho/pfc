"""Command-line orchestration; compiler stages are added in plan order."""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import cast

from pfc import bench, workflow
from pfc import report as reports
from pfc import slice as gate
from pfc.build import emission_passed
from pfc.config import (
    CLANG_ENV_VAR,
    CPP_ENV_VAR,
    DEFAULT_CLANG,
    DEFAULT_CPP,
    DIFF_SEED,
    MAX_DIFF_SEED,
    MAX_SOLVER_TIMEOUT_MS,
    OPENAI_KEY_ENV,
    PROPOSER_ATTEMPTS,
    PROPOSER_RUNS,
    SOLVER_TIMEOUT_MS,
)
from pfc.corpus import PROFILES, profile
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.difftest import render_report as render_differential
from pfc.difftest import run_sum
from pfc.emit import emit
from pfc.environment import ToolchainError, collect_environment, render_markdown
from pfc.facts import ALL_FACTS, DEFAULT_FACTS, FactName
from pfc.ir import print_c, to_data
from pfc.lower import lower
from pfc.parse import parse_source, render_report
from pfc.propose import experiment
from pfc.propose.llm import Settings
from pfc.smt import check
from pfc.smt import render_report as render_checks
from pfc.target import read_target
from pfc.vc import generate
from pfc.vc import render_report as render_vcs

MODES = ("plain", "checked", "verified")
TIMEOUT_HELP = f"Z3 timeout per query, 1..{MAX_SOLVER_TIMEOUT_MS} (default: {SOLVER_TIMEOUT_MS})"
SUBPROCESS_NOTE = "; each compiler/binary process has a fixed 10-second limit"


def _timeout(value: str) -> int:
    try:
        timeout = int(value)
        if 1 <= timeout <= MAX_SOLVER_TIMEOUT_MS:
            return timeout
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(f"timeout must be 1..{MAX_SOLVER_TIMEOUT_MS} milliseconds")


def _seed(value: str) -> int:
    try:
        seed = int(value)
        if 0 <= seed <= MAX_DIFF_SEED:
            return seed
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(f"seed must be 0..{MAX_DIFF_SEED}")


def _facts(value: str) -> frozenset[FactName]:
    values = value.split(",") if value else []
    if len(set(values)) != len(values) or not set(values) <= ALL_FACTS:
        raise argparse.ArgumentTypeError("facts must be unique delete,restrict,assume names; empty disables all")
    return cast(frozenset[FactName], frozenset(values))


def _dump(data: object, text: str, *, as_json: bool) -> None:
    sys.stdout.write(json.dumps(data, indent=2) + "\n" if as_json else text)


def _common(
    parser: argparse.ArgumentParser,
    *,
    cpp: bool = False,
    clang: str | None = None,
    timeout: str | None = None,
    seed: str | None = None,
    json_help: str = "emit structured results and diagnostics",
) -> None:
    """Shared toolchain/output options; environment variables supply the defaults."""
    if cpp:
        parser.add_argument("--cpp", default=os.environ.get(CPP_ENV_VAR, DEFAULT_CPP), help="cpp executable")
    if clang:
        parser.add_argument("--clang", default=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), help=clang)
    if timeout:
        parser.add_argument("--timeout-ms", type=_timeout, default=SOLVER_TIMEOUT_MS, help=timeout)
    if seed:
        parser.add_argument("--seed", type=_seed, default=DIFF_SEED, help=seed)
    parser.add_argument("--json", action="store_true", help=json_help)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pfc",
        description="Proof-guided C compiler (structured verification and checked/verified C emission).",
        epilog="Example: pfc parse kernels/sum/kernel.c",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"pfc {version('pfc')}")
    parser.add_argument("--debug", action="store_true", help="show a traceback for operational errors")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_: str, description: str, example: str) -> argparse.ArgumentParser:
        return commands.add_parser(
            name, help=help_, description=description, epilog=f"Example: pfc {example}", allow_abbrev=False
        )

    def source(parser: argparse.ArgumentParser, help_: str = "UTF-8 C source file") -> None:
        parser.add_argument("source", type=Path, help=help_)

    environment = add(
        "env",
        "inspect the compiler toolchain",
        "Report Python, Z3, clang, CPU and configured C flags.",
        "env --output bench/ENV.md --dry-run",
    )
    _common(environment, clang="clang executable", json_help="emit structured JSON instead of Markdown")
    environment.add_argument("--output", type=Path, help="write a new report file instead of stdout")
    environment.add_argument("--force", action="store_true", help="allow replacing the output file")
    environment.add_argument("--dry-run", action="store_true", help="print the report without writing files")

    parse = add(
        "parse",
        "inspect contracts and operation sites in C",
        "Preprocess C and report supported syntax, contracts and sites; no verification.",
        "parse kernels/sum/kernel.c --json",
    )
    source(parse)
    _common(parse, cpp=True)

    ir = add(
        "ir",
        "inspect typed IR or reconstruct annotated C",
        "Lower C to structured typed IR and print unverified annotated C to stdout.",
        "ir kernels/sum/kernel.c --json",
    )
    source(ir)
    _common(ir, cpp=True, json_help="emit typed IR and structured diagnostics instead of C")

    vc = add(
        "vc",
        "list verification conditions for structured C",
        "Generate obligations without running a solver or authorizing check removal.",
        "vc kernels/sum/kernel.c --json",
    )
    source(vc)
    _common(vc, cpp=True, json_help="emit structured obligations and diagnostics")

    checks = add(
        "check",
        "check structured obligations with Z3",
        "Check required formulas and invariant dependencies; overflow reported separately, no C emission.",
        "check kernels/sum/kernel.c --json --timeout-ms 10000",
    )
    source(checks)
    _common(
        checks,
        cpp=True,
        clang="clang for target limits",
        timeout=TIMEOUT_HELP,
        json_help="emit structured solver results and diagnostics",
    )

    emission = add(
        "emit",
        "emit plain, checked or proof-guided structured C",
        "Print guarded C; verified mode removes guards only with exact supporting proofs.",
        "emit kernels/sum/kernel.c --mode verified --json",
    )
    source(emission)
    emission.add_argument("--mode", required=True, choices=MODES, help="guard retention policy")
    emission.add_argument(
        "--facts",
        type=_facts,
        default=DEFAULT_FACTS,
        help="comma-separated verified facts: delete,restrict,assume; default delete; empty disables all",
    )
    _common(
        emission,
        cpp=True,
        clang="clang for target limits",
        timeout="Z3 timeout per query in verified mode (default: 10000 ms)",
        json_help="emit C, check decisions and available proof results as JSON",
    )

    differential = add(
        "difftest",
        "compare checked and verified sum binaries",
        "Run 1000 valid inputs for the reviewed sum profile; compare returns and every array element.",
        "difftest kernels/sum/kernel.c --seed 17 --json",
    )
    source(differential, "UTF-8 C source matching the reviewed sum profile")
    _common(
        differential,
        cpp=True,
        clang="clang for target limits and both binaries",
        timeout="Z3 timeout per query (default: 10000 ms)" + SUBPROCESS_NOTE,
        seed=f"input seed, 0..{MAX_DIFF_SEED} (default: {DIFF_SEED})",
        json_help="emit structured comparison evidence and diagnostics",
    )

    slice_command = add(
        "slice",
        "run the sum G0 gate and save reproducible evidence",
        "Check G0: precondition ablation, 1000 differential cases, deletion control and guarded traps.",
        "slice kernels/sum/kernel.c --output demo/g0",
    )
    source(slice_command, "UTF-8 C source matching the reviewed sum profile")
    slice_command.add_argument(
        "--output", type=Path, help="create a new evidence directory; existing paths are refused"
    )
    slice_command.add_argument(
        "--dry-run", action="store_true", help="run checks and print summary without saving evidence"
    )
    _common(
        slice_command,
        cpp=True,
        clang="clang executable",
        timeout="Z3 timeout per query (default: 10000 ms); fixed 10-second limit per subprocess",
        seed="differential seed (default: 0)",
        json_help="emit structured summary and diagnostics",
    )

    for command in ("verify", "build", "test"):
        stage = add(
            command,
            f"{command} kernel directories",
            f"Run {command} over explicit kernel directories or their parent; preserve failures.",
            f"{command} kernels/sum" + (" --mode verified --output build/sum" if command == "build" else " --json"),
        )
        stage.add_argument("directories", nargs="+", type=Path, help="kernel directories or immediate parent")
        _common(
            stage,
            cpp=True,
            clang="clang executable",
            timeout="Z3 timeout per query",
            seed="differential seed; exactly 1000 cases" if command == "test" else None,
            json_help="print structured summary",
        )
        if command == "build":
            stage.add_argument("--output", type=Path, help="new artifact directory (required except with --dry-run)")
            stage.add_argument("--dry-run", action="store_true", help="validate proofs and toolchain; compile nothing")
            stage.add_argument("--mode", choices=MODES, required=True, help="emission mode")
            stage.add_argument(
                "--facts", type=_facts, default=DEFAULT_FACTS, help="comma-separated facts; default delete"
            )
        if command == "test":
            stage.add_argument("--output", type=Path, help="new directory for per-kernel differential/break-it JSON")
            stage.add_argument("--dry-run", action="store_true", help="run in temporary directories; save nothing")

    stage = add(
        "report",
        "render saved experiment evidence",
        "Validate checksums and render saved results; reports confer no proof authority.",
        "report build/sum --json",
    )
    stage.add_argument("directory", type=Path, help="saved artifact directory containing report and manifest")
    _common(stage, json_help="print structured summary")

    stage = add(
        "bench",
        "measure six variants with hyperfine",
        "Run correctness checks then 3 warmups and 10 measured executions per variant.",
        "bench kernels --output bench/results/local",
    )
    stage.add_argument("directories", nargs="+", type=Path, help="reviewed kernel directories")
    stage.add_argument("--output", required=True, type=Path, help="new result directory")
    stage.add_argument("--hyperfine", default="hyperfine", help="hyperfine executable")
    stage.add_argument("--dry-run", action="store_true", help="preview protocol; no writes, compilation or timing")
    _common(stage, cpp=True, clang="clang executable", timeout="solver timeout", json_help="structured results")

    stage = add(
        "propose",
        "run bounded OpenAI invariant experiments or replay",
        "Three runs of at most five attempts per curated kernel. Network requires --live.",
        "propose kernels --output bench/proposals/local --live",
    )
    stage.add_argument("directories", nargs="+", type=Path, help="curated kernel directories")
    stage.add_argument("--output", type=Path, help="new directory; enables curated research transcripts")
    choice = stage.add_mutually_exclusive_group()
    choice.add_argument("--live", action="store_true", help="send curated sources to OpenAI; uses OPENAI_API_KEY")
    choice.add_argument("--replay", type=Path, help="recheck saved responses without network or new sampling")
    stage.add_argument("--dry-run", action="store_true", help="preview model and budget; no requests or files")
    _common(stage, cpp=True, clang="clang executable", timeout="solver timeout", json_help="structured results")
    return parser


def _validate(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    if arguments.command == "env" and arguments.force and arguments.output is None:
        parser.error("--force requires --output")
    if arguments.command in {"build", "slice"} and arguments.output is None and not arguments.dry_run:
        parser.error(f"{arguments.command} requires --output DIR or --dry-run")
    if arguments.command == "propose" and not arguments.dry_run:
        if not arguments.output or not (arguments.live or arguments.replay):
            parser.error("propose requires --output DIR and either --live or --replay DIR; use --dry-run to preview")


def _environment(arguments: argparse.Namespace) -> int:
    environment = collect_environment(arguments.clang)
    report = json.dumps(asdict(environment), indent=2) + "\n" if arguments.json else render_markdown(environment)
    if arguments.output is None or arguments.dry_run:
        sys.stdout.write(report)
        return 0
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents a check-then-write race for the default path.
    with arguments.output.open("w" if arguments.force else "x", encoding="utf-8") as stream:
        stream.write(report)
    print("pfc: environment report written", file=sys.stderr)
    return 0


def _parse(arguments: argparse.Namespace) -> int:
    report = parse_source(arguments.source, cpp=arguments.cpp).report
    _dump(asdict(report), render_report(report), as_json=arguments.json)
    return 0


def _ir(arguments: argparse.Namespace) -> int:
    program = lower(parse_source(arguments.source, cpp=arguments.cpp))
    _dump(to_data(program), print_c(program), as_json=arguments.json)
    return 0


def _vc(arguments: argparse.Namespace) -> int:
    conditions = generate(lower(parse_source(arguments.source, cpp=arguments.cpp)))
    _dump(to_data(conditions), render_vcs(conditions), as_json=arguments.json)
    return 0


def _difftest(arguments: argparse.Namespace) -> int:
    program = lower(parse_source(arguments.source, cpp=arguments.cpp))
    differential = run_sum(program, compiler=arguments.clang, seed=arguments.seed, timeout_ms=arguments.timeout_ms)
    _dump(to_data(differential), render_differential(differential), as_json=arguments.json)
    return 0 if differential.status == "passed" else 1


def _check_or_emit(arguments: argparse.Namespace) -> int:
    program = lower(parse_source(arguments.source, cpp=arguments.cpp))
    try:
        target = read_target(arguments.clang)
    except ToolchainError as error:
        raise FrontendError("target-read", str(error), SourceLocation(str(arguments.source))) from error
    if arguments.command == "emit":
        proof = check(program, target, timeout_ms=arguments.timeout_ms) if arguments.mode == "verified" else None
        emitted = emit(program, target, mode=arguments.mode, proof=proof, facts=arguments.facts)
        _dump(to_data(emitted), emitted.c, as_json=arguments.json)
        return 0 if emission_passed(emitted) else 1
    checked = check(program, target, timeout_ms=arguments.timeout_ms)
    _dump(to_data(checked), render_checks(checked), as_json=arguments.json)
    return 0 if checked.required_passed else 1


def _slice(arguments: argparse.Namespace) -> int:
    output = arguments.output
    if output is not None and "\x00" in str(output):
        raise FrontendError("slice-output", "output path cannot contain NUL", SourceLocation("<output>"))
    if not arguments.dry_run and (output.exists() or output.is_symlink()):
        raise FrontendError(
            "slice-output", "choose a new directory; existing evidence is preserved", SourceLocation(str(output))
        )
    report = gate.run(
        lower(parse_source(arguments.source, cpp=arguments.cpp)),
        compiler=arguments.clang,
        seed=arguments.seed,
        timeout_ms=arguments.timeout_ms,
    )
    if not arguments.dry_run:
        try:
            gate.save(report, output)
        except OSError as error:
            raise FrontendError(
                "slice-output",
                "cannot save evidence; choose a new writable directory (partial output is preserved)",
                SourceLocation(str(output)),
            ) from error
        print("pfc: G0 evidence written", file=sys.stderr)
    _dump(gate.summary(report), gate.render_report(report), as_json=arguments.json)
    return 0 if report.passed else 1


def _run_stage(arguments: argparse.Namespace) -> reports.Summary:
    directories = tuple(arguments.directories)
    if arguments.command == "verify":
        return workflow.verify(
            directories, compiler=arguments.clang, cpp=arguments.cpp, timeout_ms=arguments.timeout_ms
        )
    if arguments.command == "build":
        return workflow.build(
            directories,
            arguments.output or Path("build-preview"),
            compiler=arguments.clang,
            cpp=arguments.cpp,
            mode=arguments.mode,
            facts=arguments.facts,
            timeout_ms=arguments.timeout_ms,
            dry_run=arguments.dry_run,
        )
    return workflow.test(
        directories,
        compiler=arguments.clang,
        cpp=arguments.cpp,
        seed=arguments.seed,
        timeout_ms=arguments.timeout_ms,
        output=arguments.output,
        dry_run=arguments.dry_run,
    )


def _report(arguments: argparse.Namespace) -> int:
    """Render sealed evidence; optional benchmark/experiment files must be in the manifest."""
    directory = arguments.directory
    summary = reports.load(directory)
    timings = bench.load(directory) if (directory / "benchmark.json").is_file() else ()
    proposals = experiment.load(directory) if (directory / "experiment.json").is_file() else None
    data: dict[str, object] = {"summary": to_data(summary)}
    text = reports.render(summary)
    if timings:
        data["timings"] = to_data(timings)
        text += "\n" + bench.render(timings)
    if proposals:
        data["proposals"] = to_data(proposals)
        text += "\n" + experiment.render(proposals)
    _dump(data if len(data) > 1 else data["summary"], text, as_json=arguments.json)
    return 0


def _workflow(arguments: argparse.Namespace) -> int:
    try:
        if arguments.command == "report":
            return _report(arguments)
        summary = _run_stage(arguments)
    except (ToolchainError, OSError, ValueError) as error:
        raise FrontendError("workflow-operation", str(error), SourceLocation("<workflow>")) from error
    _dump(to_data(summary), reports.render(summary), as_json=arguments.json)
    return 0 if summary.passed else 1


def _propose(arguments: argparse.Namespace) -> int:
    try:
        directories = tuple(arguments.directories)
        if arguments.dry_run:
            names = [profile(lower(parse_source(p, cpp=arguments.cpp))) for p in workflow.discover(directories)]
            settings = Settings()
            if arguments.replay:
                reports.load(arguments.replay)
                archived = experiment.load(arguments.replay)
                if set(names) != {row.kernel for row in archived.rows}:
                    raise ValueError("replay requires exactly the recorded kernel set")
                settings = archived.settings
            preview = {
                "dry_run": True,
                "kernels": names,
                "settings": to_data(settings),
                "runs": PROPOSER_RUNS,
                "attempts_per_kernel": PROPOSER_ATTEMPTS,
                "max_requests": 0 if arguments.replay else len(names) * PROPOSER_RUNS * PROPOSER_ATTEMPTS,
                "key_environment": OPENAI_KEY_ENV,
                "key_configured": bool(os.environ.get(OPENAI_KEY_ENV)),
                "g2": "not-run",
            }
            sys.stdout.write(json.dumps(preview, indent=2) + "\n")
            return 0
        result = experiment.run(
            directories,
            arguments.output,
            compiler=arguments.clang,
            cpp=arguments.cpp,
            live=arguments.live,
            replay=arguments.replay,
            timeout_ms=arguments.timeout_ms,
            progress=lambda message: print(message, file=sys.stderr),
        )
        _dump(to_data(result), experiment.render(result), as_json=arguments.json)
        complete = len(result.rows) == len(PROFILES) * PROPOSER_RUNS
        if result.mode == "live" and complete:
            return 0 if result.g2_passed else 1
        return 0 if all(row.found and row.wrong_rejected for row in result.rows) else 1
    except (OSError, ValueError, ToolchainError) as error:
        raise FrontendError("propose-operation", str(error), SourceLocation("<proposer>")) from error


def _bench(arguments: argparse.Namespace) -> int:
    try:
        directories = tuple(arguments.directories)
        if arguments.dry_run:
            names = [p.parent.name for p in workflow.discover(directories)]
            data = {"dry_run": True, "kernels": names, "variants": 6, "warmups": 3, "runs": 10}
            sys.stdout.write(json.dumps(data, indent=2) + "\n")
            return 0
        timings = bench.run(
            directories,
            arguments.output,
            compiler=arguments.clang,
            cpp=arguments.cpp,
            hyperfine=arguments.hyperfine,
            timeout_ms=arguments.timeout_ms,
            progress=lambda message: print(message, file=sys.stderr),
        )
        _dump(to_data(timings), bench.render(timings), as_json=arguments.json)
        return 0
    except (OSError, ValueError, ToolchainError, subprocess.TimeoutExpired) as error:
        raise FrontendError("bench-operation", str(error), SourceLocation("<benchmark>")) from error


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "env": _environment,
    "parse": _parse,
    "ir": _ir,
    "vc": _vc,
    "check": _check_or_emit,
    "emit": _check_or_emit,
    "difftest": _difftest,
    "slice": _slice,
    "verify": _workflow,
    "build": _workflow,
    "test": _workflow,
    "report": _workflow,
    "bench": _bench,
    "propose": _propose,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI in process; return 0 success, 1 operation failure, 2 usage error."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
        _validate(parser, arguments)
    except SystemExit as exit_status:
        return int(exit_status.code or 0)
    try:
        return HANDLERS[arguments.command](arguments)
    except FrontendError as error:
        if arguments.debug:
            raise
        diagnostic = json.dumps({"error": asdict(error.diagnostic)}) if arguments.json else f"pfc: {error}"
        print(diagnostic, file=sys.stderr)
        return 1
    except FileExistsError:
        print("pfc: output already exists; choose a new path or --dry-run (only env accepts --force)", file=sys.stderr)
        return 1
    except (ToolchainError, OSError, ValueError) as error:
        if arguments.debug:
            raise
        print(f"pfc: {error}", file=sys.stderr)
        return 1
