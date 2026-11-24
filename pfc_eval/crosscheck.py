"""Second-solver cross-check of the generated obligations with cvc5 (advisory; revision plan, phase 6).

`pfc` proves with Z3 only, and `authorization` accepts only Z3's solver version; a second solver cannot grant
or withdraw removal authority. What it can do is witness: every obligation is exported as SMT-LIB 2 from the
same `smt.Encoder` terms (hypotheses and the negated goal, exactly the formula Z3 decided), fed to a pinned
cvc5 binary the user installed, and the two verdicts are compared. Agreement shrinks the solver's share of the
trusted base; a disagreement is a red flag to investigate, reported and never resolved by this module.

The harness never downloads cvc5: the path comes from `--cvc5`; its version string and SHA-256 are recorded in
the campaign configuration. Division is totalized inside the exported term (`ite (= r 0) 0 ...` over `abs` and
`div` on non-negative operands, where SMT-LIB's Euclidean `div` equals C truncation), so both solvers see the
same total function and no solver-specific semantics is involved.
"""

import hashlib
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import z3

from pfc import ir, smt, vc, workflow
from pfc.config import DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import campaign, evidence

DEFAULT_LOGIC = "QF_AUFNIA"  # arrays, uninterpreted functions, non-linear integer arithmetic (matvec multiplies)
CVC5_STATUSES = ("unsat", "sat", "unknown", "timeout", "error")
ADVISORY = (
    "advisory: cvc5 grants no removal authority; pfc authorization accepts only Z3's solver version. "
    "Agreement is a second witness of the same formula; a disagreement is reported for investigation."
)

SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("function", "string"),
        evidence.Column("obligation_id", "string"),
        evidence.Column("kind", "string"),
        evidence.Column("category", "string", allowed=("required", "reported", "entry")),
        evidence.Column("query_id", "string"),
        evidence.Column("z3_status", "string"),
        evidence.Column("z3_ms", "number"),
        evidence.Column("cvc5_status", "string", allowed=CVC5_STATUSES),
        evidence.Column("cvc5_ms", "number"),
        evidence.Column("agreement", "boolean", nullable=True),
    )
)


@dataclass(frozen=True)
class Cvc5Result:
    status: str
    elapsed_ms: float
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


# -- export --------------------------------------------------------------------------------------------------


def _smt2(encoder: smt.Encoder, assertions: Sequence[z3.ExprRef], logic: str) -> str:
    solver = z3.Solver()
    solver.add(*assertions)
    body = solver.to_smt2()
    lines = [line for line in body.splitlines() if not line.startswith("(set-logic")]
    return f"(set-logic {logic})\n" + "\n".join(lines).rstrip() + "\n"


def export(obligation: vc.Obligation, target: Target, *, logic: str = DEFAULT_LOGIC) -> str:
    """SMT-LIB 2 text of hypotheses AND NOT(goal): `unsat` proves the obligation, exactly as in `smt`."""
    encoder = smt.Encoder(target)
    hypotheses = [encoder.boolean(encoder.encode(item.formula)) for item in obligation.hypotheses]
    goal = encoder.boolean(encoder.encode(obligation.goal))
    return _smt2(encoder, [*hypotheses, z3.Not(goal)], logic)


def export_entry(entry: vc.EntryCheck, target: Target, *, logic: str = DEFAULT_LOGIC) -> str:
    """SMT-LIB 2 text of the entry hypotheses alone: `sat` means the contract is consistent."""
    encoder = smt.Encoder(target)
    hypotheses = [encoder.boolean(encoder.encode(item.formula)) for item in entry.hypotheses]
    return _smt2(encoder, hypotheses, logic)


# -- cvc5 ----------------------------------------------------------------------------------------------------


def cvc5_identity(binary: Path) -> dict[str, str]:
    """Path, version line and SHA-256 of the user-supplied cvc5; the harness never downloads one."""
    resolved = Path(binary).resolve()
    if not resolved.is_file():
        raise ValueError(f"cvc5 binary not found: {binary}")
    try:
        version = (
            subprocess.run([str(resolved), "--version"], capture_output=True, text=True, timeout=60, check=False)
            .stdout.strip()
            .splitlines()
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"cvc5 binary cannot report its version: {error}") from None
    return {
        "cvc5_path": str(resolved),
        "cvc5_version": version[0] if version else "",
        "cvc5_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
    }


