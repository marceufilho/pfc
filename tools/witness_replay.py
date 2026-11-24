"""Run the stored witnesses of the hypothesis-withdrawal campaigns.

A removed check matters only on an execution where it would fire, and no such execution exists for a program that
is safe under its contract. The withdrawal campaign builds programs that are *not* safe: each drops one REQUIRES
conjunct, and for 88 of them the sealed campaign stored an input that the weakened contract admits and that reaches
an access out of bounds. The campaign checked, in the emitted text, that the guard was back. This tool runs those
inputs: the weakened program is rebuilt with the frozen compiler, its checked and verified C are compiled with the
campaign flags, and the witness must be stopped by a trap in both.

A verified build is executed only when the sealed row says the witnessed site's guard was restored; otherwise the
access would be undefined and the row records "not executed". Nothing here confers proof authority.

    uv run --locked python tools/witness_replay.py kernels bench/external/acsl \\
        --campaign out/conjuncts \\
        --output out/witness-replay-dev
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pfc import ir, smt  # noqa: E402
from pfc.config import C_FLAGS, CLANG_ENV_VAR, CPP_ENV_VAR, DEFAULT_CLANG, DEFAULT_CPP  # noqa: E402
from pfc.emit import Emission, emit  # noqa: E402
from pfc.lower import lower  # noqa: E402
from pfc.parse import parse_source  # noqa: E402
from pfc.target import read_target  # noqa: E402
from pfc_eval import campaign, conjuncts, evidence, heldout, interpret  # noqa: E402

EXPERIMENT = "witness-replay"
SIGILL = -4
OUTCOMES = ("trap", "returned", "compile error", "not executed")
STOPS = ("witnessed guard", "another guard", "other trap", "returned", "compile error", "not executed")
MARKER_EXIT = 100  # Guard k of a marked copy ends the process with this code plus k.
SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("function", "string"),
        evidence.Column("requires_index", "integer"),
        evidence.Column("conjunct_index", "integer"),
        evidence.Column("conjunct_text", "string"),
        evidence.Column("witness_kind", "string"),
        evidence.Column("witness_message", "string"),
        evidence.Column("witness_line", "integer"),
        evidence.Column("checked", "string", allowed=OUTCOMES),
        evidence.Column("verified", "string", allowed=OUTCOMES),
        evidence.Column("checked_stop", "string", allowed=STOPS),
        evidence.Column("verified_stop", "string", allowed=STOPS),
    )
)


def _driver(function: ir.Function, inputs: interpret.Inputs) -> str:
    declarations, arguments = [], []
    for parameter in function.parameters:
        name = parameter.symbol.name
        if parameter.length is not None:
            values = list(inputs.arrays[name])
            cells = ", ".join(f"{v}L" for v in values) or "0L"
            declarations.append(f"    static long {name}[{max(len(values), 1)}] = {{{cells}}};")
            arguments.append(name)
        else:
            arguments.append(f"{inputs.scalars[name]}L")
    prototype = ", ".join(ir._declaration(p.symbol) for p in function.parameters)
    body = "\n".join(declarations)
    call = f"{function.name}({', '.join(arguments)})"
    return (
        f"#include <stdio.h>\nlong {function.name}({prototype});\nint main(void) {{\n{body}\n"
        f'    printf("%ld\\n", {call});\n'
        "    return 0;\n}\n"
    )


def _replay(c_text: str, main_text: str, clang: str) -> str:
    """Compile one emitted kernel with the witness driver and report what the process did."""
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        (base / "kernel.c").write_text(c_text, encoding="utf-8")
        (base / "main.c").write_text(main_text, encoding="utf-8")
        arguments = [clang, *C_FLAGS, str(base / "kernel.c"), str(base / "main.c"), "-o", str(base / "p")]
        compiled = subprocess.run(arguments, capture_output=True, text=True, timeout=60, check=False)
        if compiled.returncode != 0:
            return "compile error"
        result = subprocess.run([str(base / "p")], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode == SIGILL:
        return "trap (SIGILL)"
    return f"exit {result.returncode}, printed {result.stdout.strip()}"


def _clang_baseline() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clang_baseline", ROOT / "tools" / "clang_baseline.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outcome(text: str) -> str:
    if text.startswith("trap"):
        return "trap"
    return "compile error" if text == "compile error" else "returned"


def stopped_by(emission: Emission, main_text: str, clang: str, witness_line: int) -> str:
    """Which check ends the run: the same C with guard k ending the process through its own marker.

    The markers are defined in the driver; overflow traps and entry checks keep `__builtin_trap()`. The k-th marker
    is the k-th guard the emission kept, in source order. Only the line is compared, because a witness records the
    line of the access and several guards can share it.
    """
    marked, count = _clang_baseline().mark(emission.c)
    kept = [decision for decision in emission.decisions if not decision.removed]
    if count != len(kept):
        raise RuntimeError(f"{count} guard lines for {len(kept)} kept guards")
    definitions = "#include <unistd.h>\n" + "".join(
        f"_Noreturn void pfc_guard_{index}(void) {{ _exit({MARKER_EXIT + index}); }}\n" for index in range(1, count + 1)
    )
    text = _replay(marked, definitions + main_text, clang)
    if text.startswith("trap"):
        return "other trap"
    if text == "compile error":
        return text
    code = int(text.split(",")[0].removeprefix("exit "))
    if not MARKER_EXIT < code <= MARKER_EXIT + count:
        return "returned"
    return "witnessed guard" if kept[code - MARKER_EXIT - 1].location.line == witness_line else "another guard"


def replay_kernel(directory: Path, sealed: Path, clang: str, *, cpp: str = DEFAULT_CPP) -> list[dict[str, object]]:
    """One row per safety witness of one kernel of a sealed withdrawal campaign."""
    name = directory.name
    target = read_target(clang)
    program = lower(parse_source(directory / "kernel.c", cpp=cpp))
    sealed_rows = {
        (row["function"], row["requires_index"], row["conjunct_index"]): row
        for row in map(json.loads, (sealed / "observations.jsonl").read_text("utf-8").splitlines())
        if row["kernel"] == name
    }
    witnesses = {}
    path = sealed / "raw" / "witness" / f"{name}.jsonl"
    for line in path.read_text("utf-8").splitlines() if path.is_file() else []:
        record = json.loads(line)
        witnesses[record["id"]] = record["data"]
    rows: list[dict[str, object]] = []
    for function in program.functions:
        for conjunct in conjuncts.conjuncts(function):
            key = (function.name, conjunct.requires_index, conjunct.index)
            sealed_row = sealed_rows.get(key)
            data = witnesses.get(f"{conjunct.requires_index}-{conjunct.index}")
            if sealed_row is None or data is None or data["kind"] != "safety":
                continue
            if sealed_row["witness_kind"] != "safety" or sealed_row["conjunct_text"] != data["conjunct_text"]:
                raise RuntimeError(f"{name}: the witness record does not match the sealed row {key}")
            variant = conjuncts.drop(program, conjunct, function=function.name)
            inputs = interpret.Inputs(data["inputs"]["scalars"], data["inputs"]["arrays"])
            main_text = _driver(function, inputs)
            line = int(sealed_row["witness_line"])
            checked_emission = emit(variant, target, mode="checked")
            checked = _outcome(_replay(checked_emission.c, main_text, clang))
            checked_stop = stopped_by(checked_emission, main_text, clang, line)
            if sealed_row["witness_site_restored"]:
                verified_emission = emit(variant, target, mode="verified", proof=smt.check(variant, target))
                verified = _outcome(_replay(verified_emission.c, main_text, clang))
                verified_stop = stopped_by(verified_emission, main_text, clang, line)
            else:
                verified = verified_stop = "not executed"
            rows.append(
                {
                    "kernel": name,
                    "function": function.name,
                    "requires_index": conjunct.requires_index,
                    "conjunct_index": conjunct.index,
                    "conjunct_text": data["conjunct_text"],
                    "witness_kind": data["kind"],
                    "witness_message": data["message"],
                    "witness_line": line,
                    "checked": checked,
                    "verified": verified,
                    "checked_stop": checked_stop,
                    "verified_stop": verified_stop,
                }
            )
    return rows


def summarize(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    checked = sum(row["checked"] == "trap" for row in rows)
    verified = sum(row["verified"] == "trap" for row in rows)
    return {
        "witnesses": len(rows),
        "checked_trap": checked,
        "verified_trap": verified,
        "checked_stopped_by_witnessed_guard": sum(row["checked_stop"] == "witnessed guard" for row in rows),
        "verified_stopped_by_witnessed_guard": sum(row["verified_stop"] == "witnessed guard" for row in rows),
        "checked_stops": dict(Counter(str(row["checked_stop"]) for row in rows)),
        "verified_stops": dict(Counter(str(row["verified_stop"]) for row in rows)),
        "passed": bool(rows) and checked == len(rows) and verified == len(rows),
    }


def table(summary: Mapping[str, object]) -> str:
    return (
        "# Stored witnesses replayed on the weakened programs (post hoc)\n\n"
        f"Witnesses that reach an access out of bounds: {summary['witnesses']}.\n\n"
        "| Build of the weakened program | Stopped by a trap |\n| --- | ---: |\n"
        f"| checked | {summary['checked_trap']} |\n| verified | {summary['verified_trap']} |\n"
    )


def derive(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "witness"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the stored safety witnesses of a sealed withdrawal campaign.")
    parser.add_argument("directories", nargs="+", type=Path, help="kernel directories or their parents")
    parser.add_argument("--campaign", type=Path, required=True, help="sealed conjuncts campaign with the witnesses")
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    arguments = parser.parse_args(argv)
    compiler = os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
    cpp = os.environ.get(CPP_ENV_VAR, DEFAULT_CPP)
    heldout.require_intact(arguments.directories)
    if not evidence.status(arguments.campaign).complete:
        print(f"witness_replay: {arguments.campaign} is not a sealed campaign", file=sys.stderr)
        return 1
    paths: list[Path] = []
    for directory in arguments.directories:
        paths += (
            [directory]
            if (directory / "kernel.c").is_file()
            else sorted(p.parent for p in directory.glob("*/kernel.c"))
        )
    settings = {"campaign": str(arguments.campaign), "kernels": [p.name for p in paths], "post_hoc": True}
    run = campaign.start(
        EXPERIMENT, arguments.output, compiler=compiler, cpp=cpp, seed=None, settings=settings, schema=SCHEMA
    )
    rows: list[dict[str, object]] = []
    for path in paths:
        found = replay_kernel(path, arguments.campaign, compiler, cpp=cpp)
        for row in found:
            run.evidence.record(
                "witness", path.name, f"{row['function']}-{row['requires_index']}-{row['conjunct_index']}", row
            )
        rows += found
        campaign.progress(f"{path.name}: {len(found)} witness(es)")
    summary = summarize(rows)
    campaign.finish(run, summary, table(summary), derive)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
