"""General three-build differential test for any subset program (roadmap E3, EV-R10/R11, Table II).

For each kernel: generate accepted cases with the interpreter as acceptor, emit plain/checked/verified
C from one IR with the frozen compiler, compile all three with identical `C_FLAGS`, run each binary
once over a binary case file, and compare the complete observations (return value and every array
cell) pairwise and against the interpreter. Timeout, crash, truncated output and compile failure are
failures of the affected cases, never agreement. An independent retention check compares the emitted
guards with the emission decisions and the proof report, so a guard deleted without proof is detected
even though valid inputs never reach it.
"""

import hashlib
import struct
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal

from pfc import ir, smt, workflow
from pfc.config import C_FLAGS, DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.emit import ENTRY_LINE, GUARD_LINE, OVERFLOW_LINE, Emission, emit
from pfc.environment import compiler_environment, resolve_compiler
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target, read_target
from pfc_eval import campaign, evidence, generate, interpret, stats

ROOT = Path(__file__).resolve().parents[1]
DRIVER_PROTOCOL = "pfc-eval-driver-v1: little-endian target longs; per case, parameters in signature order"
PROCESS_TIMEOUT_S = 600.0
MODES: tuple[Literal["plain", "checked", "verified"], ...] = ("plain", "checked", "verified")
FailureKind = Literal["compile", "timeout", "crash", "error", "truncated", "not-run", "mismatch"]
Runner = Callable[[tuple[str, ...], Path, float], "ProcessOutcome"]

SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("case_id", "string"),
        evidence.Column("boundary", "boolean"),
        evidence.Column("accepted", "boolean"),
        evidence.Column("rejected_reason", "string", nullable=True),
        evidence.Column("total_length", "integer"),
        evidence.Column("agreement", "boolean", nullable=True),
        evidence.Column(
            "failure_kind",
            "string",
            nullable=True,
            allowed=("compile", "timeout", "crash", "error", "truncated", "not-run", "mismatch"),
        ),
        evidence.Column("annotation_violations", "integer", nullable=True),
        evidence.Column("guards_total", "integer", nullable=True),
        evidence.Column("guards_removed", "integer", nullable=True),
    )
)


@dataclass(frozen=True)
class ProcessOutcome:
    status: Literal["ok", "timeout", "crash", "error"]
    returncode: int | None
    elapsed_ms: float
    message: str


@dataclass(frozen=True)
class BuildResult:
    mode: str
    compiled: ProcessOutcome
    executed: ProcessOutcome | None
    observed_cases: int
    retention: tuple[str, ...]
    c_sha256: str


@dataclass(frozen=True)
class CaseResult:
    case: generate.Case
    agreement: bool | None
    failure_kind: FailureKind | None


@dataclass(frozen=True)
class KernelResult:
    kernel: str
    generated: generate.Generated
    proof_required_passed: bool
    builds: tuple[BuildResult, ...]
    cases: tuple[CaseResult, ...]
    failures: int
    passed: bool
    problem: str | None
    sources: dict[str, str]
    guards_total: int | None = None  # executable guard sites of the checked emission (guards endpoint denominator)
    guards_removed: int | None = None  # sites the verified emission removed with the hand invariant (`manual`)


def invoke(arguments: tuple[str, ...], cwd: Path, timeout_s: float) -> ProcessOutcome:
    """Process boundary: bounded time, no shell, sanitized message, signal recorded as a crash."""
    started = perf_counter()
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=compiler_environment(),
        )
    except subprocess.TimeoutExpired:
        return ProcessOutcome("timeout", None, (perf_counter() - started) * 1000, f"process timeout after {timeout_s}s")
    except (OSError, ValueError):
        return ProcessOutcome("error", None, (perf_counter() - started) * 1000, "cannot execute process")
    elapsed = (perf_counter() - started) * 1000
    if result.returncode < 0:
        return ProcessOutcome("crash", result.returncode, elapsed, f"terminated by signal {-result.returncode}")
    if result.returncode != 0:
        return ProcessOutcome("error", result.returncode, elapsed, f"process exited {result.returncode}")
    return ProcessOutcome("ok", 0, elapsed, "")


def _struct(target: Target) -> struct.Struct:
    if target.long_bits == 64:
        return struct.Struct("<q")
    if target.long_bits == 32:
        return struct.Struct("<i")
    raise ValueError("driver protocol supports 32- and 64-bit long only")


