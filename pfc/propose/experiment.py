"""Three independent sampling runs, offline rechecking, and descriptive G2 evidence."""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from pathlib import Path
from statistics import pvariance
from typing import Literal
from uuid import uuid4

from pfc import ir, report, vc, workflow
from pfc.build import require_new_directory
from pfc.config import DEFAULT_CPP, OPENAI_KEY_ENV, PROPOSER_ATTEMPTS, PROPOSER_RUNS, SOLVER_TIMEOUT_MS
from pfc.corpus import NEAR_MISS_INVARIANTS, PROFILES, profile
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.propose.invariants import Admission, Attempt, Synthesis, admit, loop_count, record_attempt, strip, synthesize
from pfc.propose.llm import Exchange, Request, Settings, send_request, validate
from pfc.target import Target, read_target

ExperimentMode = Literal["live", "replay", "test"]


@dataclass(frozen=True)
class KernelRun:
    kernel: str
    run: int
    source_id: str
    found: bool
    attempts: int
    accepted: int
    rejected: int
    errors: int
    hand: int
    proposed: int
    wrong_rejected: bool


@dataclass(frozen=True)
class WrongInjection:
    """Both deliberately false candidates must fail with a real model before any sampling counts."""

    literal_false: Admission
    near_miss: Admission

    @property
    def rejected(self) -> bool:
        return all(
            not item.accepted
            and item.proof is not None
            and any(r.status == "failed" and r.counterexample for r in item.proof.results)
            for item in (self.literal_false, self.near_miss)
        )


def inject_wrong(program: ir.Program, target: Target, *, timeout_ms: int = SOLVER_TIMEOUT_MS) -> WrongInjection:
    name = profile(program)
    count = loop_count(program)
    return WrongInjection(
        admit(program, json.dumps({"invariants": ["0"] * count}), target, timeout_ms=timeout_ms),
        admit(program, json.dumps({"invariants": list(NEAR_MISS_INVARIANTS[name])}), target, timeout_ms=timeout_ms),
    )


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    mode: ExperimentMode
    settings: Settings
    rows: tuple[KernelRun, ...]
    successes: tuple[int, ...]
    variance: float
    criterion_met: bool
    g2_passed: bool
    schema_version: int = 1
    vc_model: str | None = vc.MODEL


def summarize(
    identity: str, mode: ExperimentMode, rows: tuple[KernelRun, ...], *, settings: Settings | None = None
) -> Experiment:
    settings = settings or Settings()
    successes = tuple(sum(r.found for r in rows if r.run == number) for number in range(1, PROPOSER_RUNS + 1))
    complete = {r.kernel for r in rows} == set(PROFILES) and len(rows) == len(PROFILES) * PROPOSER_RUNS
    criterion = complete and sum(n >= 3 for n in successes) >= 2 and all(r.wrong_rejected for r in rows)
    return Experiment(
        identity,
        mode,
        settings,
        rows,
        successes,
        float(pvariance(successes)),
        criterion,
        mode == "live" and criterion,
    )


def render(experiment: Experiment) -> str:
    lines = [
        "| kernel | run | invariants hand | invariants proposed | attempts | accepted | rejected | errors | found |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {r.kernel} | {r.run} | {r.hand} | {r.proposed} | {r.attempts} | {r.accepted} | "
        f"{r.rejected} | {r.errors} | {str(r.found).lower()} |"
        for r in experiment.rows
    )
    gate = "passed" if experiment.g2_passed else "failed" if experiment.mode == "live" else "not-run"
    lines.extend(
        [
            f"\nG2: {gate}; mode: {experiment.mode}; successes/run: {experiment.successes}; "
            f"population variance: {experiment.variance:.6f}.",
            "Replay/test results are not independent model samples. Hand counts describe the source; "
            "all hand invariants were removed before proposals.",
        ]
    )
    return "\n".join(lines) + "\n"


