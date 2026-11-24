"""Hyperfine boundary and descriptive runtime tables; correctness is checked first."""

import csv
import json
import math
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import median, quantiles

from pfc import report, workflow
from pfc.build import build_source, require_new_directory
from pfc.config import BENCH_FLAGS, BENCH_TIMEOUT_SECONDS, DEFAULT_CPP, REMARKS_TIMEOUT_SECONDS, SOLVER_TIMEOUT_MS
from pfc.corpus import profile
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.difftest import invoke
from pfc.drivers import benchmark_driver
from pfc.emit import Mode
from pfc.environment import resolve_compiler
from pfc.facts import ALL_FACTS, FactName
from pfc.lower import lower
from pfc.parse import parse_source


@dataclass(frozen=True)
class Timing:
    kernel: str
    variant: str
    median: float
    iqr: float
    samples: tuple[float, ...]


def statistics(samples: object) -> tuple[float, float]:
    if (
        not isinstance(samples, list)
        or len(samples) != 10
        or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in samples)
    ):
        raise ValueError("expected ten finite nonnegative runtime samples")
    quartiles = quantiles(samples, n=4, method="inclusive")
    return float(median(samples)), float(quartiles[2] - quartiles[0])


def measure(arguments: tuple[str, ...], directory: Path) -> None:
    result = subprocess.run(arguments, cwd=directory, capture_output=True, text=True, timeout=BENCH_TIMEOUT_SECONDS)
    (directory / "hyperfine-output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError(f"hyperfine failed with exit {result.returncode}; inspect saved output")


def render(rows: tuple[Timing, ...]) -> str:
    lines = ["| kernel | variant | median (s) | IQR (s) |", "| --- | --- | ---: | ---: |"]
    lines.extend(f"| {r.kernel} | {r.variant} | {r.median:.9f} | {r.iqr:.9f} |" for r in rows)
    return "\n".join(lines) + "\n"


def load(directory: Path) -> tuple[Timing, ...]:
    # Call only after report.load has validated the enclosing manifest.
    data = report._read(directory / "benchmark.json")
    if not isinstance(data, list) or not data:
        raise ValueError("invalid benchmark rows")
    rows = []
    for item in data:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("kernel"), str)
            or not isinstance(item.get("variant"), str)
            or item.get("kernel") not in {"sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search"}
            or item.get("variant")
            not in {"plain", "checked", "verified", "without-delete", "without-restrict", "without-assume"}
        ):
            raise ValueError("invalid benchmark identity")
        center, spread = statistics(item.get("samples"))
        if item.get("median") != center or item.get("iqr") != spread:
            raise ValueError("inconsistent benchmark statistics")
        rows.append(Timing(item["kernel"], item["variant"], center, spread, tuple(item["samples"])))
    if len({(r.kernel, r.variant) for r in rows}) != len(rows):
        raise ValueError("duplicate benchmark rows")
    return tuple(rows)