def driver_source(function: ir.Function, target: Target) -> str:
    """Host C driver: reads cases from argv[1], writes result and every array cell to argv[2]."""
    parameters = ", ".join(ir._declaration(p.symbol) for p in function.parameters)
    reads = []
    frees = []
    writes = []
    arguments = []
    for parameter in function.parameters:
        name = parameter.symbol.name
        if isinstance(parameter.symbol.type, ir.ArrayType):
            reads.append(
                f"  if (!read_long(in, &count_{name})) return 3;\n"
                f"  long *{name} = malloc((size_t)(count_{name} > 0 ? count_{name} : 1) * sizeof(long));\n"
                f"  if (!{name}) return 4;\n"
                f"  for (long k = 0; k < count_{name}; k++) if (!read_long(in, &{name}[k])) return 3;"
            )
            frees.append(f"  free({name});")
            writes.append(f"  for (long k = 0; k < count_{name}; k++) if (!write_long(out, {name}[k])) return 5;")
            arguments.append(name)
        else:
            reads.append(f"  long {name};\n  if (!read_long(in, &{name})) return 3;")
            cast = "(_Bool)" if parameter.symbol.type is ir.ScalarType.BOOL else ""
            arguments.append(f"{cast}{name}")
    counts = "\n".join(f"  long count_{p.symbol.name} = 0;" for p in function.parameters if p.length is not None)
    width = target.long_bits // 8
    return f"""#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
/* {DRIVER_PROTOCOL} */
{function.return_type} {function.name}({parameters});
_Static_assert(sizeof(long) == {width}, "driver protocol width");
static int read_long(FILE *in, long *value) {{ return fread(value, sizeof(long), 1, in) == 1; }}
static int write_long(FILE *out, long value) {{ return fwrite(&value, sizeof(long), 1, out) == 1; }}
int main(int argc, char **argv) {{
 struct rlimit limit = {{0, 0}};
 if (argc != 3 || setrlimit(RLIMIT_CORE, &limit) != 0) return 2;
 FILE *in = fopen(argv[1], "rb");
 FILE *out = fopen(argv[2], "wb");
 if (!in || !out) return 2;
 long cases = 0;
 if (!read_long(in, &cases)) return 3;
 for (long row = 0; row < cases; row++) {{
{counts}
{chr(10).join(reads)}
  long result = {function.name}({", ".join(arguments)});
  if (!write_long(out, result)) return 5;
{chr(10).join(writes)}
  if (fflush(out) != 0) return 5;
{chr(10).join(frees)}
 }}
 return fclose(out) == 0 ? 0 : 5;
}}
"""


def encode_cases(cases: Sequence[generate.Case], function: ir.Function, target: Target) -> bytes:
    packer = _struct(target)
    chunks = [packer.pack(len(cases))]
    for case in cases:
        for parameter in function.parameters:
            name = parameter.symbol.name
            if isinstance(parameter.symbol.type, ir.ArrayType):
                values = case.inputs.arrays[name]
                chunks.append(packer.pack(len(values)))
                chunks.extend(packer.pack(v) for v in values)
            else:
                chunks.append(packer.pack(case.inputs.scalars[name]))
    return b"".join(chunks)


def _array_names(function: ir.Function) -> tuple[str, ...]:
    return tuple(p.symbol.name for p in function.parameters if isinstance(p.symbol.type, ir.ArrayType))


def expected_bytes(case: generate.Case, function: ir.Function, target: Target) -> bytes:
    packer = _struct(target)
    chunks = [packer.pack(case.expected.result)]
    for name in _array_names(function):
        chunks.extend(packer.pack(v) for v in case.expected.arrays[name])
    return b"".join(chunks)


def decode_observations(
    data: bytes, cases: Sequence[generate.Case], function: ir.Function, target: Target
) -> list[tuple[int, dict[str, tuple[int, ...]]]]:
    """Decode as many complete cases as the output holds; a partial trailing case is dropped."""
    packer = _struct(target)
    width = packer.size
    offset = 0
    observations = []
    for case in cases:
        needed = width * (1 + sum(len(case.inputs.arrays[name]) for name in _array_names(function)))
        if offset + needed > len(data):
            break
        result = packer.unpack_from(data, offset)[0]
        offset += width
        arrays: dict[str, tuple[int, ...]] = {}
        for name in _array_names(function):
            count = len(case.inputs.arrays[name])
            arrays[name] = tuple(packer.unpack_from(data, offset + k * width)[0] for k in range(count))
            offset += count * width
        observations.append((int(result), arrays))
    return observations