def load(directory: Path) -> Experiment:
    """After report.load integrity checks, validate descriptive experiment counts."""
    data = report._read(directory / "experiment.json")
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or data.get("mode") not in ("live", "replay", "test")
        or not isinstance(data.get("experiment_id"), str)
        or not data["experiment_id"]
        or not isinstance(data.get("rows"), list)
        or not data["rows"]
    ):
        raise ValueError("invalid experiment schema/settings")
    settings = Settings.from_data(data.get("settings"))
    rows = []
    for item in data["rows"]:
        if not isinstance(item, dict) or set(item) - {"node"} != {f.name for f in fields(KernelRun)}:
            raise ValueError("invalid kernel run")
        if (
            not isinstance(item["kernel"], str)
            or item["kernel"] not in PROFILES
            or item["source_id"] != PROFILES[item["kernel"]]
        ):
            raise ValueError("unreviewed experiment source")
        if any(
            type(item[k]) is not int or item[k] < 0
            for k in ("run", "attempts", "accepted", "rejected", "errors", "hand", "proposed")
        ):
            raise ValueError("invalid experiment counts")
        if (
            item["run"] not in (1, 2, 3)
            or not 1 <= item["attempts"] <= PROPOSER_ATTEMPTS
            or item["accepted"] not in (0, 1)
            or type(item["found"]) is not bool
            or type(item["wrong_rejected"]) is not bool
            or item["found"] != bool(item["accepted"])
            or item["attempts"] != item["accepted"] + item["rejected"] + item["errors"]
            or item["proposed"] != (item["hand"] if item["found"] else 0)
        ):
            raise ValueError("inconsistent experiment counts")
        rows.append(KernelRun(**{f.name: item[f.name] for f in fields(KernelRun)}))
    if len({(r.kernel, r.run) for r in rows}) != len(rows) or any(
        {r.run for r in rows if r.kernel == name} != {1, 2, 3} for name in {r.kernel for r in rows}
    ):
        raise ValueError("duplicate or missing experiment runs")
    result = summarize(data["experiment_id"], data["mode"], tuple(rows), settings=settings)
    if any(
        data.get(key) != ir.to_data(getattr(result, key))
        for key in ("successes", "variance", "criterion_met", "g2_passed")
    ):
        raise ValueError("inconsistent experiment aggregate")
    model = data.get("vc_model")
    if model is not None and (not isinstance(model, str) or not model):
        raise ValueError("invalid experiment VC model")
    return replace(result, vc_model=model)