def parse_status(stdout: str, stderr: str, returncode: int, *, elapsed_ms: float, timeout_ms: int) -> str:
    """First answer line decides; `unknown` at or past the budget is a timeout; anything else is an error."""
    first = next((line.strip() for line in stdout.splitlines() if line.strip()), "")
    if first in ("unsat", "sat"):
        return first
    if first == "unknown":
        return "timeout" if elapsed_ms >= timeout_ms else "unknown"
    del stderr, returncode  # recorded by the caller; they do not change the classification
    return "error"


def run_cvc5(binary: Path, text: str, *, timeout_ms: int, directory: Path) -> Cvc5Result:
    """One fresh query file in its own directory; argv, streams and exit status are kept beside it."""
    directory.mkdir(parents=True, exist_ok=True)
    query = directory / "query.smt2"
    query.write_text(text, encoding="utf-8")
    argv = (str(Path(binary).resolve()), "--lang=smt2", f"--tlimit={timeout_ms}", str(query))
    started = time.monotonic()
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_ms / 1000 + 60, check=False)
        stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, code = "", "outer process timeout", 124
    except OSError as error:
        stdout, stderr, code = "", str(error), 127
    elapsed = (time.monotonic() - started) * 1000
    (directory / "cvc5.stdout").write_text(stdout, encoding="utf-8")
    (directory / "cvc5.stderr").write_text(stderr, encoding="utf-8")
    status = parse_status(stdout, stderr, code, elapsed_ms=elapsed, timeout_ms=timeout_ms)
    return Cvc5Result(status, elapsed, argv, code, stdout, stderr)


def agreement(z3_status: str, cvc5_status: str, *, entry: bool = False) -> bool | None:
    """True/False when both solvers decided; None when either was inconclusive."""
    decided_cvc5 = cvc5_status in ("unsat", "sat")
    if entry:
        if z3_status in ("sat", "unsat") and decided_cvc5:
            return z3_status == cvc5_status
        return None
    mapping = {"proved": "unsat", "failed": "sat"}
    if z3_status in mapping and decided_cvc5:
        return mapping[z3_status] == cvc5_status
    return None


# -- per kernel ----------------------------------------------------------------------------------------------


def _short(identifier: str) -> str:
    return identifier.replace(":", "-")[:40]


def _z3_entry(entry: vc.EntryCheck, target: Target, timeout_ms: int) -> tuple[str, float]:
    encoder = smt.Encoder(target)
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    solver.add(*(encoder.boolean(encoder.encode(item.formula)) for item in entry.hypotheses))
    started = time.monotonic()
    outcome = solver.check()
    elapsed = (time.monotonic() - started) * 1000
    status = "sat" if outcome == z3.sat else "unsat" if outcome == z3.unsat else "unknown"
    return status, elapsed


def run_kernel(
    program: ir.Program,
    kernel: str,
    target: Target,
    *,
    cvc5: Path,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    directory: Path,
    logic: str = DEFAULT_LOGIC,
) -> list[dict[str, object]]:
    """Every obligation and entry query of a program through Z3 (fresh cache) and cvc5; one row each."""
    report = vc.generate(program)
    version = smt.solver_version()
    cache = smt.SolverCache()
    rows: list[dict[str, object]] = []
    for obligation in report.obligations:
        z3_result = cache.solve(obligation, target, timeout_ms, version, z3.Solver)
        text = export(obligation, target, logic=logic)
        result = run_cvc5(cvc5, text, timeout_ms=timeout_ms, directory=directory / kernel / _short(obligation.id))
        rows.append(
            {
                "kernel": kernel,
                "function": report.function,
                "obligation_id": obligation.id,
                "kind": obligation.kind,
                "category": obligation.category,
                "query_id": z3_result.query_id,
                "z3_status": z3_result.status,
                "z3_ms": z3_result.elapsed_ms,
                "cvc5_status": result.status,
                "cvc5_ms": result.elapsed_ms,
                "agreement": agreement(z3_result.status, result.status),
            }
        )
    for index, entry in enumerate(report.entry_checks):
        z3_status, z3_ms = _z3_entry(entry, target, timeout_ms)
        text = export_entry(entry, target, logic=logic)
        result = run_cvc5(cvc5, text, timeout_ms=timeout_ms, directory=directory / kernel / f"entry-{index:02d}")
        rows.append(
            {
                "kernel": kernel,
                "function": entry.function,
                "obligation_id": f"entry:{entry.function}:{index}",
                "kind": "entry",
                "category": "entry",
                "query_id": smt.entry_query_id(entry, target, timeout_ms, version),
                "z3_status": z3_status,
                "z3_ms": z3_ms,
                "cvc5_status": result.status,
                "cvc5_ms": result.elapsed_ms,
                "agreement": agreement(z3_status, result.status, entry=True),
            }
        )
    return rows


