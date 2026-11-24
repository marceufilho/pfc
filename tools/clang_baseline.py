"""What the C compiler removes by itself.

For each program, the *checked* emission (every guard present, no proof used) is compiled to LLVM IR by the same
clang, and the script counts the bounds and division guards whose trap is still reachable in the optimized code.
To tell the guards apart, a harness-side copy of the checked C calls a distinct external `_Noreturn` function at
each guard site instead of `__builtin_trap()`; overflow traps and everything else are left as emitted. The marked
copy is never a compiler mode and is only compiled to IR, never run. A guard "survives" when the optimized IR
still calls its marker; clang removes a guard only when it can show the condition from the C text alone.

    uv run --locked python tools/clang_baseline.py heldout --output out/clang-baseline-heldout
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pfc import ir  # noqa: E402
from pfc.config import BENCH_FLAGS, C_FLAGS, CLANG_ENV_VAR, CPP_ENV_VAR, DEFAULT_CLANG, DEFAULT_CPP  # noqa: E402
from pfc.emit import GUARD_LINE, emit  # noqa: E402
from pfc.lower import lower  # noqa: E402
from pfc.parse import parse_source  # noqa: E402
from pfc.target import Target  # noqa: E402
from pfc_eval import campaign, evidence, heldout  # noqa: E402

EXPERIMENT = "clang-baseline"
ENTRY = re.compile(r"^\s*if \(!pfc_entry_\d+\) ")
SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("guards_total", "integer"),
        evidence.Column("survive_o2", "integer"),
        evidence.Column("survive_bench", "integer"),
    )
)


def mark(text: str) -> tuple[str, int]:
    """The checked C with guard k trapping through `pfc_guard_k()`; returns the text and the number of guards."""
    count = 0

    def numbered(match: re.Match[str]) -> str:
        nonlocal count
        line = match.group(0)
        if ENTRY.match(line):
            return line
        count += 1
        return line.replace("__builtin_trap();", f"pfc_guard_{count}();")

    body = GUARD_LINE.sub(numbered, text)
    header = "".join(f"_Noreturn void pfc_guard_{index}(void);\n" for index in range(1, count + 1))
    return header + body, count


def surviving(marked: str, count: int, clang: str, flags: Sequence[str]) -> int:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "kernel.c"
        source.write_text(marked, encoding="utf-8")
        done = subprocess.run(
            [clang, *flags, "-S", "-emit-llvm", str(source), "-o", "-"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    if done.returncode != 0:
        raise RuntimeError(f"clang failed on the marked checked C: {done.stderr[-400:]}")
    return sum(bool(re.search(rf"call [^\n]*@pfc_guard_{index}\(", done.stdout)) for index in range(1, count + 1))


def measure(name: str, program: ir.Program, target: Target, clang: str) -> dict[str, object]:
    checked = emit(program, target, mode="checked")
    marked, count = mark(checked.c)
    if count != len(checked.decisions):
        raise RuntimeError(f"{name}: {count} guard lines for {len(checked.decisions)} guard decisions")
    return {
        "kernel": name,
        "guards_total": count,
        "survive_o2": surviving(marked, count, clang, C_FLAGS),
        "survive_bench": surviving(marked, count, clang, BENCH_FLAGS),
    }


def summarize(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    def total(key: str) -> int:
        return sum(int(str(row[key])) for row in rows)

    return {
        "programs": len(rows),
        "guards_total": total("guards_total"),
        "survive_o2": total("survive_o2"),
        "survive_bench": total("survive_bench"),
        "removed_by_clang_o2": total("guards_total") - total("survive_o2"),
        "programs_with_a_guard_removed_o2": sum(row["survive_o2"] != row["guards_total"] for row in rows),
        "flags_o2": list(C_FLAGS),
        "flags_bench": list(BENCH_FLAGS),
    }


def table(summary: Mapping[str, object]) -> str:
    return (
        "# What clang removes from the checked build on its own (post hoc)\n\n"
        f"Programs: {summary['programs']}; guard sites: {summary['guards_total']}.\n\n"
        "| Flags | Guards still reachable | Removed by clang |\n| --- | ---: | ---: |\n"
        f"| {' '.join(C_FLAGS)} | {summary['survive_o2']} | {summary['removed_by_clang_o2']} |\n"
        f"| {' '.join(BENCH_FLAGS)} | {summary['survive_bench']} | "
        f"{int(str(summary['guards_total'])) - int(str(summary['survive_bench']))} |\n"
    )


def derive(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "program"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guards of the checked build that clang removes with no proof.")
    parser.add_argument("directories", nargs="+", type=Path, help="kernel directories or their parents")
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    arguments = parser.parse_args(argv)
    compiler = os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
    cpp = os.environ.get(CPP_ENV_VAR, DEFAULT_CPP)
    heldout.require_intact(arguments.directories)
    paths: list[Path] = []
    for directory in arguments.directories:
        own = directory / "kernel.c"
        paths += [own] if own.is_file() else sorted(directory.glob("*/kernel.c"))
    settings = {
        "kernels": [p.parent.name for p in paths],
        "post_hoc": True,
        "marker": "pfc_guard_K, external _Noreturn",
    }
    run = campaign.start(
        EXPERIMENT, arguments.output, compiler=compiler, cpp=cpp, seed=None, settings=settings, schema=SCHEMA
    )
    rows = []
    for path in paths:
        row = measure(path.parent.name, lower(parse_source(path, cpp=cpp)), run.target, compiler)
        rows.append(row)
        run.evidence.record("program", path.parent.name, "row", row)
        campaign.progress(f"{row['kernel']}: {row['survive_o2']}/{row['guards_total']} survive -O2")
    summary = summarize(rows)
    campaign.finish(run, summary, table(summary), derive)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
