"""Expected-trap campaign (revision plan, phase 2D; ADR-0058): inputs the interpreter rejects must trap.

Two classes of rejected inputs are drawn from a kernel's profile: `precondition` (a stressed draw the interpreter
rejects with `PreconditionViolated`) and `overflow` (a draw with extreme array values the interpreter rejects with
`Overflow`). Each case runs once per instrumented build. The expectation is fixed before running: an overflow case
must trap in both builds (every unproved `+ - *` carries a trap and a proved site cannot overflow on admitted inputs);
a precondition case must trap in `verified` iff one of the conjuncts it falsifies is *rendered* as an entry check
(`emission.requires_checks`), which quantifies the skipped-conjunct gap; against `checked` the precondition class is
context only (no entry check exists, a guard may or may not fire). SIGILL (-4) is the trap, as for G0 (Linux/x86).
Operational failures (compile, timeout, other signal) are never agreement.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from typing import Literal

from pfc import ir, smt, workflow
from pfc.config import C_FLAGS, DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.emit import Emission, emit
from pfc.environment import resolve_compiler
from pfc.target import Target
from pfc_eval import campaign, conjuncts, differential, evidence, generate, interpret

CaseKind = Literal["precondition", "overflow"]
Build = Literal["checked", "verified"]
Observed = Literal["sigill", "completed", "other-signal", "timeout", "compile", "error"]
CASES_PER_CLASS = 20
DRAW_BUDGET_FACTOR = 50
PROCESS_TIMEOUT_S = 60.0
SIGILL = -4
SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("case_id", "string"),
        evidence.Column("class", "string", allowed=("precondition", "overflow")),
        evidence.Column("build", "string", allowed=("checked", "verified")),
        evidence.Column("rejected_reason", "string"),
        evidence.Column("rejection_line", "integer", nullable=True),
        evidence.Column("rejection_column", "integer", nullable=True),
        evidence.Column("false_conjuncts", "string"),
        evidence.Column("rendered_conjuncts", "string"),
        evidence.Column("expected_trap", "boolean", nullable=True),
        evidence.Column(
            "observed", "string", allowed=("sigill", "completed", "other-signal", "timeout", "compile", "error")
        ),
        evidence.Column("returncode", "integer", nullable=True),
        evidence.Column("agreement", "boolean", nullable=True),
        evidence.Column("total_length", "integer"),
    )
)


@dataclass(frozen=True)
class Case:
    identifier: str
    kind: CaseKind
    inputs: interpret.Inputs
    rejection: interpret.Rejection
    false_conjuncts: tuple[int, ...]  # flat conjunct indices (source order over every REQUIRES) that evaluate false


@dataclass(frozen=True)
class Outcome:
    observed: Observed
    returncode: int | None


@dataclass(frozen=True)
class KernelResult:
    cases: tuple[Case, ...]
    rendered: frozenset[int]
    outcomes: Mapping[tuple[str, Build], Outcome]
    proof: smt.CheckReport
    emissions: Mapping[Build, Emission]


def _extreme(profile: generate.Profile, target: Target) -> generate.Profile:
    low, high = target.long_range()
    arrays = {name: replace(spec, low=low, high=high) for name, spec in profile.arrays.items()}
    return replace(profile, arrays=arrays)


def collect(
    program: ir.Program,
    profile: generate.Profile,
    target: Target,
    *,
    seed: int,
    per_class: int = CASES_PER_CLASS,
    fuel: int = interpret.DEFAULT_FUEL,
) -> tuple[Case, ...]:
    """Seeded rejected inputs: `per_class` precondition violations (stress 1.0) and overflows (extreme values)."""
    entry = generate.bind(profile, program)
    parts = conjuncts.conjuncts(entry)
    rng = Random(seed)
    stressed = replace(profile, stress=1.0)
    extreme = _extreme(profile, target)
    preconditions: list[Case] = []
    overflows: list[Case] = []
    budget = DRAW_BUDGET_FACTOR * per_class
    for _ in range(budget):
        if len(preconditions) >= per_class and len(overflows) >= per_class:
            break
        if len(preconditions) < per_class:
            inputs = generate.draw(stressed, rng)
            outcome = interpret.run(program, inputs, target=target, function=entry.name, fuel=fuel)
            if isinstance(outcome, interpret.Rejection) and outcome.kind == "PreconditionViolated":
                false = tuple(
                    index
                    for index, part in enumerate(parts)
                    if interpret.evaluate_condition(
                        program, inputs, part.expression, target=target, function=entry.name
                    )
                    is False
                )
                preconditions.append(Case(f"p{len(preconditions):04d}", "precondition", inputs, outcome, false))
        if len(overflows) < per_class:
            inputs = generate.draw(extreme, rng)
            outcome = interpret.run(program, inputs, target=target, function=entry.name, fuel=fuel)
            if isinstance(outcome, interpret.Rejection) and outcome.kind == "Overflow":
                overflows.append(Case(f"o{len(overflows):04d}", "overflow", inputs, outcome, ()))
    return tuple(preconditions + overflows)


def expected_trap(
    kind: CaseKind, build: Build, *, false_conjuncts: Sequence[int], rendered: set[int] | frozenset[int]
) -> bool | None:
    """Pre-registered expectation: overflow always traps; a precondition violation traps in verified iff a rendered
    conjunct is false; against checked the precondition class has no expectation (context)."""
    if kind == "overflow":
        return True
    if build == "checked":
        return None
    return any(index in rendered for index in false_conjuncts)


def _observe(outcome: differential.ProcessOutcome) -> Outcome:
    if outcome.status == "ok":
        return Outcome("completed", 0)
    if outcome.status == "crash":
        return Outcome("sigill" if outcome.returncode == SIGILL else "other-signal", outcome.returncode)
    if outcome.status == "timeout":
        return Outcome("timeout", None)
    return Outcome("error", outcome.returncode)


def run_kernel(
    program: ir.Program,
    profile: generate.Profile,
    cases: Sequence[Case],
    *,
    compiler: str,
    target: Target,
    runner: differential.Runner = differential.invoke,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    process_timeout_s: float = PROCESS_TIMEOUT_S,
) -> KernelResult:
    """Prove, emit both instrumented builds, compile each once with the file-based driver, run one process per case."""
    function = generate.bind(profile, program)
    proof = smt.check(program, target, timeout_ms=timeout_ms)
    emissions: dict[Build, Emission] = {
        "checked": emit(program, target, mode="checked"),
        "verified": emit(program, target, mode="verified", proof=proof),
    }
    checks = emissions["verified"].requires_checks
    rendered = frozenset(index for index, check in enumerate(checks) if check.rendered)
    outcomes: dict[tuple[str, Build], Outcome] = {}
    executable = resolve_compiler(compiler)
    for build, emission in emissions.items():
        with TemporaryDirectory(prefix="pfc-eval-negative-") as temporary:
            directory = Path(temporary)
            (directory / "kernel.c").write_text(emission.c, encoding="utf-8")
            (directory / "driver.c").write_text(differential.driver_source(function, target), encoding="utf-8")
            compiled = runner(
                (
                    executable,
                    *C_FLAGS,
                    str(directory / "kernel.c"),
                    str(directory / "driver.c"),
                    "-o",
                    str(directory / build),
                ),
                directory,
                differential.PROCESS_TIMEOUT_S,
            )
            for case in cases:
                if compiled.status != "ok":
                    outcomes[(case.identifier, build)] = Outcome("compile", compiled.returncode)
                    continue
                encoded = generate.Case(case.identifier, False, case.inputs, interpret.Observation(0, {}, 0, ()))
                (directory / "case.bin").write_bytes(differential.encode_cases((encoded,), function, target))
                outcome = runner(
                    (str(directory / build), str(directory / "case.bin"), str(directory / "out.bin")),
                    directory,
                    process_timeout_s,
                )
                outcomes[(case.identifier, build)] = _observe(outcome)
    return KernelResult(tuple(cases), rendered, outcomes, proof, emissions)


def rows(result: KernelResult, kernel: str) -> list[dict[str, object]]:
    table: list[dict[str, object]] = []
    for case in result.cases:
        for build in ("checked", "verified"):
            outcome = result.outcomes[(case.identifier, build)]
            expected = expected_trap(case.kind, build, false_conjuncts=case.false_conjuncts, rendered=result.rendered)
            if expected is None:
                agreement: bool | None = None
            elif outcome.observed not in ("sigill", "completed"):
                agreement = False  # an operational failure is never agreement
            else:
                agreement = (outcome.observed == "sigill") == expected
            location = case.rejection.location
            table.append(
                {
                    "kernel": kernel,
                    "case_id": case.identifier,
                    "class": case.kind,
                    "build": build,
                    "rejected_reason": f"{case.rejection.kind}: {case.rejection.message}",
                    "rejection_line": location.line if location is not None else None,
                    "rejection_column": location.column if location is not None else None,
                    "false_conjuncts": ";".join(str(i) for i in case.false_conjuncts),
                    "rendered_conjuncts": ";".join(str(i) for i in sorted(result.rendered)),
                    "expected_trap": expected,
                    "observed": outcome.observed,
                    "returncode": outcome.returncode,
                    "agreement": agreement,
                    "total_length": sum(len(v) for v in case.inputs.arrays.values()),
                }
            )
    return table


def summarize(results: Mapping[str, Sequence[Mapping[str, object]]]) -> dict[str, object]:
    kernels: dict[str, object] = {}
    passed = True
    for name, table in results.items():
        pre = [r for r in table if r["class"] == "precondition"]
        ovf = [r for r in table if r["class"] == "overflow"]
        verified_pre = [r for r in pre if r["build"] == "verified"]
        skipped = sum(r["expected_trap"] is False for r in verified_pre)
        operational = sum(r["observed"] not in ("sigill", "completed") for r in table)
        disagreements = sum(r["agreement"] is False for r in table)
        kernels[name] = {
            "precondition": {
                "cases": len(verified_pre),
                "verified_traps": sum(r["observed"] == "sigill" for r in verified_pre),
                "skipped_conjunct_cases": skipped,
                "checked_traps": sum(r["observed"] == "sigill" for r in pre if r["build"] == "checked"),
                "disagreements": sum(r["agreement"] is False for r in verified_pre),
            },
            "overflow": {
                "cases": len(ovf) // 2,
                "checked_traps": sum(r["observed"] == "sigill" for r in ovf if r["build"] == "checked"),
                "verified_traps": sum(r["observed"] == "sigill" for r in ovf if r["build"] == "verified"),
                "disagreements": sum(r["agreement"] is False for r in ovf),
            },
            "operational_failures": operational,
            "passed": disagreements == 0 and operational == 0,
        }
        passed = passed and disagreements == 0 and operational == 0
    return {
        "experiment": "negative",
        "kernels": kernels,
        "passed": passed,
        "statement": (
            "Expected traps: overflow cases trap in both instrumented builds; precondition violations trap in verified "
            "when a rendered conjunct is false; skipped-conjunct cases are the disclosed gap. SIGILL on Linux/x86 is "
            "the trap. Agreement never counts a compile, timeout or other-signal outcome."
        ),
    }


def render_table(summary: Mapping[str, object]) -> str:
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    lines = [
        "| kernel | precondition cases | verified traps | skipped-conjunct cases | overflow cases | checked traps "
        "| verified traps | disagreements | operational failures |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, entry in kernels.items():
        pre, ovf = entry["precondition"], entry["overflow"]
        lines.append(
            f"| {name} | {pre['cases']} | {pre['verified_traps']} | {pre['skipped_conjunct_cases']} | {ovf['cases']} | "
            f"{ovf['checked_traps']} | {ovf['verified_traps']} | {pre['disagreements'] + ovf['disagreements']} | "
            f"{entry['operational_failures']} |"
        )
    lines.append("")
    lines.append(str(summary["statement"]))
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "case"]


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = 0,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    cases: int = CASES_PER_CLASS,
    profiles_dir: Path = generate.PROFILES_DIR,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    sources = workflow.discover(tuple(directories))
    loaded = differential._load_profiles(sources, profiles_dir, cpp)
    if dry_run:
        for name, program, _, path in loaded:
            functions = len(program.functions)
            print(f"{name}: {cases} precondition + {cases} overflow cases from {path}; {functions} function(s)")
        print(
            f"Preview: seed {seed}, solver timeout {timeout_ms} ms, builds checked and verified. No files were written."
        )
        return True
    run = campaign.start(
        "negative",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "timeout_ms": timeout_ms,
            "cases_per_class": cases,
            "draw_budget_factor": DRAW_BUDGET_FACTOR,
            "trap_signal": SIGILL,
            "profiles": {name: generate.file_digest(path) for name, _, _, path in loaded},
            "kernels": [name for name, _, _, _ in loaded],
        },
        schema=SCHEMA,
    )
    tables: dict[str, list[dict[str, object]]] = {}
    for name, program, profile, _ in loaded:
        if progress:
            progress(f"negative: {name}")
        collected = collect(program, profile, run.target, seed=seed, per_class=cases)
        result = run_kernel(program, profile, collected, compiler=compiler, target=run.target, timeout_ms=timeout_ms)
        tables[name] = rows(result, name)
        for row in tables[name]:
            run.evidence.record("case", name, f"{row['case_id']}-{row['build']}", row)
        for build, emission in result.emissions.items():
            run.evidence.write_text(f"sources/{name}-{build}.c", emission.c)
    summary = summarize(tables)
    campaign.finish(run, summary, render_table(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    namespace = dict(vars(arguments))
    per_class = namespace.pop("cases", None)
    runner = partial(run_directories, cases=int(per_class)) if per_class is not None else run_directories
    return campaign.run_command(namespace, runner)
