"""Runtime effect, reported not claimed (roadmap E11, design §6, Table V and Figure 5).

Per kernel: plain, checked, verified and the three fact ablations, all built by the frozen `build_source`
with the existing `BENCH_FLAGS` and the reviewed driver. Three warm-ups, then three blocks of ten runs with
the variants interleaved inside every run (A B C, A B C, …), each run timed by this module with a
monotonic clock and stamped so the interleaving is verifiable from the evidence. Instruction counts come
from `perf stat` when present, otherwise from `valgrind --tool=cachegrind`, otherwise they are recorded
as unavailable; the tool is named in every row. Ratios are geometric (log scale), paired by (block, run),
with a seeded bootstrap; absolute medians always stand beside them. The CPU governor is recorded and any
value other than `performance` labels the whole run exploratory. Nothing here supports a faster-than-C claim.
"""

import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp, log
from pathlib import Path
from statistics import median, quantiles

from pfc.build import build_source
from pfc.config import BENCH_FLAGS, DEFAULT_CPP, REMARKS_TIMEOUT_SECONDS, SOLVER_TIMEOUT_MS
from pfc.emit import OVERFLOW_LINE, Mode
from pfc.environment import resolve_compiler
from pfc.facts import ALL_FACTS, FactName
from pfc.workflow import discover
from pfc_eval import campaign, evidence, stats

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = 3
RUNS = 10
WARMUPS = 3
ELEMENTS = 1 << 24
REPETITIONS = 50
RUN_TIMEOUT_S = 600
COUNTER_RUNS = 2  # repeatability check of the simulated/retired instruction count
GOVERNOR_PATH = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")


@dataclass(frozen=True)
class Variant:
    label: str
    mode: Mode
    facts: frozenset[FactName]
    transform: str | None = None  # evaluation-only rewrite of the emitted C before compiling (never a compiler mode)


@dataclass(frozen=True)
class Step:
    block: int
    run: int
    kernel: str
    variant: str


VARIANTS: tuple[Variant, ...] = (
    Variant("plain", "plain", ALL_FACTS),
    Variant("checked", "checked", ALL_FACTS),
    Variant("verified", "verified", ALL_FACTS),
    Variant("without-delete", "verified", ALL_FACTS - {"delete"}),
    Variant("without-restrict", "verified", ALL_FACTS - {"restrict"}),
    Variant("without-assume", "verified", ALL_FACTS - {"assume"}),
)
# AMENDMENTS 39 (post-outcome, exploratory): verified C with every retained overflow trap rewritten as the plain
# operation, to isolate what the traps cost. Opt-in (`--trap-ablation`); the transformed C is not claimed safe.
STRIP_OVERFLOW_TRAPS = "strip-overflow-traps"
TRAP_ABLATION = Variant("without-overflow-traps", "verified", ALL_FACTS, STRIP_OVERFLOW_TRAPS)
_OPERATORS = {"add": "+", "sub": "-", "mul": "*"}


def strip_overflow_traps(text: str) -> str:
    """Rewrite `if (__builtin_OP_overflow(a, b, &t)) __builtin_trap();` as `t = a OP b;`, keeping every other line.

    Operands of the emitter are names or literals; anything else is refused rather than guessed.
    """

    def plain(match: re.Match[str]) -> str:
        parts = [part.strip() for part in match.group(2).split(",")]
        if len(parts) != 3 or not parts[2].startswith("&") or any("(" in part or ")" in part for part in parts):
            raise ValueError(f"cannot parse the overflow trap operands: {match.group(0).strip()}")
        indent = match.group(0)[: len(match.group(0)) - len(match.group(0).lstrip())]
        return f"{indent}{parts[2][1:]} = {parts[0]} {_OPERATORS[match.group(1)]} {parts[1]};"

    return OVERFLOW_LINE.sub(plain, text)


SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("variant", "string", allowed=(*(v.label for v in VARIANTS), TRAP_ABLATION.label)),
        evidence.Column("mode", "string", allowed=("plain", "checked", "verified")),
        evidence.Column("facts", "string"),
        evidence.Column("block", "integer"),
        evidence.Column("run", "integer"),
        evidence.Column("warmup", "boolean"),
        evidence.Column("wall_ms", "number"),
        evidence.Column("exit_code", "integer"),
        evidence.Column("started", "string"),
        evidence.Column("instructions", "integer", nullable=True),
        evidence.Column("counter_tool", "string", allowed=("perf", "cachegrind", "unavailable")),
        evidence.Column("vectorized", "boolean"),
        evidence.Column("elements", "integer"),
        evidence.Column("repetitions", "integer"),
    )
)


