"""Audit record with offline replay of a `pfc build` directory (revision plan, phase 2C; ADR-0058).

For every kernel of a build directory (validated by `pfc.report.load`, which recomputes the sealed checksums), the
audit re-lowers the recorded source and checks `source_id`, replays every solver query with a fresh process-local
cache and compares the statuses with the recorded ones (a flip is reported, never accepted or corrected), re-emits
with the recorded mode and facts and compares the C byte for byte with the saved `kernel.c`, and builds an
independent site map from the saved C text alone (guard, overflow and entry trap shapes plus `// proved` citations)
to check that every removed site is cited and every retained site is guarded. The rows are replayable solver
evidence: a reproduction record, not a certificate. Recorded decisions are reported, never rewritten.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pfc import ir, report, smt
from pfc.config import DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.digest import sha256_text
from pfc.emit import ENTRY_LINE, GUARD_LINE, OVERFLOW_LINE, Mode, emit
from pfc.facts import FactName
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import campaign, evidence

CITATION = re.compile(r"// proved: obligation (\S+)")
SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("mode", "string"),
        evidence.Column("site_id", "string"),
        evidence.Column("kind", "string"),
        evidence.Column("location", "string"),
        evidence.Column("condition", "string"),
        evidence.Column("recorded_removed", "boolean"),
        evidence.Column("replayed_removed", "boolean", nullable=True),
        evidence.Column("obligation_ids", "string"),
        evidence.Column("recorded_statuses", "string"),
        evidence.Column("replayed_statuses", "string"),
        evidence.Column("status_agreement", "boolean", nullable=True),
        evidence.Column("cited_in_c", "boolean"),
        evidence.Column("c_identical", "boolean"),
        evidence.Column("source_id_match", "boolean"),
        evidence.Column("context_id", "string"),
        evidence.Column("emitter", "string"),
        evidence.Column("recorded_vc_model", "string"),
        evidence.Column("recorded_solver_version", "string"),
        evidence.Column("replay_solver_version", "string"),
        evidence.Column("recorded_clang", "string"),
    )
)


@dataclass(frozen=True)
class SiteMap:
    """What the C text alone says: trap shapes and citations, read without any emitter helper."""

    guards: int
    overflow_traps: int
    entry_traps: int
    citations: tuple[str, ...]


def site_map(c: str) -> SiteMap:
    return SiteMap(
        len(GUARD_LINE.findall(c)),
        len(OVERFLOW_LINE.findall(c)),
        len(ENTRY_LINE.findall(c)),
        tuple(CITATION.findall(c)),
    )


def _where(site: Mapping[str, object]) -> str:
    location = site["location"]
    assert isinstance(location, dict)
    return f"{location['line']}:{location['column']}"


def problems(
    found: SiteMap,
    decisions: Sequence[Mapping[str, object]],
    overflow: Sequence[Mapping[str, object]],
    *,
    rendered: int,
) -> tuple[str, ...]:
    """Independent consistency of the C text with the recorded decisions: counts per shape and citations."""
    found_problems: list[str] = []
    retained = sum(not d["removed"] for d in decisions)
    retained_overflow = sum(not d["removed"] for d in overflow)
    if found.guards != retained:
        found_problems.append(f"C has {found.guards} guard trap(s) but {retained} retained guard decision(s)")
    if found.overflow_traps != retained_overflow:
        found_problems.append(
            f"C has {found.overflow_traps} overflow trap(s) but {retained_overflow} retained overflow site(s)"
        )
    if found.entry_traps != rendered:
        found_problems.append(f"C has {found.entry_traps} entry trap(s) but {rendered} rendered conjunct(s)")
    for site in (*decisions, *overflow):
        if not site["removed"]:
            continue
        identifiers = site["obligation_ids"]
        assert isinstance(identifiers, list)
        if not identifiers:
            found_problems.append(f"removed {site['kind']} site at {_where(site)} without any obligation")
        for identifier in identifiers:
            if identifier not in found.citations:
                found_problems.append(f"removed {site['kind']} site at {_where(site)} without citation of {identifier}")
    return tuple(found_problems)


@dataclass(frozen=True)
class KernelAudit:
    name: str
    rows: tuple[dict[str, object], ...]
    problems: tuple[str, ...]
    status_flips: int
    c_identical: bool
    source_id_match: bool
    target_match: bool


def _statuses(proof: Mapping[str, object] | None) -> dict[str, str]:
    if not proof:
        return {}
    results = proof["results"]
    assert isinstance(results, list)
    return {str(r["obligation"]["id"]): str(r["status"]) for r in results}


def audit_kernel(directory: Path, *, compiler: str, cpp: str) -> KernelAudit:
    """Replay one kernel of a build directory; every discrepancy is a row value, never a correction."""
    data = json.loads((directory / "build.json").read_text(encoding="utf-8"))
    emission = data["emission"]
    mode = str(emission["mode"])
    saved_c = (directory / "kernel.c").read_text(encoding="utf-8")
    source = Path(str(data["source"]))
    if not source.is_file():
        raise ValueError(f"{directory.name}: recorded source {source} is missing")
    program = lower(parse_source(source, cpp=cpp))
    source_id_match = sha256_text(ir.print_c(program)) == data["source_id"]
    target = read_target(compiler)
    recorded_target = emission["target"]
    target_match = (recorded_target["triple"], recorded_target["int_bits"], recorded_target["long_bits"]) == (
        target.triple,
        target.int_bits,
        target.long_bits,
    )
    recorded_proof = emission.get("proof")
    recorded = _statuses(recorded_proof)
    replayed_proof = None
    if recorded_proof:
        replayed_proof = smt.check(
            program, target, timeout_ms=int(recorded_proof["timeout_ms"]), cache=smt.SolverCache()
        )
    replayed = {r.obligation.id: r.status for r in replayed_proof.results} if replayed_proof else {}
    facts = cast(frozenset[FactName], frozenset(str(f) for f in emission["facts"]))
    re_emitted = emit(program, target, mode=cast(Mode, mode), proof=replayed_proof, facts=facts)
    c_identical = re_emitted.c == saved_c and emission["c"] == saved_c
    replayed_sites = {
        f"{d.kind}:{d.location.line}:{d.location.column}:{d.condition}": d.removed
        for d in (*re_emitted.decisions, *re_emitted.overflow)
    }
    decisions = list(emission["decisions"])
    overflow = list(emission.get("overflow", []))
    rendered = sum(bool(c.get("rendered")) for c in emission.get("requires_checks", []))
    found = site_map(saved_c)
    kernel_problems = list(problems(found, decisions, overflow, rendered=rendered))
    if not source_id_match:
        kernel_problems.append("recorded source_id differs from the re-lowered program text")
    if not target_match:
        kernel_problems.append("recorded target differs from the selected compiler's integer layout")
    if not c_identical:
        kernel_problems.append("re-emitted C differs from the saved kernel.c")
    rows: list[dict[str, object]] = []
    flips = 0
    for site in (*decisions, *overflow):
        identifiers = [str(i) for i in site["obligation_ids"]]
        recorded_statuses = [recorded.get(i, "missing") for i in identifiers]
        replayed_statuses = [replayed.get(i, "missing") for i in identifiers]
        agreement: bool | None = None
        if recorded_proof:
            agreement = recorded_statuses == replayed_statuses
            flips += not agreement
        location = site["location"]
        key = f"{site['kind']}:{location['line']}:{location['column']}:{site.get('condition', '')}"
        rows.append(
            {
                "kernel": directory.name,
                "mode": mode,
                "site_id": f"{site['kind']}:{Path(str(location['file'])).name}:{location['line']}:{location['column']}",
                "kind": str(site["kind"]),
                "location": f"{Path(str(location['file'])).name}:{location['line']}:{location['column']}",
                "condition": str(site.get("condition", "")),
                "recorded_removed": bool(site["removed"]),
                "replayed_removed": replayed_sites.get(key),
                "obligation_ids": ";".join(identifiers),
                "recorded_statuses": ";".join(recorded_statuses),
                "replayed_statuses": ";".join(replayed_statuses),
                "status_agreement": agreement,
                "cited_in_c": (
                    all(f"// proved: obligation {i}" in saved_c for i in identifiers) if site["removed"] else True
                ),
                "c_identical": c_identical,
                "source_id_match": source_id_match,
                "context_id": str(emission["context_id"]),
                "emitter": str(emission.get("emitter", "unknown")),
                "recorded_vc_model": str(emission.get("vc_model", "unknown")),
                "recorded_solver_version": str(recorded_proof["solver_version"]) if recorded_proof else "",
                "replay_solver_version": smt.solver_version(),
                "recorded_clang": str(data["environment"]["clang"]),
            }
        )
    return KernelAudit(
        directory.name, tuple(rows), tuple(kernel_problems), flips, c_identical, source_id_match, target_match
    )


def summarize(audits: Sequence[KernelAudit]) -> dict[str, object]:
    flips = sum(a.status_flips for a in audits)
    uncited = sum(not r["cited_in_c"] for a in audits for r in a.rows)
    differences = sum(not a.c_identical for a in audits)
    problem_count = sum(len(a.problems) for a in audits)
    return {
        "experiment": "audit",
        "kernels": {
            a.name: {
                "sites": len(a.rows),
                "status_flips": a.status_flips,
                "c_identical": a.c_identical,
                "source_id_match": a.source_id_match,
                "target_match": a.target_match,
                "problems": list(a.problems),
            }
            for a in audits
        },
        "sites": sum(len(a.rows) for a in audits),
        "status_flips": flips,
        "uncited_removals": uncited,
        "c_differences": differences,
        "problems": problem_count,
        "passed": flips == 0 and uncited == 0 and differences == 0 and problem_count == 0,
        "statement": (
            "Replayable solver evidence: every recorded query was re-solved with a fresh process-local cache and "
            "compared with the recorded status; the C was re-emitted and compared byte for byte; the site map was read "
            "from the saved C text alone. This is a reproduction record, not a certificate; recorded decisions are "
            "never rewritten."
        ),
    }


def render_table(summary: Mapping[str, object]) -> str:
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    lines = [
        "| kernel | sites | status flips | C identical | source id | target | problems |",
        "| --- | ---: | ---: | --- | --- | --- | ---: |",
    ]
    for name, entry in kernels.items():
        lines.append(
            f"| {name} | {entry['sites']} | {entry['status_flips']} | {entry['c_identical']} | "
            f"{entry['source_id_match']} | {entry['target_match']} | {len(entry['problems'])} |"
        )
    lines.append("")
    lines.append(str(summary["statement"]))
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "site"]


def _kernel_directories(build: Path) -> list[Path]:
    return sorted(p.parent for p in build.glob("*/build.json"))


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """Audit one or more `pfc build` directories (sealed manifests are re-verified first by `report.load`)."""
    del timeout_ms  # the replay uses the timeout recorded with each proof, so the comparison is like for like
    builds = [Path(d) for d in directories]
    for build in builds:
        report.load(build)  # raises ValueError on a tampered or incomplete directory before anything is written
    if dry_run:
        for build in builds:
            names = [p.name for p in _kernel_directories(build)]
            print(f"{build}: {len(names)} kernel build(s) to audit: {', '.join(names)}")
        print("Preview: offline solver replay, re-emission and independent site map. No files were written.")
        return True
    run = campaign.start(
        "audit",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=None,
        settings={
            "builds": [str(b) for b in builds],
            "manifests": {str(b): sha256_text((b / "manifest.json").read_text(encoding="utf-8")) for b in builds},
            "replay_cache": "fresh smt.SolverCache per kernel",
        },
        schema=SCHEMA,
    )
    audits: list[KernelAudit] = []
    for build in builds:
        for directory in _kernel_directories(build):
            if progress:
                progress(f"audit: {directory.name}")
            audited = audit_kernel(directory, compiler=compiler, cpp=cpp)
            audits.append(audited)
            for index, row in enumerate(audited.rows):
                # Two operators at one location (ambiguous sites) share a site_id; the record id stays unique.
                run.evidence.record("site", audited.name, f"{index:03d}-{row['kind']}", row)
            run.evidence.write_text(
                f"audit/{audited.name}.json",
                json.dumps({"problems": list(audited.problems), "status_flips": audited.status_flips}, indent=2) + "\n",
            )
    summary = summarize(audits)
    campaign.finish(run, summary, render_table(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
