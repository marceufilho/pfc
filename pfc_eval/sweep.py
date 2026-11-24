"""Solver-budget sweep for pfc/Z3 and WP/Alt-Ergo (roadmap E8, design §4, Figure 2).

For each kernel and budget, `pfc check` runs in a fresh process with that timeout, so no cached result
can cross budgets (the query identity includes the timeout and is recorded per row). The WP side runs the
handwritten ACSL of the baseline oracle through the pinned image with the same budgets, one fresh
container per case and budget. Rows are long format: one goal or obligation per row, with the tool, the
budget and the raw status. The proved fraction against budget is the curve; it is descriptive.
"""

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from pfc import workflow
from pfc.config import DEFAULT_CPP, MAX_SOLVER_TIMEOUT_MS, SOLVER_TIMEOUT_MS
from pfc_eval import campaign, evidence
from pfc_eval.wp import (
    WP_INPUT,
    Profile,
    case_for,
    describe,
    image_available,
    run_wp_case,
    verify_case_source,
    wp_assessment,
    wp_command,
    wp_outer_timeout,
    wp_profile,
    wp_rows,
)

DEFAULT_BUDGETS = (1, 2, 5, 10, 30, 60)
DEFAULT_BUDGET_TEXT = "1,2,5,10,30,60"
TOOLS = ("pfc", "wp")
DEFAULT_TOOL_TEXT = "pfc,wp"
PFC_PROCESS_TIMEOUT_S = 900
__all__ = [
    "DEFAULT_BUDGETS",
    "WP_INPUT",
    "Profile",
    "image_available",
    "parse_budgets",
    "pfc_command",
    "pfc_rows",
    "run_pfc",
    "run_wp_case",
    "summarize",
    "render_table",
    "verify_case_source",
    "wp_assessment",
    "wp_command",
    "wp_outer_timeout",
    "wp_profile",
    "wp_rows",
]

SCHEMA = evidence.Schema(
    (
        evidence.Column("tool", "string", allowed=TOOLS),
        evidence.Column("kernel", "string"),
        evidence.Column("budget_s", "integer"),
        evidence.Column("unit", "string", allowed=("obligation", "goal")),
        evidence.Column("identifier", "string"),
        evidence.Column("kind", "string"),
        evidence.Column("category", "string"),
        evidence.Column("status", "string"),
        evidence.Column("solver_ms", "number", nullable=True),
        evidence.Column("query_id", "string", nullable=True),
        evidence.Column("smoke", "boolean", nullable=True),
    )
)


def parse_budgets(text: str) -> tuple[int, ...]:
    """Distinct positive whole seconds within the compiler's accepted timeout range, in the given order."""
    budgets = []
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit():
            raise ValueError(f"budget {part!r} is not a positive integer")
        value = int(part)
        if value < 1 or value * 1000 > MAX_SOLVER_TIMEOUT_MS or value in budgets:
            raise ValueError(f"budget {value} is out of range or repeated")
        budgets.append(value)
    if not budgets:
        raise ValueError("at least one budget is required")
    return tuple(budgets)


def pfc_command(source: Path, budget_s: int, *, compiler: str, cpp: str) -> list[str]:
    return [
        "pfc",
        "check",
        str(source),
        "--timeout-ms",
        str(budget_s * 1000),
        "--clang",
        compiler,
        "--cpp",
        cpp,
        "--json",
    ]


def pfc_rows(kernel: str, budget_s: int, report: Mapping[str, object]) -> list[dict[str, object]]:
    """Tidy rows from one `pfc check --json` report; the recorded timeout must equal the budget."""
    if report.get("timeout_ms") != budget_s * 1000:
        raise ValueError("report timeout differs from the requested budget")
    results = report.get("results")
    if not isinstance(results, list):
        raise ValueError("report without results")
    rows: list[dict[str, object]] = []
    for item in results:
        obligation = item["obligation"]
        rows.append(
            {
                "tool": "pfc",
                "kernel": kernel,
                "budget_s": budget_s,
                "unit": "obligation",
                "identifier": str(obligation["id"]),
                "kind": str(obligation["kind"]),
                "category": str(obligation["category"]),
                "status": str(item["status"]),
                "solver_ms": float(item["elapsed_ms"]),
                "query_id": str(item["query_id"]),
                "smoke": None,
            }
        )
    return rows


