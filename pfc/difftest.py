"""Reproducible, valid-input differential testing of the reviewed sum slice.

This is not a general C fuzzer: a pinned normalized program profile prevents
applying sum's input/oracle semantics to an arbitrary source with a similar ABI.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal

from pfc import ir, smt
from pfc.config import (
    C_FLAGS,
    DIFF_CASES,
    DIFF_SEED,
    DIFF_VALUE_BOUND,
    MAX_DIFF_FAILURES,
    MAX_DIFF_LENGTH,
    MAX_DIFF_OUTPUT_BYTES,
    MAX_DIFF_SEED,
    PROCESS_TIMEOUT_SECONDS,
    SOLVER_TIMEOUT_MS,
)
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.digest import sha256_text
from pfc.emit import Decision, Emission, emit
from pfc.environment import Environment, ToolchainError, collect_environment, compiler_environment, resolve_compiler
from pfc.target import Target, read_target

if TYPE_CHECKING:
    from pfc.corpus import Case

# Reviewed print_c(kernel.c) identity, independent of source locations. Changes
# require a new profile and a semantic review of generator/oracle, not auto-update.
SUM_PROFILE = "28d7c5e5a415d7728df1cab9d29c6b6d958b156a043ba58eeac066f870a00c53"
GENERATOR = "sum-inputs-v1: 10 boundary cases + 990 Random(seed) cases; max-length=64"


@dataclass(frozen=True)
class SumInput:
    values: tuple[int, ...]
    n: int


@dataclass(frozen=True)
class Observation:
    value: int
    values: tuple[int, ...]


@dataclass(frozen=True)
class Failure:
    index: int
    case: SumInput
    expected: Observation
    checked: Observation
    verified: Observation


@dataclass(frozen=True)
class Comparison:
    compared: int
    agreed: int
    failures: tuple[Failure, ...]


@dataclass(frozen=True)
class ProcessResult:
    status: Literal["ok", "timeout", "error"]
    stdout: str
    returncode: int | None
    message: str


@dataclass(frozen=True)
class DiffReport:
    seed: int
    generator: str
    profile_id: str
    cases: int
    dataset_id: str
    context_id: str
    target: Target
    environment: Environment
    flags: tuple[str, ...]
    checked_c_id: str
    verified_c_id: str
    driver_id: str
    proof: smt.CheckReport
    decisions: tuple[Decision, ...]
    status: Literal["passed", "mismatch", "proof-failed", "compile-error", "run-error", "timeout", "protocol-error"]
    compared: int = 0
    agreed: int = 0
    failures: tuple[Failure, ...] = ()
    problem: str | None = None


def _invalid(message: str) -> FrontendError:
    return FrontendError("difftest-input", message, SourceLocation("<sum-input>"))


def require_sum_profile(program: ir.Program) -> None:
    """Reject unreviewed behavior before generating or executing any inputs."""
    if sha256_text(ir.print_c(program)) != SUM_PROFILE:
        location = program.functions[0].location if program.functions else SourceLocation("<IR>")
        raise FrontendError("difftest-profile", "this harness accepts only the reviewed sum program", location)


def validate_input(case: SumInput, target: Target) -> None:
    """Check all stored longs and every executed prefix sum using exact integers."""
    if not 32 <= target.long_bits <= 64:
        raise _invalid("sum differential testing requires 32..64-bit long")
    low, high = target.long_range()
    if not isinstance(case.values, tuple) or len(case.values) > MAX_DIFF_LENGTH:
        raise _invalid("array exceeds the differential input limit")
    if type(case.n) is not int or not 0 <= case.n <= len(case.values):
        raise _invalid("input does not satisfy 0 <= n <= a_len")
    if any(type(value) is not int or not low <= value <= high for value in case.values):
        raise _invalid("array elements must be representable C longs")
    accumulator = 0
    for value in case.values[: case.n]:
        accumulator += value
        if not low <= accumulator <= high:
            raise _invalid("an intermediate sum is not representable in C long")


def generate_inputs(target: Target, *, seed: int = DIFF_SEED) -> tuple[SumInput, ...]:
    """Generate exactly 1000 reproducible cases; bounds are part of the versioned profile."""
    if type(seed) is not int or not 0 <= seed <= MAX_DIFF_SEED:
        raise _invalid(f"seed must be an integer in 0..{MAX_DIFF_SEED}")
    low, high = target.long_range()
    cases = [
        SumInput((), 0),
        SumInput((high, high), 0),
        SumInput((high,), 1),
        SumInput((low,), 1),
        SumInput((low, high), 2),
        SumInput((high, -high), 2),
        SumInput((-high, high), 2),
        SumInput((7, -3, 9), 2),
        SumInput((0,) * MAX_DIFF_LENGTH, MAX_DIFF_LENGTH),
        SumInput((low, 0), 2),
    ]
    random = Random(seed)
    bound = min(DIFF_VALUE_BOUND, high // MAX_DIFF_LENGTH)
    while len(cases) < DIFF_CASES:
        length = random.randint(0, MAX_DIFF_LENGTH)
        values = tuple(random.randint(-bound, bound) for _ in range(length))
        cases.append(SumInput(values, random.randint(0, length)))
    for case in cases:
        validate_input(case, target)
    return tuple(cases)


def driver_source(cases: tuple[SumInput, ...], target: Target) -> str:
    """Create a fixed batch driver, with no parser for arbitrary external input."""
    if not cases or len(cases) > DIFF_CASES:
        raise _invalid(f"driver requires 1..{DIFF_CASES} cases")
    for case in cases:
        validate_input(case, target)
    rows = ",\n".join(
        "{" + ",".join(target.long_literal(value) for value in case.values or (0,)) + "}" for case in cases
    )
    lengths = ",".join(str(len(case.values)) for case in cases)
    prefixes = ",".join(str(case.n) for case in cases)
    return f"""/* pfc sum batch driver: generated valid values, no stdin interface. */