# -- summary -------------------------------------------------------------------------------------------------


def summarize(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    by_category: dict[str, dict[str, int]] = {}
    for category in ("required", "reported", "entry"):
        mine = [r for r in rows if r["category"] == category]
        if mine:
            by_category[category] = {
                "n": len(mine),
                "agree": sum(r["agreement"] is True for r in mine),
                "disagree": sum(r["agreement"] is False for r in mine),
                "inconclusive": sum(r["agreement"] is None for r in mine),
            }
    disagreements = [str(r.get("obligation_id")) for r in rows if r["agreement"] is False]
    return {
        "experiment": "crosscheck",
        "advisory": True,
        "note": ADVISORY,
        "by_category": by_category,
        "disagreements": disagreements,
        "cvc5_statuses": {s: sum(r["cvc5_status"] == s for r in rows) for s in CVC5_STATUSES},
        "passed": not disagreements,
    }


def render(summary: Mapping[str, object]) -> str:
    lines = [
        "| category | n | both decided and agree | disagree | inconclusive |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    categories = summary["by_category"]
    assert isinstance(categories, dict)
    for name, e in categories.items():
        lines.append(f"| {name} | {e['n']} | {e['agree']} | {e['disagree']} | {e['inconclusive']} |")
    lines.append("")
    lines.append(f"Result is advisory: {summary['note']}")
    disagreements = summary["disagreements"]
    assert isinstance(disagreements, list)
    if disagreements:
        lines.append("Disagreements to investigate: " + ", ".join(str(d) for d in disagreements))
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "query"]


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    cvc5: Path | None = None,
    logic: str = DEFAULT_LOGIC,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    if cvc5 is None:
        found = shutil.which("cvc5")
        if found is None:
            raise ValueError("no cvc5 binary: pass --cvc5 PATH (the harness never downloads a solver)")
        cvc5 = Path(found)
    identity = cvc5_identity(cvc5)
    sources = workflow.discover(tuple(directories))
    programs = [(source.parent.name, lower(parse_source(source, cpp=cpp))) for source in sources]
    if dry_run:
        for name, program in programs:
            report = vc.generate(program)
            print(f"{name}: {len(report.obligations)} obligation(s), {len(report.entry_checks)} entry query(ies)")
        print(
            f"Preview: {identity['cvc5_version']} at {identity['cvc5_path']}; logic {logic}; {timeout_ms} ms per query."
        )
        print("Advisory only. No files were written.")
        return True
    run = campaign.start(
        "crosscheck",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=None,
        settings={
            "timeout_ms": timeout_ms,
            "logic": logic,
            **identity,
            "z3_version": smt.solver_version(),
            "advisory": ADVISORY,
            "kernels": [name for name, _ in programs],
        },
        schema=SCHEMA,
    )
    all_rows: list[dict[str, object]] = []
    for name, program in programs:
        if progress:
            progress(f"crosscheck: {name}")
        rows = run_kernel(
            program,
            name,
            run.target,
            cvc5=cvc5,
            timeout_ms=timeout_ms,
            directory=run.evidence.directory / "smt2",
            logic=logic,
        )
        for index, row in enumerate(rows):
            run.evidence.record("query", name, f"{index:04d}", row)
        all_rows.extend(rows)
    summary = summarize(all_rows)
    campaign.finish(run, summary, render(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