def _run_process(argv: Sequence[str], timeout_s: int) -> tuple[int, str, str]:
    result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout_s, check=False)
    return result.returncode, result.stdout, result.stderr


def run_pfc(
    source: Path,
    kernel: str,
    budgets: Sequence[int],
    *,
    compiler: str,
    cpp: str,
    run: Callable[[Sequence[str], int], tuple[int, str, str]] = _run_process,
) -> list[dict[str, object]]:
    """One fresh `pfc check` process per budget; exit 1 (failed required proofs) still yields rows."""
    rows: list[dict[str, object]] = []
    for budget in budgets:
        code, stdout, stderr = run(pfc_command(source, budget, compiler=compiler, cpp=cpp), PFC_PROCESS_TIMEOUT_S)
        if code not in (0, 1) or not stdout.strip():
            raise ValueError(f"pfc check failed for {kernel} at {budget}s (exit {code}): {stderr.strip()[:200]}")
        rows.extend(pfc_rows(kernel, budget, json.loads(stdout)))
    return rows


def summarize(
    rows: Sequence[Mapping[str, object]], *, budgets: Sequence[int], tools: Sequence[str]
) -> dict[str, object]:
    """Proved fraction per tool and budget (pfc: required obligations; wp: non-smoke goals), and per kernel."""

    def counted(tool: str, row: Mapping[str, object]) -> bool:
        if tool == "pfc":
            return row["category"] == "required"
        return row["smoke"] is False

    def proved(tool: str, row: Mapping[str, object]) -> bool:
        return row["status"] == ("proved" if tool == "pfc" else "valid")

    curve: dict[str, dict[str, dict[str, int]]] = {}
    kernels: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    for tool in tools:
        curve[tool] = {}
        for budget in budgets:
            selected = [r for r in rows if r["tool"] == tool and r["budget_s"] == budget and counted(tool, r)]
            if not selected:
                continue
            curve[tool][str(budget)] = {"proved": sum(proved(tool, r) for r in selected), "total": len(selected)}
            for kernel in sorted({str(r["kernel"]) for r in selected}):
                own = [r for r in selected if r["kernel"] == kernel]
                kernels.setdefault(kernel, {}).setdefault(tool, {})[str(budget)] = {
                    "proved": sum(proved(tool, r) for r in own),
                    "total": len(own),
                }
    return {"experiment": "sweep", "budgets": list(budgets), "tools": list(tools), "curve": curve, "kernels": kernels}