def retention_problems(emission: Emission, proof: smt.CheckReport | None) -> tuple[str, ...]:
    """Independent check: emitted guards match the decisions, and each removal cites proved obligations."""
    problems = []
    traps = emission.c.count("__builtin_trap();")
    guards = len(GUARD_LINE.findall(emission.c))
    overflow_traps = len(OVERFLOW_LINE.findall(emission.c))
    entry_traps = len(ENTRY_LINE.findall(emission.c))
    retained = sum(not d.removed for d in emission.decisions)
    retained_overflow = sum(not d.removed for d in emission.overflow)
    rendered = sum(c.rendered is not None for c in emission.requires_checks)
    if emission.mode == "plain":
        if traps:
            problems.append(f"plain emission contains {traps} trap(s)")
        return tuple(problems)
    if emission.mode == "checked" and (
        any(d.removed for d in emission.decisions) or any(d.removed for d in emission.overflow)
    ):
        problems.append("checked emission reports a removed guard or overflow trap")
    if guards != retained:
        problems.append(f"{emission.mode} emission has {guards} guard(s) but {retained} retained decision(s)")
    if overflow_traps != retained_overflow:
        problems.append(
            f"{emission.mode} emission has {overflow_traps} overflow trap(s) but {retained_overflow} retained "
            "overflow site(s)"
        )
    if entry_traps != rendered or (emission.mode == "checked" and entry_traps):
        problems.append(f"{emission.mode} emission has {entry_traps} entry trap(s) but {rendered} rendered conjunct(s)")
    if traps != guards + overflow_traps + entry_traps:
        problems.append(
            f"{emission.mode} emission has {traps - guards - overflow_traps - entry_traps} trap(s) of no known shape"
        )
    statuses = {r.obligation.id: r for r in proof.results} if proof is not None else {}
    for decision in emission.decisions:
        if not decision.removed:
            continue
        if proof is None or not decision.obligation_ids:
            problems.append(f"guard at {decision.location} removed without a proof report or citation")
            continue
        for identifier in decision.obligation_ids:
            result = statuses.get(identifier)
            if result is None or result.status != "proved" or not result.dependencies_satisfied:
                problems.append(
                    f"guard at {decision.location} removed but {identifier} is not proved with dependencies"
                )
            if f"// proved: obligation {identifier}" not in emission.c:
                problems.append(f"guard at {decision.location} removed without citing {identifier}")
    return tuple(problems)


def _clang_version_matches(compiler: str, target: Target) -> bool:
    return read_target(compiler) == target


def _failure(
    executed: ProcessOutcome | None, compiled: ProcessOutcome, observed: int, index: int
) -> FailureKind | None:
    if compiled.status != "ok":
        return "compile"
    assert executed is not None
    if executed.status == "timeout":
        return "timeout"
    if index < observed:
        return None
    if executed.status == "crash":
        return "crash" if index == observed else "not-run"
    if executed.status == "error":
        return "error" if index == observed else "not-run"
    return "truncated"


