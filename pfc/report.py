"""Descriptive experiment tables and integrity-checked saved artifacts, never proof imports."""

import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
from typing import Literal

from pfc import ir, smt, vc
from pfc.config import MAX_REPORT_BYTES
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.emit import EMITTER, Emission


@dataclass(frozen=True)
class Row:
    name: str
    total: int
    proved: int
    failed: int
    timeout: int
    unknown: int
    error: int
    required_passed: bool | None
    checks: int
    removed: int
    compared: int | None
    agreed: int | None
    breakit_passed: bool | None
    passed: bool
    problem: str | None = None
    overflow_checks: int | None = None  # schema 3: arithmetic sites trapped unless proved (emitter v5)
    overflow_removed: int | None = None


@dataclass(frozen=True)
class Summary:
    kind: Literal["verify", "build", "test", "propose"]
    rows: tuple[Row, ...]
    passed: bool
    dry_run: bool = False
    schema_version: int = 3
    vc_model: str | None = vc.MODEL
    smt_encoding: str | None = smt.ENCODING
    emitter: str | None = EMITTER


def row(
    name: str,
    proof: smt.CheckReport | None,
    *,
    emission: Emission | None = None,
    compared: int | None = None,
    agreed: int | None = None,
    breakit: bool | None = None,
    passed: bool,
    problem: str | None = None,
) -> Row:
    results = proof.results if proof else ()
    if proof and problem is None:
        failures = [f"{r.query.function}: {r.status}" for r in proof.entry_results if r.status != "sat"]
        if failures:
            problem = "entry consistency not established (" + ", ".join(failures) + ")"
    counts = {
        status: sum(r.status == status for r in results)
        for status in ("proved", "failed", "timeout", "unknown", "error")
    }
    return Row(
        name,
        len(results),
        counts["proved"],
        counts["failed"],
        counts["timeout"],
        counts["unknown"],
        counts["error"],
        proof.required_passed if proof else None,
        len(emission.decisions) if emission else 0,
        sum(d.removed for d in emission.decisions) if emission else 0,
        compared,
        agreed,
        breakit,
        passed,
        problem,
        len(emission.overflow) if emission else 0,
        sum(d.removed for d in emission.overflow) if emission else 0,
    )


def render(summary: Summary) -> str:
    """Statuses include reported overflow; required admission has its own column."""
    lines = [
        "| kernel | total | proved | failed | timeout | unknown | error | required | "
        "checks removed | diff | break-it | result |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    if summary.schema_version >= 2:
        lines[0] = lines[0].replace("| total |", "| obligations |").replace("checks removed", "guard sites removed")
    if summary.schema_version >= 3:
        lines[0] = lines[0].replace("| diff |", "| overflow traps removed | diff |")
        lines[1] = lines[1].replace("| --- | --- | --- | --- |", "| --- | --- | --- | --- | --- |", 1)
    for entry in summary.rows:

        def status(value: bool | None) -> str:
            return "not-run" if value is None else "passed" if value else "failed"

        name = entry.name.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
        diff = "not-run" if entry.compared is None else f"{entry.agreed}/{entry.compared}"
        overflow = ""
        if summary.schema_version >= 3:
            overflow = (
                "n/a | " if entry.overflow_checks is None else f"{entry.overflow_removed}/{entry.overflow_checks} | "
            )
        lines.append(
            f"| {name} | {entry.total} | {entry.proved} | {entry.failed} | {entry.timeout} | "
            f"{entry.unknown} | {entry.error} | {status(entry.required_passed)} | "
            f"{entry.removed}/{entry.checks} | {overflow}{diff} | {status(entry.breakit_passed)} | "
            f"{status(entry.passed)} |"
        )
        if entry.problem:
            lines.append(f'<!-- {name}: {entry.problem.replace(chr(10), " ").replace("--", "—")} -->')
    lines.append(
        "\nDescriptive evidence; failed counts include reported overflow. This table grants no proof authority."
    )
    if summary.dry_run:
        lines.append("Preview: no artifacts were written; compilation was not performed by build.")
    if summary.schema_version >= 2:
        lines.append(f"VC model: {summary.vc_model}; SMT encoding: {summary.smt_encoding}.")
        lines.append("Entry consistency queries are separate from obligation counts; optimization requires SAT.")
    if summary.schema_version >= 3:
        lines.append(
            f"Emitter: {summary.emitter}. Overflow traps are removed only with a cited proof; unproved ones stay."
        )
    return "\n".join(lines) + "\n"


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(ir.to_data(value), stream, sort_keys=True, indent=2)
        stream.write("\n")


def seal(directory: Path, summary: Summary) -> None:
    """Write summary then manifest last, including failed/partial build evidence."""
    write_json(directory / "report.json", summary)
    hashes = {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }
    with (directory / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": summary.schema_version,
                "vc_model": summary.vc_model,
                "smt_encoding": summary.smt_encoding,
                "emitter": summary.emitter,
                "files": hashes,
            },
            stream,
            indent=2,
            sort_keys=True,
        )
        stream.write("\n")


