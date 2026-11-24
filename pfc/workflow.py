"""Directory workflows; pure compiler stages remain independent of CLI and persistence."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pfc import breakit, difftest, report, smt
from pfc.build import build_source, require_new_directory
from pfc.config import DEFAULT_CPP, DIFF_SEED, SOLVER_TIMEOUT_MS
from pfc.diagnostics import Diagnostic, FrontendError, SourceLocation
from pfc.emit import Mode
from pfc.facts import DEFAULT_FACTS, FactName
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target


@dataclass(frozen=True)
class Verification:
    source: str
    proof: smt.CheckReport | None
    diagnostic: Diagnostic | None = None


@dataclass(frozen=True)
class KernelTest:
    differential: difftest.KernelDiffReport
    breakit: breakit.KernelBreakReport


def discover(directories: tuple[Path, ...]) -> tuple[Path, ...]:
    """Accept kernel directories or their immediate parent; never silently skip a kernel.c."""
    found: set[Path] = set()
    for directory in directories:
        try:
            directory = directory.resolve()
            if not directory.is_dir():
                raise ValueError("not a directory")
            paths = (
                [directory / "kernel.c"] if (directory / "kernel.c").is_file() else sorted(directory.glob("*/kernel.c"))
            )
            if not paths:
                raise ValueError("no kernel.c")
            found.update(path.resolve() for path in paths)
        except (OSError, ValueError) as error:
            raise FrontendError(
                "workflow-input",
                "expected a kernel directory or parent containing kernel.c",
                SourceLocation(str(directory)),
            ) from error
    if not found or len({path.parent.name for path in found}) != len(found):
        raise FrontendError(
            "workflow-input", "kernel directory names must be nonempty and unique", SourceLocation("<inputs>")
        )
    return tuple(sorted(found))


def _summary(kind: Literal["verify", "build", "test"], rows: list[report.Row], dry_run: bool) -> report.Summary:
    assert kind in {"verify", "build", "test"}
    return report.Summary(kind, tuple(rows), all(row.passed for row in rows), dry_run)


def verify(
    directories: tuple[Path, ...], *, compiler: str, cpp: str = DEFAULT_CPP, timeout_ms: int = SOLVER_TIMEOUT_MS
) -> report.Summary:
    sources = discover(directories)
    target = read_target(compiler)
    cache = smt.SolverCache()
    rows = []
    for source in sources:
        try:
            proof = smt.check(lower(parse_source(source, cpp=cpp)), target, timeout_ms=timeout_ms, cache=cache)
            rows.append(report.row(source.parent.name, proof, passed=proof.required_passed))
        except FrontendError as error:
            rows.append(report.row(source.parent.name, None, passed=False, problem=str(error)))
    return _summary("verify", rows, False)


def build(
    directories: tuple[Path, ...],
    output: Path,
    *,
    compiler: str,
    mode: Mode,
    cpp: str = DEFAULT_CPP,
    facts: frozenset[FactName] = DEFAULT_FACTS,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
) -> report.Summary:
    require_new_directory(output, dry_run=dry_run)
    sources = discover(directories)
    if not dry_run:
        output.mkdir(parents=True, exist_ok=False)
    rows = []
    for source in sources:
        name = source.parent.name
        driver = source.parent / "main.c"
        try:
            result = build_source(
                source,
                output / name,
                mode=mode,
                compiler=compiler,
                cpp=cpp,
                facts=facts,
                timeout_ms=timeout_ms,
                dry_run=dry_run,
                driver=driver if driver.is_file() else None,
            )
            rows.append(
                report.row(
                    name,
                    result.emission.proof,
                    emission=result.emission,
                    passed=result.passed,
                    problem=result.compilation.message if result.compilation and not result.passed else None,
                )
            )
        except FrontendError as error:
            rows.append(report.row(name, None, passed=False, problem=str(error)))
    summary = _summary("build", rows, dry_run)
    if not dry_run:
        report.seal(output, summary)
    return summary


def test(
    directories: tuple[Path, ...],
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = DIFF_SEED,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    output: Path | None = None,
    dry_run: bool = False,
) -> report.Summary:
    if output:
        require_new_directory(output, dry_run=dry_run)
    sources = discover(directories)
    target = read_target(compiler)
    if output and not dry_run:
        output.mkdir(parents=True, exist_ok=False)
    rows = []
    for index, source in enumerate(sources):
        try:
            program = lower(parse_source(source, cpp=cpp))
            differential = difftest.run_kernel(program, compiler=compiler, seed=seed, timeout_ms=timeout_ms)
            ablation = breakit.check_kernel(program, target, timeout_ms=timeout_ms)
            verified = differential.emissions[-1]
            problem = None
            if not ablation.passed:
                problem = "dependent-consequence break-it failed; inspect per-contract guards and alias facts"
            if differential.status != "passed":
                problem = differential.problem or differential.status
            rows.append(
                report.row(
                    source.parent.name,
                    verified.proof,
                    emission=verified,
                    compared=differential.compared,
                    agreed=differential.agreed,
                    breakit=ablation.passed,
                    passed=differential.status == "passed" and ablation.passed,
                    problem=problem,
                )
            )
            if output and not dry_run:
                report.write_json(output / f"kernel-{index}.json", KernelTest(differential, ablation))
        except (FrontendError, ValueError) as error:
            rows.append(report.row(source.parent.name, None, passed=False, problem=str(error)))
    summary = _summary("test", rows, dry_run)
    if output and not dry_run:
        report.seal(output, summary)
    return summary