def _transcripts(directory: Path) -> dict[tuple[str, int], list[dict[str, object]]]:
    report.load(directory)
    experiment = load(directory)
    path = directory / "logs/proposals.jsonl"
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("oversized proposal transcript")
    groups: dict[tuple[str, int], list[dict[str, object]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        data = json.loads(line)
        if not isinstance(data, dict) or not isinstance(data.get("sample_id"), str):
            raise ValueError("invalid proposal transcript")
        parts = data["sample_id"].split("/")
        if len(parts) != 4 or parts[1] not in ("run-1", "run-2", "run-3") or parts[2] not in PROFILES:
            raise ValueError("invalid transcript sampling identity")
        key = (parts[2], int(parts[1][-1]))
        items = groups.setdefault(key, [])
        if parts[3] != str(len(items) + 1) or data.get("kernel") != key[0] or len(items) >= PROPOSER_ATTEMPTS:
            raise ValueError("invalid transcript attempt sequence")
        items.append(data)
    if set(groups) != {(r.kernel, r.run) for r in experiment.rows} or any(
        len(groups[(r.kernel, r.run)]) != r.attempts for r in experiment.rows
    ):
        raise ValueError("transcript does not match experiment rows")
    return groups


def _recheck(
    program: ir.Program,
    target: Target,
    records: list[dict[str, object]],
    log: Path,
    timeout_ms: int,
    *,
    settings: Settings | None = None,
) -> Synthesis:
    settings = settings or Settings()
    baseline = admit(program, json.dumps({"invariants": ["1"] * loop_count(program)}), target, timeout_ms=timeout_ms)
    attempts = []
    for number, data in enumerate(records, 1):
        prompt, sample = data.get("prompt"), data.get("sample_id")
        if not isinstance(prompt, str) or not isinstance(sample, str) or data.get("settings") != ir.to_data(settings):
            raise ValueError("invalid replay request")
        request = Request(prompt, sample, settings)
        payload = data.get("exchange")
        exchange = None
        admission = None
        if payload is not None:
            if not isinstance(payload, dict) or set(payload) - {"node"} != {f.name for f in fields(Exchange)}:
                raise ValueError("invalid replay exchange")
            exchange = Exchange(**{f.name: payload[f.name] for f in fields(Exchange)})
            validate(exchange, settings)
            exchange = replace(exchange, origin="replay")
            admission = admit(program, exchange.text, target, timeout_ms=timeout_ms)
        attempt = Attempt(
            number, request, exchange, admission, "recorded service failure" if exchange is None else None
        )
        record_attempt(log, profile(program), attempt)
        attempts.append(attempt)
        if admission and admission.accepted:
            return Synthesis(True, tuple(attempts), baseline)
    return Synthesis(False, tuple(attempts), baseline)


def run(
    directories: tuple[Path, ...],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    live: bool = False,
    replay: Path | None = None,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    send: Callable[[Request], Exchange] = send_request,
    progress: Callable[[str], None] | None = None,
) -> Experiment:
    require_new_directory(output)
    if live == bool(replay):
        raise ValueError("choose exactly one of --live or --replay DIR")
    sources = workflow.discover(directories)
    programs = {}
    for source in sources:
        program = lower(parse_source(source, cpp=cpp))
        programs[profile(program)] = program
    if len(programs) != len(sources):
        raise ValueError("duplicate kernel profile")
    if live and send is send_request and not os.environ.get(OPENAI_KEY_ENV):
        raise ValueError("configure OPENAI_API_KEY in the environment before --live")
    transcripts = _transcripts(replay) if replay else {}
    settings = load(replay).settings if replay else Settings()
    if replay and {key[0] for key in transcripts} != set(programs):
        raise ValueError("replay requires exactly the recorded kernel set")
    target = read_target(compiler)
    identity = uuid4().hex
    mode: ExperimentMode = "replay" if replay else "live" if send is send_request else "test"
    output.mkdir(parents=True, exist_ok=False)
    (output / "sources").mkdir()
    (output / "wrong-injection").mkdir()
    wrong = {}
    for name, p in programs.items():
        (output / "sources" / f"{name}.c").write_text(ir.print_c(strip(p)), encoding="utf-8")
        injection = inject_wrong(p, target, timeout_ms=timeout_ms)
        wrong[name] = injection.rejected
        report.write_json(output / "wrong-injection" / f"{name}.json", injection)
    rows = []
    summary_rows = []
    for number in range(1, PROPOSER_RUNS + 1):
        for name, program in sorted(programs.items()):
            if progress:
                progress(f"run {number}/{PROPOSER_RUNS}: {name}")
            if replay:
                result = _recheck(
                    program,
                    target,
                    transcripts[(name, number)],
                    output / "logs/proposals.jsonl",
                    timeout_ms,
                    settings=settings,
                )
            else:
                result = synthesize(
                    program,
                    target,
                    settings=settings,
                    sample_id=f"{identity}/run-{number}/{name}",
                    cache=output / "cache",
                    log=output / "logs/proposals.jsonl",
                    live=True,
                    send=send,
                    timeout_ms=timeout_ms,
                )
            origins = {a.exchange.origin for a in result.attempts if a.exchange}
            if "test" in origins:
                mode = "test"
            elif "replay" in origins and mode == "live":
                mode = "replay"
            accepted = sum(bool(a.admission and a.admission.accepted) for a in result.attempts)
            rejected = sum(bool(a.admission and not a.admission.accepted) for a in result.attempts)
            errors = sum(a.admission is None for a in result.attempts)
            count = loop_count(program)
            rows.append(
                KernelRun(
                    name,
                    number,
                    PROFILES[name],
                    result.found,
                    len(result.attempts),
                    accepted,
                    rejected,
                    errors,
                    count,
                    count if result.found else 0,
                    wrong[name],
                )
            )
            final: Admission = result.attempts[-1].admission or result.baseline
            emission = emit(final.program, target, mode="verified", proof=final.proof) if final.program else None
            summary_rows.append(
                report.row(f"{name}/run-{number}", final.proof, emission=emission, passed=result.found and wrong[name])
            )
            if result.found and final.program and emission:
                directory = output / f"run-{number}" / name
                directory.mkdir(parents=True)
                (directory / "proposed.c").write_text(ir.print_c(final.program), encoding="utf-8")
                (directory / "verified.c").write_text(emission.c, encoding="utf-8")
    experiment = summarize(identity, mode, tuple(rows), settings=settings)
    report.write_json(output / "experiment.json", experiment)
    summary = report.Summary("propose", tuple(summary_rows), all(r.passed for r in summary_rows))
    (output / "tables.md").write_text(report.render(summary) + "\n" + render(experiment), encoding="utf-8")
    report.seal(output, summary)
    return experiment