def _read(path: Path) -> object:
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("report exceeds size limit")
    return json.loads(path.read_text(encoding="utf-8"))


_SCHEMA_3_FIELDS = ("overflow_checks", "overflow_removed")


def _row(data: object, schema_version: int = 3) -> Row:
    expected = {f.name for f in fields(Row)}
    if schema_version < 3:
        expected -= set(_SCHEMA_3_FIELDS)
    if not isinstance(data, dict) or set(data) - {"node"} != expected:
        raise ValueError("invalid report row shape")
    if schema_version < 3:
        data = {**data, "overflow_checks": None, "overflow_removed": None}
    integers = ("total", "proved", "failed", "timeout", "unknown", "error", "checks", "removed")
    if any(type(data[key]) is not int or data[key] < 0 for key in integers):
        raise ValueError("invalid report count")
    for key in _SCHEMA_3_FIELDS:
        if data[key] is not None and (type(data[key]) is not int or data[key] < 0):
            raise ValueError("invalid overflow count")
    if (data["overflow_checks"] is None) != (data["overflow_removed"] is None) or (
        data["overflow_checks"] is not None and data["overflow_removed"] > data["overflow_checks"]
    ):
        raise ValueError("inconsistent overflow counts")
    for key in ("compared", "agreed"):
        if data[key] is not None and (type(data[key]) is not int or data[key] < 0):
            raise ValueError("invalid differential count")
    for key in ("required_passed", "breakit_passed"):
        if data[key] is not None and type(data[key]) is not bool:
            raise ValueError("invalid report status")
    if type(data["passed"]) is not bool or not isinstance(data["name"], str) or not data["name"]:
        raise ValueError("invalid row identity/status")
    if data["problem"] is not None and not isinstance(data["problem"], str):
        raise ValueError("invalid problem diagnostic")
    if (
        sum(data[k] for k in ("proved", "failed", "timeout", "unknown", "error")) != data["total"]
        or data["removed"] > data["checks"]
        or (data["compared"] is None) != (data["agreed"] is None)
        or data["compared"] is not None
        and data["agreed"] > data["compared"]
    ):
        raise ValueError("inconsistent report counts")
    if data["passed"] and (
        data["required_passed"] is False
        or data["breakit_passed"] is False
        or data["compared"] is not None
        and data["compared"] != data["agreed"]
    ):
        raise ValueError("success contradicts row evidence")
    return Row(**{field.name: data[field.name] for field in fields(Row)})


def load(directory: Path) -> Summary:
    """Load a saved summary after checksum/schema validation; never deserialize proofs."""
    try:
        manifest = _read(directory / "manifest.json")
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") not in (1, 2, 3)
            or not isinstance(manifest.get("files"), dict)
        ):
            raise ValueError("invalid manifest")
        if "report.json" not in manifest["files"]:
            raise ValueError("summary missing from manifest")
        for name, digest in manifest["files"].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError("unsafe manifest path")
            path = directory.joinpath(*relative.parts)
            if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
                raise ValueError("manifest path escapes artifact directory")
            if path.stat().st_size > MAX_REPORT_BYTES or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("artifact checksum or size mismatch")
        present = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
        if present - {"manifest.json"} != set(manifest["files"]):
            # Benchmark, experiment and transcript readers run after this check; nothing unsealed may join.
            raise ValueError("evidence directory contains files absent from the sealed manifest")
        data = _read(directory / "report.json")
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != manifest["schema_version"]
            or data.get("kind") not in {"verify", "build", "test", "propose"}
        ):
            raise ValueError("invalid report schema")
        if not isinstance(data.get("rows"), list) or not data["rows"] or type(data.get("passed")) is not bool:
            raise ValueError("invalid report summary")
        rows = tuple(_row(item, data["schema_version"]) for item in data["rows"])
        if data["passed"] != all(item.passed for item in rows) or type(data.get("dry_run")) is not bool:
            raise ValueError("inconsistent aggregate status")
        model, encoding, emitter = None, None, None
        if data["schema_version"] >= 2:
            for key in ("vc_model", "smt_encoding"):
                if not isinstance(data.get(key), str) or not data[key] or data[key] != manifest.get(key):
                    raise ValueError("missing or mismatched evidence model metadata")
            model, encoding = data["vc_model"], data["smt_encoding"]
        if data["schema_version"] >= 3:
            if (
                not isinstance(data.get("emitter"), str)
                or not data["emitter"]
                or data["emitter"] != manifest.get("emitter")
            ):
                raise ValueError("missing or mismatched emitter identity")
            emitter = data["emitter"]
        return Summary(
            data["kind"], rows, data["passed"], data["dry_run"], data["schema_version"], model, encoding, emitter
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        raise FrontendError(
            "report-input", f"cannot validate saved evidence ({type(error).__name__})", SourceLocation(str(directory))
        ) from error