def run_kernel(
    program: ir.Program,
    profile: generate.Profile,
    *,
    compiler: str,
    target: Target,
    seed: int,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    runner: Runner = invoke,
    generated: generate.Generated | None = None,
    process_timeout_s: float = PROCESS_TIMEOUT_S,
    fuel: int = interpret.DEFAULT_FUEL,
) -> KernelResult:
    """One kernel: generation, proof, three emissions, retention check, compile, run, compare."""
    function = generate.bind(profile, program)
    generated = (
        generated if generated is not None else generate.generate(profile, program, target, seed=seed, fuel=fuel)
    )
    if not _clang_version_matches(compiler, target):
        raise ValueError("selected compiler does not match the target used for generation")
    proof = smt.check(program, target, timeout_ms=timeout_ms)
    emissions = {
        "plain": emit(program, target, mode="plain"),
        "checked": emit(program, target, mode="checked"),
        "verified": emit(program, target, mode="verified", proof=proof),
    }
    sources = {mode: e.c for mode, e in emissions.items()}
    sources["driver"] = driver_source(function, target)
    builds = []
    observations: dict[str, list[tuple[int, dict[str, tuple[int, ...]]]]] = {}
    executable = resolve_compiler(compiler)
    with TemporaryDirectory(prefix="pfc-eval-diff-") as temporary:
        directory = Path(temporary)
        driver = directory / "driver.c"
        driver.write_text(sources["driver"], encoding="utf-8")
        cases_file = directory / "cases.bin"
        cases_file.write_bytes(encode_cases(generated.cases, function, target))
        for mode in MODES:
            emission = emissions[mode]
            source = directory / f"{mode}.c"
            source.write_text(emission.c, encoding="utf-8")
            binary = directory / mode
            output = directory / f"{mode}.out"
            compiled = runner(
                (executable, *C_FLAGS, str(source), str(driver), "-o", str(binary)), directory, process_timeout_s
            )
            executed = None
            observed: list[tuple[int, dict[str, tuple[int, ...]]]] = []
            if compiled.status == "ok":
                executed = runner((str(binary), str(cases_file), str(output)), directory, process_timeout_s)
                data = output.read_bytes() if output.exists() else b""
                observed = (
                    decode_observations(data, generated.cases, function, target) if executed.status != "timeout" else []
                )
            observations[mode] = observed
            retention = retention_problems(emission, proof if mode == "verified" else None)
            digest = hashlib.sha256(emission.c.encode("utf-8")).hexdigest()
            builds.append(BuildResult(mode, compiled, executed, len(observed), retention, digest))
    results = []
    for index, case in enumerate(generated.cases):
        failure: FailureKind | None = None
        for build in builds:
            kind = _failure(build.executed, build.compiled, build.observed_cases, index)
            if kind is not None:
                failure = kind
                break
        if failure is None:
            reference = (case.expected.result, {n: case.expected.arrays[n] for n in _array_names(function)})
            if any(observations[mode][index] != reference for mode in MODES):
                failure = "mismatch"
        results.append(CaseResult(case, failure is None, failure))
    failures = sum(not r.agreement for r in results)
    retention_failures = [f"{b.mode}: {p}" for b in builds for p in b.retention]
    problem = None
    if retention_failures:
        problem = "retention check failed: " + "; ".join(retention_failures)
    elif any(b.compiled.status != "ok" for b in builds):
        problem = "compilation failed: " + "; ".join(
            f"{b.mode}: {b.compiled.message}" for b in builds if b.compiled.status != "ok"
        )
    elif failures:
        problem = f"{failures} case(s) without agreement"
    passed = problem is None
    return KernelResult(
        profile.kernel,
        generated,
        proof.required_passed,
        tuple(builds),
        tuple(results),
        failures,
        passed,
        problem,
        sources,
        len(emissions["checked"].decisions),
        sum(d.removed for d in emissions["verified"].decisions),
    )


def rows(result: KernelResult) -> list[dict[str, object]]:
    """Tidy rows: one per drawn candidate (accepted or rejected), in draw-independent id order."""
    table: list[dict[str, object]] = []
    for item in result.cases:
        table.append(
            {
                "kernel": result.kernel,
                "case_id": item.case.identifier,
                "boundary": item.case.boundary,
                "accepted": True,
                "rejected_reason": None,
                "total_length": sum(len(v) for v in item.case.inputs.arrays.values()),
                "agreement": item.agreement,
                "failure_kind": item.failure_kind,
                "annotation_violations": len(item.case.expected.annotation_violations),
                "guards_total": result.guards_total,
                "guards_removed": result.guards_removed,
            }
        )
    for rejected in result.generated.rejected:
        table.append(
            {
                "kernel": result.kernel,
                "case_id": rejected.identifier,
                "boundary": False,
                "accepted": False,
                "rejected_reason": rejected.kind,
                "total_length": rejected.total_length,
                "agreement": None,
                "failure_kind": None,
                "annotation_violations": None,
                "guards_total": result.guards_total,
                "guards_removed": result.guards_removed,
            }
        )
    return table


def summarize(results: Sequence[KernelResult]) -> dict[str, object]:
    kernels: dict[str, object] = {}
    for result in results:
        boundary = sum(c.case.boundary for c in result.cases)
        seeded = len(result.cases) - boundary
        seeded_failures = sum(not c.agreement for c in result.cases if not c.case.boundary)
        kernels[result.kernel] = {
            "accepted": len(result.cases),
            "boundary": boundary,
            "seeded": seeded,
            "drawn": result.generated.drawn,
            "rejections": dict(sorted(result.generated.rejections.items())),
            "failures": result.failures,
            "seeded_failures": seeded_failures,
            "seeded_upper_bound": stats.binomial_upper_bound(seeded_failures, seeded) if seeded else None,
            "rule_of_three": stats.rule_of_three(seeded) if seeded else None,
            "proof_required_passed": result.proof_required_passed,
            "retention": {b.mode: list(b.retention) for b in result.builds},
            "passed": result.passed,
            "problem": result.problem,
        }
    return {"experiment": "differential", "kernels": kernels, "passed": all(r.passed for r in results)}