#include <stdio.h>
#include <sys/resource.h>
long sum(const long[], long, long);
static const long inputs[{len(cases)}][{MAX_DIFF_LENGTH}] = {{
{rows}
}};
static const long lengths[] = {{{lengths}}};
static const long prefixes[] = {{{prefixes}}};
int main(void)
{{
    struct rlimit core_limit = {{0, 0}};
    if (setrlimit(RLIMIT_CORE, &core_limit) != 0) return 2;
    for (long row = 0; row < {len(cases)}; row++) {{
        long values[{MAX_DIFF_LENGTH}] = {{0}};
        for (long j = 0; j < lengths[row]; j++) values[j] = inputs[row][j];
        long result = sum(values, lengths[row], prefixes[row]);
        printf("%ld %ld %ld", row, result, lengths[row]);
        for (long j = 0; j < lengths[row]; j++) printf(" %ld", values[j]);
        putchar('\\n');
    }}
    return ferror(stdout) ? 2 : 0;
}}
"""


def parse_observations(text: str, cases: tuple[SumInput, ...], target: Target) -> tuple[Observation, ...]:
    """Reject incomplete/extra rows, wrong IDs and values outside the target domain."""
    lines = text.splitlines()
    if len(lines) != len(cases):
        raise ValueError("binary output row count mismatch")
    low, high = target.long_range()
    observations = []
    for index, (line, case) in enumerate(zip(lines, cases, strict=True)):
        tokens = line.split()
        if len(tokens) != len(case.values) + 3 or any(
            re.fullmatch(r"-?[0-9]{1,20}", token) is None for token in tokens
        ):
            raise ValueError("binary output row format mismatch")
        numbers = tuple(map(int, tokens))
        if numbers[0] != index or numbers[2] != len(case.values) or any(not low <= value <= high for value in numbers):
            raise ValueError("binary output identity, length or integer range mismatch")
        observations.append(Observation(numbers[1], numbers[3:]))
    return tuple(observations)


def compare(
    cases: tuple[SumInput, ...], checked: tuple[Observation, ...], verified: tuple[Observation, ...]
) -> Comparison:
    """Compare full observations and an independent mathematical sum/read-only oracle."""
    if not cases or len(cases) != len(checked) or len(cases) != len(verified):
        raise ValueError("comparison requires complete, nonempty observations")
    failures: list[Failure] = []
    agreed = 0
    for index, (case, left, right) in enumerate(zip(cases, checked, verified, strict=True)):
        expected = Observation(sum(case.values[: case.n]), case.values)
        if left == right == expected:
            agreed += 1
        elif len(failures) < MAX_DIFF_FAILURES:
            failures.append(Failure(index, case, expected, left, right))
    return Comparison(len(cases), agreed, tuple(failures))


def invoke(arguments: tuple[str, ...], cwd: Path) -> ProcessResult:
    """Explicit process boundary with bounded time and sanitized diagnostics.

    Output size is checked after capture, not an OS memory sandbox. The fixed
    driver disables core dumps; these binaries are generated from the sum profile.
    """
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=PROCESS_TIMEOUT_SECONDS,
            check=False,
            env=compiler_environment(),
        )
    except subprocess.TimeoutExpired:
        return ProcessResult("timeout", "", None, "process timeout")
    except (OSError, UnicodeError, ValueError):
        return ProcessResult("error", "", None, "cannot execute process or decode output")
    if result.returncode != 0:
        return ProcessResult("error", "", result.returncode, f"process exited {result.returncode}")
    if len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8")) > MAX_DIFF_OUTPUT_BYTES:
        return ProcessResult("error", "", result.returncode, "process output exceeds limit")
    if result.stderr:
        return ProcessResult("error", "", result.returncode, "process produced unexpected diagnostics")
    return ProcessResult("ok", result.stdout, result.returncode, "")


def execute_variant(
    emission: Emission,
    driver: Path,
    directory: Path,
    compiler: str,
    runner: Callable[[tuple[str, ...], Path], ProcessResult],
) -> tuple[ProcessResult, ProcessResult | None]:
    """Common compile/run boundary; all modes use the same C_FLAGS and driver."""
    source = directory / f"{emission.mode}.c"
    source.write_text(emission.c, encoding="utf-8")
    binary = directory / emission.mode
    compiled = runner((compiler, *C_FLAGS, str(source), str(driver), "-o", str(binary)), directory)
    return compiled, runner((str(binary),), directory) if compiled.status == "ok" else None


def run_sum(
    program: ir.Program,
    *,
    compiler: str,
    seed: int = DIFF_SEED,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    runner: Callable[[tuple[str, ...], Path], ProcessResult] = invoke,
) -> DiffReport:
    """Compile two modes in an isolated temporary directory and compare 1000 cases.

    This boundary creates/deletes only its own temporary files. Reports preserve
    failure seed/case and identities; arbitrary source and process stderr are not logged.
    """
    require_sum_profile(program)
    try:
        target = read_target(compiler)
        environment = collect_environment(compiler)
    except ToolchainError as error:
        raise FrontendError("difftest-toolchain", str(error), program.functions[0].location) from error
    executable = resolve_compiler(compiler)
    cases = generate_inputs(target, seed=seed)
    proof = smt.check(program, target, timeout_ms=timeout_ms)
    checked = emit(program, target, mode="checked")
    verified = emit(program, target, mode="verified", proof=proof)
    driver = driver_source(cases, target)
    dataset = json.dumps(ir.to_data((GENERATOR, target.long_bits, cases)), sort_keys=True, separators=(",", ":"))
    report = DiffReport(
        seed,
        GENERATOR,
        SUM_PROFILE,
        len(cases),
        sha256_text(dataset),
        checked.context_id,
        target,
        environment,
        C_FLAGS,
        sha256_text(checked.c),
        sha256_text(verified.c),
        sha256_text(driver),
        proof,
        verified.decisions,
        "proof-failed",
    )
    if not proof.required_passed or not verified.decisions or not all(item.removed for item in verified.decisions):
        return replace(report, problem="verified sum did not obtain every required proof and guard removal")
    observations: list[tuple[Observation, ...]] = []
    with TemporaryDirectory(prefix="pfc-difftest-") as temporary:
        directory = Path(temporary)
        driver_path = directory / "driver.c"
        driver_path.write_text(driver, encoding="utf-8")
        for mode in (checked, verified):
            compiled, result = execute_variant(mode, driver_path, directory, executable, runner)
            if compiled.status != "ok":
                return replace(
                    report,
                    status="timeout" if compiled.status == "timeout" else "compile-error",
                    problem=f"{mode.mode} compile: {compiled.message}",
                )
            assert result is not None
            if result.status != "ok":
                return replace(
                    report,
                    status="timeout" if result.status == "timeout" else "run-error",
                    problem=f"{mode.mode} run: {result.message}",
                )
            try:
                observations.append(parse_observations(result.stdout, cases, target))
            except ValueError as error:
                return replace(report, status="protocol-error", problem=f"{mode.mode}: {error}")
    comparison = compare(cases, observations[0], observations[1])
    return replace(
        report,
        status="passed" if comparison.agreed == len(cases) else "mismatch",
        compared=comparison.compared,
        agreed=comparison.agreed,
        failures=comparison.failures,
    )


def render_report(report: DiffReport) -> str:
    """Render observed agreement; it is not an equivalence proof or a timing benchmark."""
    lines = [
        f"sum differential: {report.agreed}/{report.compared} agreed; {report.cases} planned; "
        f"{report.status}; seed={report.seed}",
        "Comparison covers return values and every array element against both modes and the sum/read-only oracle.",
        f"Dataset: {report.dataset_id}",
    ]
    if report.problem:
        lines.append(report.problem)
    for failure in report.failures:
        lines.append(f"case {failure.index}: n={failure.case.n}, values={failure.case.values}")
        lines.append(f"  expected={failure.expected}; checked={failure.checked}; verified={failure.verified}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class KernelFailure:
    index: int
    case: Case
    expected: Observation
    observed: tuple[Observation, ...]


@dataclass(frozen=True)
class KernelDiffReport:
    kernel: str
    seed: int
    generator: str
    profile_id: str
    dataset_id: str
    driver_id: str
    environment: Environment
    flags: tuple[str, ...]
    emissions: tuple[Emission, ...]
    cases: int
    compared: int
    agreed: int
    failures: tuple[KernelFailure, ...]
    status: str
    problem: str | None = None


def run_kernel(
    program: ir.Program,
    *,
    compiler: str,
    seed: int = DIFF_SEED,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    runner: Callable[[tuple[str, ...], Path], ProcessResult] = invoke,
) -> KernelDiffReport:
    """Three-mode differential experiment for an exact reviewed profile."""
    from pfc import corpus

    name = corpus.profile(program)
    target = read_target(compiler)
    environment = collect_environment(compiler)
    cases = corpus.generate(name, target, seed=seed)
    proof = smt.check(program, target, timeout_ms=timeout_ms)
    emissions = (
        emit(program, target, mode="plain"),
        emit(program, target, mode="checked"),
        emit(program, target, mode="verified", proof=proof),
    )
    driver = corpus.driver_source(program, cases, target)
    report = KernelDiffReport(
        name,
        seed,
        corpus.GENERATOR,
        corpus.PROFILES[name],
        sha256_text(json.dumps(ir.to_data(cases), sort_keys=True)),
        sha256_text(driver),
        environment,
        C_FLAGS,
        emissions,
        len(cases),
        0,
        0,
        (),
        "proof-failed",
    )
    if not proof.required_passed:
        return replace(report, problem="required verification did not pass")
    observations: list[tuple[Observation, ...]] = []
    executable = resolve_compiler(compiler)
    with TemporaryDirectory(prefix="pfc-difftest-") as temporary:
        directory = Path(temporary)
        driver_path = directory / "driver.c"
        driver_path.write_text(driver, encoding="utf-8")
        for emission in emissions:
            compiled, result = execute_variant(emission, driver_path, directory, executable, runner)
            if compiled.status != "ok":
                return replace(
                    report,
                    status="timeout" if compiled.status == "timeout" else "compile-error",
                    problem=f"{emission.mode}: {compiled.message}",
                )
            assert result is not None
            if result.status != "ok":
                return replace(
                    report,
                    status="timeout" if result.status == "timeout" else "run-error",
                    problem=f"{emission.mode}: {result.message}",
                )
            try:
                observations.append(
                    parse_observations(result.stdout, tuple(SumInput(case.values, case.n) for case in cases), target)
                )
            except ValueError as error:
                return replace(report, status="protocol-error", problem=f"{emission.mode}: {error}")
    failures: list[KernelFailure] = []
    agreed = 0
    for index, case in enumerate(cases):
        expected = corpus.oracle(name, case)
        actual = tuple(mode[index] for mode in observations)
        if all(item == actual[0] for item in actual) and corpus.matches(name, case, actual[0]):
            agreed += 1
        elif len(failures) < MAX_DIFF_FAILURES:
            failures.append(KernelFailure(index, case, expected, actual))
    return replace(
        report,
        status="passed" if agreed == len(cases) else "mismatch",
        compared=len(cases),
        agreed=agreed,
        failures=tuple(failures),
    )