# --- schedule and environment ------------------------------------------------------------------------


def schedule(
    kernels: Sequence[str], variants: Sequence[str], *, blocks: int = BLOCKS, runs: int = RUNS
) -> tuple[Step, ...]:
    """Blocks of runs; inside each run every kernel's variants are executed back to back (A B C, A B C, …)."""
    return tuple(
        Step(block, run, kernel, variant)
        for block in range(1, blocks + 1)
        for run in range(1, runs + 1)
        for kernel in kernels
        for variant in variants
    )


def governor(read: Callable[[Path], str] | None = None) -> str:
    reader = read or (lambda path: path.read_text(encoding="utf-8"))
    try:
        return reader(GOVERNOR_PATH).strip() or "unavailable"
    except OSError:
        return "unavailable"


def label(governor_name: str, counter: str) -> str:
    """Controlled only with the performance governor and a second measure (an instruction counter)."""
    return "controlled" if governor_name == "performance" and counter != "unavailable" else "exploratory"


def pinned(program: Sequence[str], *, cpu: int | None, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Wrap the program in `taskset -c CPU` when a CPU is requested; refuse when taskset is absent."""
    if cpu is None:
        return list(program)
    if which("taskset") is None:
        raise ValueError("--cpu requested but taskset is not installed")
    return ["taskset", "-c", str(cpu), *program]


def counter_tool(*, which: Callable[[str], str | None] = shutil.which) -> str:
    if which("perf"):
        return "perf"
    if which("valgrind"):
        return "cachegrind"
    return "unavailable"


def counter_command(tool: str, program: Sequence[str]) -> list[str]:
    if tool == "perf":
        return ["perf", "stat", "-x,", "-e", "instructions", *program]
    if tool == "cachegrind":
        return ["valgrind", "--tool=cachegrind", "--cachegrind-out-file=/dev/null", *program]
    raise ValueError("no instruction counter is available")


def parse_perf(text: str) -> int | None:
    for line in text.splitlines():
        fields = line.split(",")
        if len(fields) >= 3 and fields[2].strip() == "instructions" and fields[0].strip().isdigit():
            return int(fields[0].strip())
    return None


def parse_cachegrind(text: str) -> int | None:
    match = re.search(r"I\s+refs:\s+([\d,]+)", text)
    return int(match.group(1).replace(",", "")) if match else None


# --- measurement --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Timed:
    wall_ms: float
    exit_code: int
    started: str


def time_run(program: Sequence[str], *, cwd: Path, timeout_s: int = RUN_TIMEOUT_S) -> Timed:
    started = datetime.now(UTC).isoformat(timespec="microseconds")
    begin = time.perf_counter_ns()
    try:
        code = subprocess.run(list(program), cwd=cwd, capture_output=True, timeout=timeout_s, check=False).returncode
    except subprocess.TimeoutExpired:
        code = 124
    return Timed((time.perf_counter_ns() - begin) / 1e6, code, started)


def count_instructions(tool: str, program: Sequence[str], *, cwd: Path, timeout_s: int = RUN_TIMEOUT_S) -> int | None:
    if tool == "unavailable":
        return None
    try:
        result = subprocess.run(
            counter_command(tool, program), cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_perf(result.stderr) if tool == "perf" else parse_cachegrind(result.stderr)


def vectorization_remarks(compiler: str, source: Path, directory: Path) -> tuple[str, bool]:
    """Compile once more with the vectorizer remarks; `vectorized` is a categorical witness, not a measure."""
    result = subprocess.run(
        [
            compiler,
            *BENCH_FLAGS,
            "-Rpass=loop-vectorize",
            "-Rpass-missed=loop-vectorize",
            "-c",
            str(source),
            "-o",
            "/dev/null",
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=REMARKS_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"vectorization diagnostic build failed for {source}")
    return result.stderr, "vectorized loop" in result.stderr


# --- analysis -----------------------------------------------------------------------------------------


Pair = tuple[int, int]


def ratio(
    numerators: Mapping[Pair, float], denominators: Mapping[Pair, float], *, seed: int, replicates: int = 2000
) -> dict[str, object]:
    """Geometric ratio of paired (block, run) measurements with a seeded percentile bootstrap over pairs."""
    if set(numerators) != set(denominators) or not numerators:
        raise ValueError("ratios need the same (block, run) pairs on both sides")
    logs = {
        f"{block}-{run}": [log(numerators[(block, run)] / denominators[(block, run)])]
        for block, run in sorted(numerators)
    }
    bootstrap = stats.cluster_bootstrap(logs, stats.mean, replicates=replicates, seed=seed)
    return {
        "estimate": exp(bootstrap.estimate),
        "low": exp(bootstrap.interval.low),
        "high": exp(bootstrap.interval.high),
        "pairs": len(logs),
        "replicates": replicates,
        "seed": seed,
    }


def _iqr(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    quartiles = quantiles(values, n=4, method="inclusive")
    return quartiles[2] - quartiles[0]


def summarize(rows: Sequence[Mapping[str, object]], *, seed: int, governor: str, cpu: int | None) -> dict[str, object]:
    timed = [r for r in rows if not r["warmup"]]
    kernels: dict[str, dict[str, object]] = {}
    tools = {str(r["counter_tool"]) for r in rows} or {"unavailable"}
    for kernel in sorted({str(r["kernel"]) for r in timed}):
        mine = [r for r in timed if r["kernel"] == kernel]
        by_variant: dict[str, dict[Pair, float]] = {}
        counts: dict[str, dict[Pair, float]] = {}
        vectorized: dict[str, bool] = {}
        for r in mine:
            variant = str(r["variant"])
            pair = (int(str(r["block"])), int(str(r["run"])))
            by_variant.setdefault(variant, {})[pair] = float(str(r["wall_ms"]))
            if r["instructions"] is not None:
                counts.setdefault(variant, {})[pair] = float(str(r["instructions"]))
            vectorized[variant] = bool(r["vectorized"])
        contrasts: tuple[tuple[str, str], ...] = (("checked", "plain"), ("verified", "plain"), ("verified", "checked"))
        contrasts += tuple((v, "verified") for v in by_variant if v.startswith("without-"))
        contrasts += ((TRAP_ABLATION.label, "plain"),)
        kernels[kernel] = {
            "medians_ms": {v: median(list(samples.values())) for v, samples in sorted(by_variant.items())},
            "iqr_ms": {v: _iqr(list(samples.values())) for v, samples in sorted(by_variant.items())},
            "runs_per_variant": {v: len(samples) for v, samples in sorted(by_variant.items())},
            "ratios": {
                f"{a}/{b}": ratio(by_variant[a], by_variant[b], seed=seed)
                for a, b in contrasts
                if a in by_variant and b in by_variant
            },
            "median_instructions": {v: median(list(samples.values())) for v, samples in sorted(counts.items())},
            "instruction_ratios": {
                f"{a}/{b}": ratio(counts[a], counts[b], seed=seed) for a, b in contrasts if a in counts and b in counts
            },
            "vectorized": vectorized,
            "failed_runs": sum(int(str(r["exit_code"])) != 0 for r in mine),
        }
    tool = "unavailable" if tools == {"unavailable"} else ",".join(sorted(tools - {"unavailable"}))
    return {
        "governor": governor,
        "label": label(governor, tool),
        "counter_tool": tool,
        "cpu": cpu,
        "timed_runs": len(timed),
        "warmups": len(rows) - len(timed),
        "kernels": kernels,
        "passed": all(int(str(r["exit_code"])) == 0 for r in rows),
    }


def _fmt(value: Mapping[str, object]) -> str:
    return f"{float(str(value['estimate'])):.3f} [{float(str(value['low'])):.3f}, {float(str(value['high'])):.3f}]"


def render_table(summary: Mapping[str, object]) -> str:
    kernels: Mapping[str, Mapping[str, object]] = summary["kernels"]  # type: ignore[assignment]
    lines = [
        "# Runtime effect (design §6; reported, not claimed)",
        "",
        f"Governor: {summary['governor']}; pinned CPU: {summary['cpu']} -> run label **{summary['label']}**; "
        f"instruction counter: {summary['counter_tool']}; timed runs: {summary['timed_runs']}; "
        f"warm-ups: {summary['warmups']}.",
        "",
        "| kernel | variant | median ms | IQR ms | vectorized | median instructions |",
        "| --- | --- | ---: | ---: | --- | ---: |",
    ]
    for name, data in kernels.items():
        medians: Mapping[str, float] = data["medians_ms"]  # type: ignore[assignment]
        iqr: Mapping[str, float] = data["iqr_ms"]  # type: ignore[assignment]
        vectorized: Mapping[str, bool] = data["vectorized"]  # type: ignore[assignment]
        instructions: Mapping[str, float] = data["median_instructions"]  # type: ignore[assignment]
        for variant, value in medians.items():
            count = instructions.get(variant)
            lines.append(
                f"| {name} | {variant} | {value:.3f} | {iqr[variant]:.3f} | {vectorized.get(variant)} | "
                f"{'n/a' if count is None else f'{count:.0f}'} |"
            )
    lines += ["", "| kernel | ratio | time (geometric, 95% bootstrap) | instructions |", "| --- | --- | --- | --- |"]
    for name, data in kernels.items():
        ratios: Mapping[str, Mapping[str, object]] = data["ratios"]  # type: ignore[assignment]
        count_ratios: Mapping[str, Mapping[str, object]] = data["instruction_ratios"]  # type: ignore[assignment]
        for contrast, estimate in ratios.items():
            counted = _fmt(count_ratios[contrast]) if contrast in count_ratios else "n/a"
            lines.append(f"| {name} | {contrast} | {_fmt(estimate)} | {counted} |")
    lines.append("")
    return "\n".join(lines)


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "run"]


# --- campaign -----------------------------------------------------------------------------------------


def _facts_label(facts: frozenset[FactName]) -> str:
    return ",".join(sorted(facts))


def run(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = 0,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    elements: int = ELEMENTS,
    repetitions: int = REPETITIONS,
    blocks: int = BLOCKS,
    runs: int = RUNS,
    warmups: int = WARMUPS,
    variants: Sequence[Variant] = VARIANTS,
    counter: str | None = None,
    cpu: int | None = None,
    trap_ablation: bool = False,
) -> bool:
    """Build every variant, warm up, run the interleaved schedule, count instructions, seal."""
    if trap_ablation and TRAP_ABLATION not in variants:
        variants = (*variants, TRAP_ABLATION)
    if min(elements, repetitions, blocks, runs) < 1 or warmups < 0:
        raise ValueError("elements, repetitions, blocks and runs must be positive; warmups non-negative")
    sources = discover(tuple(directories))
    kernels = [source.parent.name for source in sources]
    for source in sources:
        if not (source.parent / "main.c").is_file():
            raise ValueError(f"{source.parent} has no main.c driver")
    tool = counter if counter is not None else counter_tool()
    current = governor()
    pinned([], cpu=cpu)  # fail before building when taskset is missing
    steps = schedule(kernels, [v.label for v in variants], blocks=blocks, runs=runs)
    if dry_run:
        print(f"governor {current}; pinned CPU {cpu} -> {label(current, tool)}; instruction counter: {tool}")
        print(f"{len(kernels)} kernel(s) x {len(variants)} variants; {warmups} warm-ups each; then:")
        for step in steps:
            print(f"block {step.block} run {step.run}: {step.kernel} {step.variant}")
        print(f"Preview: {len(steps)} timed runs with {elements} elements x {repetitions} repetitions; seed {seed}.")
        print("No files were written.")
        return True
    compiler = resolve_compiler(compiler)
    campaign_run = campaign.start(
        "runtime",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "flags": list(BENCH_FLAGS),
            "variants": [
                {"label": v.label, "mode": v.mode, "facts": _facts_label(v.facts), "transform": v.transform}
                for v in variants
            ],
            "blocks": blocks,
            "runs": runs,
            "warmups": warmups,
            "elements": elements,
            "repetitions": repetitions,
            "governor": current,
            "label": label(current, tool),
            "counter_tool": tool,
            "cpu": cpu,
            "counter_runs": COUNTER_RUNS,
            "timeout_ms": timeout_ms,
            "kernels": kernels,
        },
        schema=SCHEMA,
    )
    work = Path(tempfile.mkdtemp(prefix="pfc-runtime-"))
    rows: list[dict[str, object]] = []
    try:
        programs: dict[tuple[str, str], list[str]] = {}
        vectorized: dict[tuple[str, str], bool] = {}
        for source, kernel in zip(sources, kernels, strict=True):
            for variant in variants:
                if progress:
                    progress(f"runtime build: {kernel} {variant.label}")
                directory = work / kernel / variant.label
                built = build_source(
                    source,
                    directory,
                    mode=variant.mode,
                    compiler=compiler,
                    cpp=cpp,
                    facts=variant.facts,
                    flags=BENCH_FLAGS,
                    driver=source.parent / "main.c",
                    timeout_ms=timeout_ms,
                )
                if not built.passed:
                    raise ValueError(f"build failed for {kernel} {variant.label}; inspect builds/ in the evidence")
                if variant.transform == STRIP_OVERFLOW_TRAPS:
                    emitted = directory / "kernel.c"
                    emitted.write_text(strip_overflow_traps(emitted.read_text(encoding="utf-8")), encoding="utf-8")
                    # Same compiler, flags, driver and argument order as the frozen build_source.
                    recompile = [
                        resolve_compiler(compiler),
                        *BENCH_FLAGS,
                        str(emitted.resolve()),
                        str((source.parent / "main.c").resolve()),
                        "-o",
                        str((directory / "program").resolve()),
                    ]
                    compiled = subprocess.run(recompile, cwd=directory, capture_output=True, text=True, check=False)
                    if compiled.returncode != 0:
                        raise ValueError(f"trap ablation of {kernel} does not compile: {compiled.stderr[:400]}")
                remarks, flag = vectorization_remarks(compiler, directory / "kernel.c", directory)
                for name in ("kernel.c", "build.json"):
                    campaign_run.evidence.write_text(
                        f"builds/{kernel}/{variant.label}/{name}", (directory / name).read_text(encoding="utf-8")
                    )
                campaign_run.evidence.write_text(f"builds/{kernel}/{variant.label}/vectorization.txt", remarks)
                programs[(kernel, variant.label)] = [str(directory / "program"), str(elements), str(repetitions)]
                vectorized[(kernel, variant.label)] = flag
        instructions: dict[tuple[str, str], int | None] = {}
        for key, program in programs.items():
            if progress:
                progress(f"runtime counters ({tool}): {key[0]} {key[1]}")
            samples = [count_instructions(tool, pinned(program, cpu=cpu), cwd=work) for _ in range(COUNTER_RUNS)]
            instructions[key] = samples[0] if samples and len(set(samples)) == 1 else None
            campaign_run.evidence.write_text(
                f"builds/{key[0]}/{key[1]}/instructions.json",
                json.dumps({"tool": tool, "samples": samples, "repeatable": len(set(samples)) == 1}) + "\n",
            )
        by_variant = {v.label: v for v in variants}

        def record(step: Step, timed: Timed, *, warmup: bool) -> None:
            variant = by_variant[step.variant]
            row: dict[str, object] = {
                "kernel": step.kernel,
                "variant": step.variant,
                "mode": variant.mode,
                "facts": _facts_label(variant.facts),
                "block": step.block,
                "run": step.run,
                "warmup": warmup,
                "wall_ms": round(timed.wall_ms, 3),
                "exit_code": timed.exit_code,
                "started": timed.started,
                "instructions": instructions[(step.kernel, step.variant)],
                "counter_tool": tool,
                "vectorized": vectorized[(step.kernel, step.variant)],
                "elements": elements,
                "repetitions": repetitions,
            }
            identifier = f"{step.variant}.b{step.block}.r{step.run}" + (".warmup" if warmup else "")
            campaign_run.evidence.record("run", step.kernel, identifier, row)
            rows.append(row)

        for kernel in kernels:
            for variant in variants:
                for number in range(1, warmups + 1):
                    if progress:
                        progress(f"runtime warm-up {number}/{warmups}: {kernel} {variant.label}")
                    step = Step(0, number, kernel, variant.label)
                    record(step, time_run(pinned(programs[(kernel, variant.label)], cpu=cpu), cwd=work), warmup=True)
        for step in steps:
            if progress and step.run == 1 and step.variant == variants[0].label:
                progress(f"runtime block {step.block}/{blocks}: {step.kernel}")
            record(step, time_run(pinned(programs[(step.kernel, step.variant)], cpu=cpu), cwd=work), warmup=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = summarize(rows, seed=seed, governor=current, cpu=cpu)
    campaign.finish(campaign_run, summary, render_table(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run)