def render(summary: Mapping[str, object]) -> str:
    """Table II rendering; `summary.json` and `observations.jsonl` are the data, this is not."""
    lines = [
        "| kernel | accepted | boundary | seeded | drawn | rejected by reason | failures "
        "| 95% one-sided bound (seeded) | 3/n | proofs | passed |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |",
    ]
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    for name, entry in kernels.items():
        rejected = ", ".join(f"{k}={v}" for k, v in entry["rejections"].items()) or "none"
        bound = entry["seeded_upper_bound"]
        bound_text = f"{100 * bound:.3f}%" if bound is not None else "n/a"
        three = entry["rule_of_three"]
        three_text = f"{100 * three:.3f}%" if three is not None else "n/a"
        proofs = "passed" if entry["proof_required_passed"] else "not passed"
        lines.append(
            f"| {name} | {entry['accepted']} | {entry['boundary']} | {entry['seeded']} | {entry['drawn']} | "
            f"{rejected} | "
            f"{entry['failures']} | {bound_text} | {three_text} | {proofs} | {'yes' if entry['passed'] else 'no'} |"
        )
    lines.append("")
    lines.append(
        "The bound is the exact one-sided 95% binomial upper bound on the per-input disagreement probability relative "
        "to the documented generator, over seeded cases only; it is not a bound over all inputs or a proof."
    )
    return "\n".join(lines) + "\n"


def _load_profiles(
    sources: Sequence[Path], profiles_dir: Path, cpp: str
) -> list[tuple[str, ir.Program, generate.Profile, Path]]:
    loaded = []
    for source in sources:
        name = source.parent.name
        program = lower(parse_source(source, cpp=cpp))
        path = profiles_dir / f"{name}.json"
        if not path.is_file():
            raise ValueError(f"no profile for kernel {name!r} in {profiles_dir}")
        profile = (
            generate.committed_profile(name, path)
            if path.resolve().parent == generate.PROFILES_DIR.resolve()
            else generate.load_profile(path)
        )
        generate.bind(profile, program)
        loaded.append((name, program, profile, path))
    return loaded


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = 0,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    profiles_dir: Path = generate.PROFILES_DIR,
    dry_run: bool = False,
    runner: Runner = invoke,
    process_timeout_s: float = PROCESS_TIMEOUT_S,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """CLI-level experiment: one sealed evidence directory for all selected kernels."""
    sources = workflow.discover(tuple(directories))
    loaded = _load_profiles(sources, profiles_dir, cpp)
    if dry_run:
        for name, _program, profile, path in loaded:
            lengths = ", ".join(f"{a}:[{s.min_length}, {s.max_length}]" for a, s in profile.arrays.items())
            digest = generate.file_digest(path)[:16]
            print(
                f"{name}: profile {digest}; {profile.cases} cases; {len(profile.boundary)} boundary; lengths {lengths}"
            )
        print(f"Preview: seed {seed}, solver timeout {timeout_ms} ms, process timeout {process_timeout_s} s.")
        print("No files were written.")
        return True
    run = campaign.start(
        "differential",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "timeout_ms": timeout_ms,
            "process_timeout_s": process_timeout_s,
            "c_flags": list(C_FLAGS),
            "driver_protocol": DRIVER_PROTOCOL,
            "interpreter_fuel": interpret.DEFAULT_FUEL,
            "kernels": [name for name, *_ in loaded],
            "profile_digests": {name: generate.file_digest(path) for name, _p, _f, path in loaded},
            "program_sha256": {name: profile.program_sha256 for name, _p, profile, _path in loaded},
            "profile_stress": {name: profile.stress for name, _p, profile, _path in loaded},
        },
        schema=SCHEMA,
    )
    results = []
    for name, program, profile, _path in loaded:
        if progress:
            progress(f"differential: {name}")
        result = run_kernel(
            program,
            profile,
            compiler=compiler,
            target=run.target,
            seed=seed,
            timeout_ms=timeout_ms,
            runner=runner,
            process_timeout_s=process_timeout_s,
        )
        results.append(result)
        for row in rows(result):
            run.evidence.record("case", name, str(row["case_id"]), row)
        for build in result.builds:
            run.evidence.record("build", name, build.mode, asdict(build))
        for mode, text in result.sources.items():
            run.evidence.write_text(f"sources/{name}-{mode}.c", text)
    summary = summarize(results)
    campaign.finish(run, summary, render(summary), derive_rows)
    return bool(summary["passed"])


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    """Tidy table from raw case records only; the build records are provenance for the experiment."""
    return [record.data for record in records if record.unit == "case"]


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
