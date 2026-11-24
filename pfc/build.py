"""One-source build boundary: identical flags, exclusive output and complete evidence."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pfc import difftest, ir, smt
from pfc.config import C_FLAGS, DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.digest import sha256_text
from pfc.emit import Emission, Mode, emit
from pfc.environment import Environment, collect_environment, resolve_compiler
from pfc.facts import DEFAULT_FACTS, FactName
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.report import write_json
from pfc.target import read_target


def require_new_directory(output: Path, *, dry_run: bool = False) -> None:
    """Validate before work and use exclusive mkdir again at the write boundary."""
    if "\x00" in str(output):
        raise FrontendError("build-output", "output path cannot contain NUL", SourceLocation("<output>"))
    if not dry_run and (output.exists() or output.is_symlink()):
        raise FrontendError(
            "build-output", "choose a new directory; existing evidence is preserved", SourceLocation(str(output))
        )


@dataclass(frozen=True)
class BuildReport:
    source: str
    source_id: str
    driver_id: str | None
    environment: Environment
    flags: tuple[str, ...]
    emission: Emission
    artifact: str
    dry_run: bool
    compilation: difftest.ProcessResult | None
    passed: bool


def emission_passed(emission: Emission) -> bool:
    """Ablation is intentional retention; failed required verification still fails."""
    return not emission.proof or (
        emission.proof.required_passed
        and ("delete" not in emission.facts or all(d.removed for d in emission.decisions))
    )


def build_source(
    source: Path,
    output: Path,
    *,
    mode: Mode,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    facts: frozenset[FactName] = DEFAULT_FACTS,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
    flags: tuple[str, ...] = C_FLAGS,
    driver: Path | None = None,
    runner: Callable[[tuple[str, ...], Path], difftest.ProcessResult] = difftest.invoke,
) -> BuildReport:
    """Build an object or link an explicitly supplied driver; never execute the result.

    Dry-run validates input/proofs/toolchain but does not create files or compile.
    Failed compilations and unproved verified builds retain their evidence.
    """
    require_new_directory(output, dry_run=dry_run)
    program = lower(parse_source(source, cpp=cpp))
    target = read_target(compiler)
    environment = collect_environment(compiler)
    proof = smt.check(program, target, timeout_ms=timeout_ms) if mode == "verified" else None
    emission = emit(program, target, mode=mode, proof=proof, facts=facts)
    driver_id = sha256_text(driver.read_text(encoding="utf-8")) if driver else None
    artifact = "program" if driver else "kernel.o"
    compilation = None
    if not dry_run:
        output.mkdir(parents=True, exist_ok=False)
        generated = output / "kernel.c"
        generated.write_text(emission.c, encoding="utf-8")
        executable = resolve_compiler(compiler)
        arguments = (
            executable,
            *flags,
            str(generated.resolve()),
            *((str(driver.resolve()),) if driver else ("-c",)),
            "-o",
            str((output / artifact).resolve()),
        )
        compilation = runner(arguments, output)
    report = BuildReport(
        str(source),
        sha256_text(ir.print_c(program)),
        driver_id,
        environment,
        flags,
        emission,
        artifact,
        dry_run,
        compilation,
        emission_passed(emission) and (dry_run or compilation is not None and compilation.status == "ok"),
    )
    if not dry_run:
        write_json(output / "build.json", report)
    return report