def render_table(summary: Mapping[str, object]) -> str:
    budgets = summary["budgets"]
    assert isinstance(budgets, list)
    lines = ["| tool | " + " | ".join(f"budget {b} s" for b in budgets) + " |", "| --- |" + " ---: |" * len(budgets)]
    curve = summary["curve"]
    assert isinstance(curve, dict)
    for tool, points in curve.items():
        cells = []
        for budget in budgets:
            point = points.get(str(budget))
            cells.append(f"{point['proved']}/{point['total']}" if point else "n/a")
        lines.append(f"| {tool} | " + " | ".join(cells) + " |")
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    lines.append("")
    lines.append("| kernel | tool | " + " | ".join(f"{b} s" for b in budgets) + " |")
    lines.append("| --- | --- |" + " ---: |" * len(budgets))
    for kernel, by_tool in kernels.items():
        for tool, points in by_tool.items():
            cells = [
                f"{points[str(b)]['proved']}/{points[str(b)]['total']}" if str(b) in points else "n/a" for b in budgets
            ]
            lines.append(f"| {kernel} | {tool} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Proved fraction against solver budget (Figure 2 data). pfc counts required obligations proved by Z3 in a "
        "fresh process per budget; wp counts non-smoke WP goals valid under Alt-Ergo in a fresh container per budget. "
        "The curve is descriptive; a timeout is a row, never an error, and no result crosses budgets."
    )
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "row"]


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    budgets: str = DEFAULT_BUDGET_TEXT,
    tools: str = DEFAULT_TOOL_TEXT,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    prover: str | None = None,
    image: str | None = None,
) -> bool:
    del timeout_ms  # The sweep sets its own per-budget timeouts; the shared flag does not apply.
    budget_list = parse_budgets(budgets)
    tool_list = tuple(t.strip() for t in tools.split(",") if t.strip())
    if not tool_list or any(t not in TOOLS for t in tool_list):
        raise ValueError(f"tools must be a subset of {TOOLS}")
    sources = workflow.discover(tuple(directories))
    kernels = [(source.parent.name, source) for source in sources]
    profile: Profile | None = wp_profile(prover=prover, image=image) if "wp" in tool_list else None
    wp_reason = "not requested"
    if profile is not None:
        if not image_available(profile.image):
            wp_reason = "pinned image not available locally"
            profile = None
        else:
            wp_reason = ""
    if dry_run:
        for name, _source in kernels:
            acsl = "with handwritten ACSL" if profile and case_for(profile, name) else "pfc only"
            print(f"{name}: budgets {list(budget_list)}; {acsl}")
        print(f"Preview: tools {list(tool_list)}; WP {'enabled' if profile else 'disabled: ' + wp_reason}.")
        print("No files were written.")
        return True
    run = campaign.start(
        "sweep",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=None,
        settings={
            "budgets_s": list(budget_list),
            "tools": list(tool_list),
            "pfc_process_timeout_s": PFC_PROCESS_TIMEOUT_S,
            "wp": describe(profile) if profile else None,
            "wp_skipped_reason": wp_reason or None,
            "wp_outer_timeout_s": {str(b): wp_outer_timeout(b) for b in budget_list} if profile else None,
            "kernels": [name for name, _ in kernels],
        },
        schema=SCHEMA,
    )
    all_rows: list[dict[str, object]] = []
    wp_errors = 0
    for name, source in kernels:
        if "pfc" in tool_list:
            if progress:
                progress(f"sweep pfc: {name}")
            rows = run_pfc(source, name, budget_list, compiler=compiler, cpp=cpp)
            for index, row in enumerate(rows):
                run.evidence.record("row", f"pfc-{name}", f"{index:04d}", row)
            all_rows.extend(rows)
        case = case_for(profile, name) if profile else None
        if case is not None:
            for budget in budget_list:
                if progress:
                    progress(f"sweep wp: {name} at {budget}s")
                assert profile is not None
                outcome = run_wp_case(profile, case, budget, run.evidence.directory / "wp" / name / f"{budget:02d}s")
                for index, row in enumerate(outcome.rows):
                    run.evidence.record("row", f"wp-{name}", f"{budget:02d}-{index:04d}", row)
                run.evidence.record(
                    "assessment",
                    name,
                    f"{budget:02d}",
                    {**asdict(outcome), "rows": len(outcome.rows)},
                )
                all_rows.extend(outcome.rows)
                if outcome.assessment.get("status") == "error":
                    wp_errors += 1
                    if progress:
                        progress(f"sweep wp: {name} at {budget}s failed: {outcome.assessment.get('problem')}")
    summary = summarize(all_rows, budgets=budget_list, tools=tool_list)
    summary["wp_skipped_reason"] = wp_reason or None
    summary["wp_errors"] = wp_errors
    campaign.finish(run, summary, render_table(summary), derive_rows)
    return wp_errors == 0  # An operational failure of a WP run is never reported as a completed sweep.


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