def run(
    directories: tuple[Path, ...],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    hyperfine: str = "hyperfine",
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    timer: Callable[[tuple[str, ...], Path], None] = measure,
    progress: Callable[[str], None] | None = None,
) -> tuple[Timing, ...]:
    require_new_directory(output)
    sources = workflow.discover(directories)
    for source in sources:
        program = lower(parse_source(source, cpp=cpp))
        if profile(program) != source.parent.name:
            raise ValueError("benchmark directory must use the reviewed kernel name")
        if (source.parent / "main.c").read_text(encoding="utf-8") != benchmark_driver(program):
            raise ValueError("benchmark requires the reviewed driver")
    if timer is measure and shutil.which(hyperfine) is None:
        raise FrontendError("bench-tool", "install hyperfine or supply --hyperfine PATH", SourceLocation("<benchmark>"))
    # Resolve paths in the invocation directory before any subprocess changes cwd.
    compiler = resolve_compiler(compiler)
    if timer is measure:
        hyperfine = str(Path(shutil.which(hyperfine) or hyperfine).resolve())
    output.mkdir(parents=True, exist_ok=False)
    soundness = workflow.test(
        directories, compiler=compiler, cpp=cpp, output=output / "soundness", timeout_ms=timeout_ms
    )
    if not soundness.passed:
        report.seal(output, soundness)
        raise ValueError("soundness failed; timings were not run")
    governor = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    metadata = {
        "governor": governor.read_text(encoding="utf-8").strip() if governor.exists() else "unavailable",
        "perf_instructions": "not-run: perf tool unavailable or not configured",
        "warmups": 3,
        "runs": 10,
        "elements": 1 << 24,
        "repetitions": 50,
        "hyperfine": (
            invoke((hyperfine, "--version"), output).stdout.strip() if timer is measure else "injected test timer"
        ),
    }
    (output / "protocol.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    variants: list[tuple[str, Mode, frozenset[FactName]]] = [
        ("plain", "plain", ALL_FACTS),
        ("checked", "checked", ALL_FACTS),
        ("verified", "verified", ALL_FACTS),
        *((f"without-{fact}", "verified", ALL_FACTS - {fact}) for fact in ("delete", "restrict", "assume")),
    ]
    rows = []
    for source in sources:
        for label, mode, facts in variants:
            if progress:
                progress(f"{source.parent.name}: {label}")
            directory = output / source.parent.name / label
            built = build_source(
                source,
                directory,
                mode=mode,
                compiler=compiler,
                cpp=cpp,
                facts=facts,
                flags=BENCH_FLAGS,
                driver=source.parent / "main.c",
                timeout_ms=timeout_ms,
            )
            if not built.passed:
                raise ValueError("benchmark build failed; inspect build.json")
            remarks = subprocess.run(
                (
                    compiler,
                    *BENCH_FLAGS,
                    "-Rpass=loop-vectorize",
                    "-Rpass-missed=loop-vectorize",
                    "-c",
                    str((directory / "kernel.c").resolve()),
                    "-o",
                    str((directory / "remarks.o").resolve()),
                ),
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=REMARKS_TIMEOUT_SECONDS,
            )
            (directory / "vectorization.txt").write_text(remarks.stderr, encoding="utf-8")
            if remarks.returncode:
                raise ValueError("vectorization diagnostic build failed")
            arguments = (
                hyperfine,
                "-w",
                "3",
                "-r",
                "10",
                "--shell=none",
                "--export-json",
                str((directory / "timing.json").resolve()),
                shlex.join([str((directory / "program").resolve())]),
            )
            timer(arguments, directory)
            data = report._read(directory / "timing.json")
            if not isinstance(data, dict) or not isinstance(data.get("results"), list) or len(data["results"]) != 1:
                raise ValueError("invalid hyperfine result")
            record = data["results"][0]
            if not isinstance(record, dict):
                raise ValueError("invalid hyperfine result row")
            if record.get("exit_codes") != [0] * 10:
                raise ValueError("benchmark execution failed")
            center, spread = statistics(record.get("times"))
            rows.append(Timing(source.parent.name, label, center, spread, tuple(record["times"])))
    result = tuple(rows)
    report.write_json(output / "benchmark.json", result)
    with (output / "results.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("kernel", "variant", "median_seconds", "iqr_seconds"))
        writer.writerows((r.kernel, r.variant, f"{r.median:.9f}", f"{r.iqr:.9f}") for r in result)
    (output / "tables.md").write_text(report.render(soundness) + "\n" + render(result), encoding="utf-8")
    report.seal(output, soundness)
    return result


def freeze(source: Path, output: Path) -> None:
    """Archive validated research evidence without temporary executables/object files."""
    require_new_directory(output)
    summary = report.load(source)
    load(source)
    manifest = report._read(source / "manifest.json")
    assert isinstance(manifest, dict)
    output.mkdir(parents=True, exist_ok=False)
    for name in manifest["files"]:
        relative = Path(name)
        if name == "report.json" or relative.name == "program" or relative.suffix == ".o":
            continue
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    report.seal(output, summary)
