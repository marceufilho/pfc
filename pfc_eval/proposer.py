"""Proposer conditions L1 (feedback), L2 (independent samples) and L3 (open-weight endpoint) over function
tasks, every candidate admitted only through the frozen `propose.invariants.admit` (roadmap E10, design §5).

The curated `synthesize()` stays untouched: it accepts only the six reviewed kernels and the pinned
protocol. This runner builds the same instruction for any accepted program, varies only the feedback
channel per condition, records every request and response as a research transcript (sources must be
repository programs), and replays archived transcripts with zero requests. Success is the frozen
`Admission.accepted`; nothing here relabels a rejection.
"""

import json
import os
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from pfc import ir, smt, vc
from pfc.config import DEFAULT_CPP, OPENAI_KEY_ENV, PROPOSER_ATTEMPTS, SOLVER_TIMEOUT_MS
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import SpecKind, parse_source
from pfc.propose.invariants import Admission, admit, feedback, loop_count, strip
from pfc.target import Target
from pfc.workflow import discover
from pfc_eval import adapters, campaign, evidence, expressions, stats

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("L1", "L2", "L3")
ATTEMPTS = PROPOSER_ATTEMPTS
DEFAULT_RUNS = 5
FEEDBACK_MARKERS = ("checker feedback", "previous proposal", "counterexample", "previous request failed")
REJECTION_CLASSES = (
    "success",
    "not_inductive",
    "too_weak",
    "out_of_scope",
    "abstained",
    "unparseable",
    "protocol_error",
)
Sender = Callable[[adapters.Request], adapters.Exchange]
SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("function", "string"),
        evidence.Column("condition", "string", allowed=CONDITIONS),
        evidence.Column("run", "integer"),
        evidence.Column("attempt", "integer"),
        evidence.Column("model", "string"),
        evidence.Column("protocol", "string"),
        evidence.Column("base_url", "string"),
        evidence.Column("snapshot_status", "string", allowed=("dated", "undated")),
        evidence.Column("origin", "string", allowed=("live", "replay", "test")),
        evidence.Column("success", "boolean"),
        evidence.Column("task_success", "boolean"),
        evidence.Column("rejection_class", "string", allowed=REJECTION_CLASSES),
        evidence.Column("tokens_in", "integer", nullable=True),
        evidence.Column("tokens_out", "integer", nullable=True),
        evidence.Column("reasoning_tokens", "integer", nullable=True),
        evidence.Column("latency_ms", "number", nullable=True),
        evidence.Column("loops", "integer"),
        evidence.Column("guards_total", "integer", nullable=True),
        evidence.Column("guards_removed", "integer", nullable=True),
    )
)


@dataclass(frozen=True)
class Attempt:
    number: int
    request: adapters.Request
    exchange: adapters.Exchange | None
    admission: Admission | None
    error: str | None
    rejection_class: str


@dataclass(frozen=True)
class TaskResult:
    condition: str
    attempts: tuple[Attempt, ...]
    success: bool


# --- prompts ----------------------------------------------------------------------------------------


def _nodes(node: object, *, contracts: bool = True) -> Iterator[ir.Node]:
    """Every IR node under `node`; with `contracts=False`, annotation subtrees are skipped (executable code only)."""
    if isinstance(node, ir.Node):
        if not contracts and isinstance(node, ir.Contract):
            return
        yield node
        for field in fields(node):
            yield from _nodes(getattr(node, field.name), contracts=contracts)
    elif isinstance(node, tuple):
        for item in node:
            yield from _nodes(item, contracts=contracts)


def _divides(nodes: Sequence[ir.Node]) -> bool:
    return any(isinstance(node, ir.Binary) and node.operator in ("/", "%") for node in nodes)


def _bare(text: str) -> str:
    """Drop the renderer's outermost parentheses when they enclose the whole expression."""
    depth = 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth == 0 and index < len(text) - 1:
            return text
    return text[1:-1] if text.startswith("(") and text.endswith(")") else text


def _listed(noun: str, items: Sequence[str]) -> str:
    return f"the {noun}{'s' if len(items) > 1 else ''} " + "; ".join(_bare(item) for item in items)


def entry(program: ir.Program) -> ir.Function:
    """The task's entry function: the one no other function calls (callees precede callers in the subset).

    With several roots the last defined one is the entry, matching the single-function convention
    (`functions[0]`) when the unit has one function.
    """
    called = {node.function for function in program.functions for node in _nodes(function) if isinstance(node, ir.Call)}
    roots = [function for function in program.functions if function.name not in called]
    return (roots or list(program.functions))[-1]


def goals(program: ir.Program) -> str:
    """The proof goals of the task read from the IR: assertions, postconditions, call preconditions, bounds, division.

    Protocol v2/v3 (paper/AMENDMENTS.md item 8): the v3 development wording asked only for the variables the
    bounds and division proofs need, and on scalar tasks whose goal is a post-loop ASSERT the model answered
    with empty lists. Naming the goals keeps one instruction for every task without telling the model an answer.
    Every function of the unit contributes, because the invariants array covers every loop in program order.
    """
    nodes = tuple(node for function in program.functions for node in _nodes(function))
    contracts = [node for node in nodes if isinstance(node, ir.Contract)]
    asserts = [expressions.render(c.condition) for c in contracts if c.kind is SpecKind.ASSERT]
    ensures = [expressions.render(c.condition) for c in contracts if c.kind is SpecKind.ENSURES]
    callees = sorted({node.function for node in nodes if isinstance(node, ir.Call)})
    parts: list[str] = []
    if asserts:
        parts.append(
            _listed("assertion", asserts)
            + " (after a loop, the invariant with the negated loop condition must imply it)"
        )
    if ensures:
        parts.append(_listed("postcondition", ensures))
    parts.extend(f"the precondition of {callee} at every call" for callee in callees)
    if any(isinstance(node, ir.Index) for node in nodes):
        parts.append("bounds of every array access")
    if _divides(nodes):
        parts.append("definedness of every division")
    return "; ".join(parts) if parts else "initiation and preservation only"


def overflow_caveat(program: ir.Program) -> str:
    """What the frozen admission demands of C arithmetic, stated for this program (protocol v3).

    Admission succeeds when every required obligation is proved and every executable guard is removed; guard
    removal needs the arithmetic that reaches the guard's operands, through the invariant's variables, proved
    free of overflow (ADR-0023). A function without executable array accesses or divisions has no guard, so
    overflow stays a reported obligation there and the v2 caveat would forbid variables the goals need (the
    empty-list replies of proposer-L1-zilu-20261105b).
    """
    executable = tuple(node for function in program.functions for node in _nodes(function.body, contracts=False))
    if any(isinstance(node, ir.Index) for node in executable) or _divides(executable):
        return (
            "Mention only variables these goals need: every C arithmetic operation on a mentioned variable must "
            "also be proved free of overflow before a bounds or division check is removed, so mention an "
            "accumulator only when a goal depends on it."
        )
    return (
        "Overflow of the C arithmetic is reported separately and does not decide admission for this function, "
        "so mention any in-scope variable the goals need."
    )


def instruction(program: ir.Program) -> str:
    """The goal-directed instruction (protocol pfc-eval-proposer-v3) for any accepted function.

    It differs from the frozen pfc-invariants-v3 text in one sentence: instead of "mention only variables that
    the bounds and division proofs need ... avoid accumulators", it names the proof goals read from the program
    (`goals`) and states the overflow requirement the admission really imposes on it (`overflow_caveat`).
    """
    name = entry(program).name
    source = ir.print_c(strip(program))
    unit = (
        f" The unit defines {len(program.functions)} functions; loops are numbered in preorder across all of them."
        if len(program.functions) > 1
        else ""
    )
    return (
        f"Propose inductive loop invariants for the C function {name}.{unit} "
        f"Return JSON with an invariants array of {loop_count(program)} expressions, one per loop in preorder. "
        "Use only call-free C integer/Boolean expressions over in-scope names. "
        "Do not change code or contracts. Prove initiation, preservation and the proof goals; "
        f"annotation arithmetic is mathematical. No quantifiers. Proof goals: {goals(program)}. "
        f"{overflow_caveat(program)}\nFunction without handwritten invariants:\n" + source
    )


def contains_feedback(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in FEEDBACK_MARKERS)


def _trivial(program: ir.Program) -> str:
    return json.dumps({"invariants": ["1"] * loop_count(program)})


# --- classification ---------------------------------------------------------------------------------


def abstained(text: str) -> bool:
    """A well-formed reply whose invariant list is empty: the model declined rather than failed to format."""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, Mapping) and data.get("invariants") == []


def classify(exchange: adapters.Exchange | None, admission: Admission | None, error: str | None) -> str:
    """Design Table VIII classes (plus `abstained`, AMENDMENTS item 15); derived from the frozen admission
    except for the abstention, which is a property of the reply the admission rejects as a count mismatch."""
    if error is not None or exchange is None or admission is None:
        return "protocol_error"
    if admission.accepted:
        return "success"
    if admission.problem is not None and abstained(exchange.text):
        return "abstained"
    if admission.problem is not None:
        problem = admission.problem.lower()
        protocol = ("expected", "json", "expecting", "16384", "call-free", "invariants", "delimiter")
        return "unparseable" if any(token in problem for token in protocol) else "out_of_scope"
    if admission.proof is not None:
        for result in admission.proof.results:
            if result.obligation.category != "required":
                continue
            inductive = result.obligation.kind in ("entry", "preservation")
            if inductive and (result.status != "proved" or not result.dependencies_satisfied):
                return "not_inductive"
    return "too_weak"


# --- one function task ------------------------------------------------------------------------------


def settings_for(
    condition: str, *, model: str, effort: adapters.Effort, temperature: float | None, base_url: str | None
) -> adapters.Settings:
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {', '.join(CONDITIONS)}")
    if condition == "L3":
        if base_url is None:
            raise ValueError("L3 needs --base-url, the OpenAI-compatible endpoint recorded in the evidence")
        return adapters.Settings(model, temperature, 2048, effort, base_url=base_url)
    if base_url is not None:
        raise ValueError("--base-url is only meaningful for L3; L1 and L2 use the OpenAI endpoint")
    return adapters.Settings(model, temperature, 2048, effort)


def run_task(
    program: ir.Program,
    target: Target,
    *,
    condition: str,
    settings: adapters.Settings,
    sample_id: str,
    cache: Path,
    live: bool,
    send: Sender = adapters.send,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    attempts: int = ATTEMPTS,
) -> TaskResult:
    """At most `attempts` proposals; L1/L3 feed the checker's reply back, L2 repeats the bare instruction."""
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {', '.join(CONDITIONS)}")
    base = instruction(program)
    history = ""
    if condition != "L2":
        baseline = admit(program, _trivial(program), target, timeout_ms=timeout_ms)
        history = "\nChecker feedback for true at every loop:\n" + feedback(baseline)
    records: list[Attempt] = []
    for number in range(1, attempts + 1):
        request = adapters.Request(base + history, f"{sample_id}/{number}", settings)
        exchange = None
        admission = None
        error = None
        try:
            exchange = adapters.complete(request, cache, live=live, send=send)
            admission = admit(program, exchange.text, target, timeout_ms=timeout_ms)
            if condition != "L2":
                history = "\nPrevious proposal:\n" + exchange.text + "\nChecker feedback:\n" + feedback(admission)
        except (ValueError, OSError) as problem:
            error = str(problem)
            if condition != "L2":
                history = "\nPrevious request failed. Produce a fresh proposal."
        attempt = Attempt(number, request, exchange, admission, error, classify(exchange, admission, error))
        records.append(attempt)
        if admission is not None and admission.accepted:
            return TaskResult(condition, tuple(records), True)
    return TaskResult(condition, tuple(records), False)


# --- transcripts and replay -------------------------------------------------------------------------


def record(log: Path, kernel: str, condition: str, attempt: Attempt) -> None:
    """One JSON line per attempt, a superset of the curated transcript format."""
    admission = attempt.admission
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        json.dump(
            {
                "vc_model": vc.MODEL,
                "smt_encoding": smt.ENCODING,
                "sample_id": attempt.request.sample_id,
                "kernel": kernel,
                "condition": condition,
                "attempt": attempt.number,
                "settings": asdict(attempt.request.settings),
                "prompt": attempt.request.prompt,
                "exchange": asdict(attempt.exchange) if attempt.exchange else None,
                "accepted": bool(admission and admission.accepted),
                "rejection_class": attempt.rejection_class,
                "proof": ir.to_data(admission.proof) if admission else None,
                "problem": admission.problem if admission else attempt.error,
            },
            stream,
            sort_keys=True,
        )
        stream.write("\n")


def _identity(sample_id: str) -> tuple[str, str, int, str, int]:
    """(identity, condition, run, kernel, attempt) from either transcript format."""
    parts = sample_id.split("/")
    if len(parts) == 4 and parts[1].startswith("run-"):
        identity, run, kernel, attempt = parts
        condition = "L1"  # the curated experiment always fed the checker's reply back
    elif len(parts) == 5 and parts[1] in CONDITIONS and parts[2].startswith("run-"):
        identity, condition, run, kernel, attempt = parts
    else:
        raise ValueError(f"unrecognized transcript sampling identity {sample_id!r}")
    if not run[4:].isdigit() or not attempt.isdigit():
        raise ValueError(f"unrecognized transcript sampling identity {sample_id!r}")
    return identity, condition, int(run[4:]), kernel, int(attempt)


def replay_groups(log: Path) -> dict[tuple[str, int], list[dict[str, object]]]:
    """Transcript lines grouped by (kernel, run) with the attempt sequence validated; no network."""
    if log.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("oversized proposal transcript")
    groups: dict[tuple[str, int], list[dict[str, object]]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        data = json.loads(line)
        if not isinstance(data, dict) or not isinstance(data.get("sample_id"), str):
            raise ValueError("invalid proposal transcript")
        _, condition, run, kernel, attempt = _identity(data["sample_id"])
        if data.get("kernel") != kernel:
            raise ValueError("transcript kernel does not match its sampling identity")
        items = groups.setdefault((kernel, run), [])
        if attempt != len(items) + 1 or len(items) >= ATTEMPTS:
            raise ValueError("invalid transcript attempt sequence")
        data.setdefault("condition", condition)
        if data["condition"] != condition:
            raise ValueError("transcript condition does not match its sampling identity")
        items.append(data)
    return groups


def _settings_from(data: object) -> adapters.Settings:
    if not isinstance(data, Mapping):
        raise ValueError("transcript without settings")
    effort = data.get("reasoning_effort")
    temperature = data.get("temperature")
    tokens = data.get("max_output_tokens")
    return adapters.Settings(
        str(data["model"]),
        float(str(temperature)) if temperature is not None else None,
        int(str(tokens)) if tokens is not None else 2048,
        str(effort) if effort is not None else None,
        base_url=str(data.get("base_url", adapters.OPENAI_BASE_URL)),
        protocol=str(data.get("protocol", adapters.PROTOCOL)),
    )


def _exchange_from(payload: object, settings: adapters.Settings) -> adapters.Exchange:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid replay exchange")
    exchange = adapters.Exchange(
        str(payload["text"]),
        str(payload["model"]),
        str(payload["response_id"]),
        int(str(payload["input_tokens"])),
        int(str(payload["output_tokens"])),
        int(str(payload.get("reasoning_tokens", 0))),
        float(str(payload.get("latency_ms", 0.0))),
        "replay",
    )
    adapters.validate(exchange, settings)
    return exchange


def recheck(
    program: ir.Program, target: Target, records: Sequence[Mapping[str, object]], *, timeout_ms: int = SOLVER_TIMEOUT_MS
) -> TaskResult:
    """Re-admit every recorded reply through frozen admit(); the service is never contacted."""
    attempts: list[Attempt] = []
    condition = str(records[0]["condition"]) if records else "L1"
    for number, data in enumerate(records, 1):
        settings = _settings_from(data.get("settings"))
        prompt, sample = data.get("prompt"), data.get("sample_id")
        if not isinstance(prompt, str) or not isinstance(sample, str):
            raise ValueError("invalid replay request")
        request = adapters.Request(prompt, sample, settings)
        exchange = None
        admission = None
        error = None
        if data.get("exchange") is not None:
            exchange = _exchange_from(data["exchange"], settings)
            admission = admit(program, exchange.text, target, timeout_ms=timeout_ms)
        else:
            error = "recorded service failure"
        attempt = Attempt(number, request, exchange, admission, error, classify(exchange, admission, error))
        attempts.append(attempt)
        if admission is not None and admission.accepted:
            return TaskResult(condition, tuple(attempts), True)
    return TaskResult(condition, tuple(attempts), False)


def replay_consistent(records: Sequence[Mapping[str, object]], result: TaskResult) -> bool:
    """The rechecked verdicts must equal the recorded ones, attempt by attempt."""
    if len(records) != len(result.attempts):
        return False
    return all(
        bool(data.get("accepted")) == bool(attempt.admission and attempt.admission.accepted)
        for data, attempt in zip(records, result.attempts, strict=True)
    )


def authorize_transcripts(sources: Sequence[Path], root: Path) -> None:
    """Full transcripts are research artifacts; only programs inside the repository may enter them."""
    for source in sources:
        if not source.resolve().is_relative_to(root.resolve()):
            raise ValueError(
                f"{source} is outside the repository; full transcripts are recorded only for repository programs"
            )


# --- rows and summary -------------------------------------------------------------------------------


def guard_counts(
    program: ir.Program, admission: Admission | None, target: Target | None
) -> tuple[int | None, int | None]:
    """(executable guard sites of the task, sites the admission's verified emission removed); None without a target.

    The denominator comes from the checked emission of the task program (no solver); the numerator re-emits the
    admitted candidate with its in-process proof, so a rejected admission that still removes some sites counts
    them (the guards endpoint scores the best emitted build), and an attempt without an admission counts None.
    """
    if target is None:
        return None, None
    total = len(emit(program, target, mode="checked").decisions)
    if admission is None or admission.program is None or admission.proof is None:
        return total, None
    verified = emit(admission.program, target, mode="verified", proof=admission.proof)
    return total, sum(d.removed for d in verified.decisions)


def rows(
    kernel: str,
    program: ir.Program,
    run: int,
    result: TaskResult,
    *,
    origin: str | None = None,
    target: Target | None = None,
) -> list[dict[str, object]]:
    table: list[dict[str, object]] = []
    total: int | None = None
    if target is not None:
        total = len(emit(program, target, mode="checked").decisions)
    for attempt in result.attempts:
        exchange = attempt.exchange
        settings = attempt.request.settings
        removed: int | None = None
        if target is not None and attempt.admission is not None:
            removed = guard_counts(program, attempt.admission, target)[1]
        table.append(
            {
                "kernel": kernel,
                "function": program.functions[0].name,
                "condition": result.condition,
                "run": run,
                "attempt": attempt.number,
                "model": settings.model,
                "protocol": settings.protocol,
                "base_url": settings.base_url,
                "snapshot_status": settings.snapshot_status(),
                "origin": origin or (exchange.origin if exchange else "live"),
                "success": attempt.rejection_class == "success",
                "task_success": result.success,
                "rejection_class": attempt.rejection_class,
                "tokens_in": exchange.input_tokens if exchange else None,
                "tokens_out": exchange.output_tokens if exchange else None,
                "reasoning_tokens": exchange.reasoning_tokens if exchange else None,
                "latency_ms": exchange.latency_ms if exchange else None,
                "loops": loop_count(program),
                "guards_total": total,
                "guards_removed": removed,
            }
        )
    return table


def summarize(rows_: Sequence[Mapping[str, object]], *, runs: int, seed: int) -> dict[str, object]:
    """success@R per task, its cluster-bootstrap mean over kernels, attempts to first success, tokens."""
    conditions: dict[str, dict[str, object]] = {}
    for condition in sorted({str(r["condition"]) for r in rows_}):
        mine = [r for r in rows_ if r["condition"] == condition]
        kernels = sorted({str(r["kernel"]) for r in mine})
        per_run: dict[tuple[str, int], bool] = {}
        for r in mine:
            key = (str(r["kernel"]), int(str(r["run"])))
            per_run[key] = per_run.get(key, False) or bool(r["success"])
        success_at_k = {k: sum(per_run.get((k, n), False) for n in range(1, runs + 1)) / runs for k in kernels}
        clusters = {k: [1.0 if per_run.get((k, n), False) else 0.0 for n in range(1, runs + 1)] for k in kernels}
        bootstrap = stats.cluster_bootstrap(clusters, stats.mean, seed=seed)
        first = [
            int(str(r["attempt"])) for r in mine if r["success"]
        ]  # one success closes a (kernel, run); its attempt number is the attempts to first success
        tokens = sum(int(str(r["tokens_in"] or 0)) + int(str(r["tokens_out"] or 0)) for r in mine)
        successes = sum(per_run.values())
        conditions[condition] = {
            "tasks": len(kernels),
            "runs": runs,
            "attempts": len(mine),
            "success_at_k": success_at_k,
            "mean_success_at_k": {
                "estimate": bootstrap.estimate,
                "low": bootstrap.interval.low,
                "high": bootstrap.interval.high,
                "replicates": len(bootstrap.replicates),
                "seed": seed,
            },
            "successes_by_run": [sum(per_run.get((k, n), False) for k in kernels) for n in range(1, runs + 1)],
            "attempts_to_first_success": stats.mean(first) if first else None,
            "rejection_classes": dict(
                sorted(Counter(str(r["rejection_class"]) for r in mine if not r["success"]).items())
            ),
            "tokens_total": tokens,
            "tokens_per_success": tokens / successes if successes else None,
            "origins": dict(sorted(Counter(str(r["origin"]) for r in mine).items())),
        }
    return {"conditions": conditions}


def render_table(summary: Mapping[str, object]) -> str:
    conditions: Mapping[str, Mapping[str, object]] = summary["conditions"]  # type: ignore[assignment]
    lines = ["# Proposer conditions (design §5)", ""]
    for name, data in conditions.items():
        runs = data["runs"]
        mean: Mapping[str, object] = data["mean_success_at_k"]  # type: ignore[assignment]
        lines += [
            f"## {name}: {data['tasks']} tasks, {runs} runs, {data['attempts']} attempts",
            "",
            f"Mean success@{runs}: {float(str(mean['estimate'])):.3f} "
            f"[{float(str(mean['low'])):.3f}, {float(str(mean['high'])):.3f}] (cluster bootstrap over kernels)",
            f"Successes by run: {data['successes_by_run']}; attempts to first success: "
            f"{data['attempts_to_first_success']}; tokens per success: {data['tokens_per_success']}",
            f"Rejection classes: {data['rejection_classes']}; origins: {data['origins']}",
            "",
            f"| task | success@{runs} |",
            "| --- | ---: |",
        ]
        per_task: Mapping[str, object] = data["success_at_k"]  # type: ignore[assignment]
        lines += [f"| {task} | {float(str(value)):.2f} |" for task, value in per_task.items()]
        lines.append("")
    return "\n".join(lines)


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "attempt"]


# --- campaign ---------------------------------------------------------------------------------------


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
    condition: str = "L1",
    runs: int = DEFAULT_RUNS,
    live: bool = False,
    replay: Path | None = None,
    base_url: str | None = None,
    model: str = "gpt-6-astra",
    effort: adapters.Effort = "low",
    temperature: float | None = None,
    send: Sender | None = None,
) -> bool:
    """One campaign per condition; live requests or a zero-request replay of archived transcripts."""
    if live == (replay is not None):
        raise ValueError("choose exactly one of --live or --replay DIR")
    if runs < 1:
        raise ValueError("runs must be at least 1")
    sources = discover(tuple(directories))
    authorize_transcripts(sources, ROOT)
    programs = {source.parent.name: lower(parse_source(source, cpp=cpp)) for source in sources}
    tasks = {name: p for name, p in programs.items() if loop_count(p) > 0}
    skipped = sorted(set(programs) - set(tasks))
    groups: dict[tuple[str, int], list[dict[str, object]]] = {}
    if replay is not None:
        groups = replay_groups(replay / "logs" / "proposals.jsonl")
        recorded_runs = {run_ for _, run_ in groups}
        if {kernel for kernel, _ in groups} != set(tasks) or recorded_runs != set(range(1, runs + 1)):
            raise ValueError("replay requires exactly the recorded kernel set and run count")
        settings = _settings_from(next(iter(groups.values()))[0].get("settings"))
        condition = str(next(iter(groups.values()))[0]["condition"])
    else:
        settings = settings_for(condition, model=model, effort=effort, temperature=temperature, base_url=base_url)
        if send is None and not os.environ.get(OPENAI_KEY_ENV):
            raise ValueError(f"configure {OPENAI_KEY_ENV} in the environment before --live")
    if dry_run:
        for name, program in sorted(tasks.items()):
            print(f"{name}: {loop_count(program)} loop(s), {runs} run(s) x up to {ATTEMPTS} attempts, {condition}")
        for name in skipped:
            print(f"{name}: no loop; not a function task")
        mode = f"replay of {replay} (zero requests)" if replay else f"live requests to {settings.base_url}"
        print(f"Preview: {mode}; model {settings.model}; protocol {settings.protocol}.")
        print("No files were written.")
        return True
    campaign_run = campaign.start(
        "proposer",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "condition": condition,
            "runs": runs,
            "attempts_per_run": ATTEMPTS,
            "timeout_ms": timeout_ms,
            "service": adapters.describe(settings),
            "mode": "replay" if replay else "live",
            "replay_of": str(replay) if replay else None,
            "transcripts": "full; repository programs only",
            "tasks": sorted(tasks),
            "skipped_without_loops": skipped,
        },
        schema=SCHEMA,
    )
    identity = uuid.uuid4().hex
    log = campaign_run.evidence.directory / "logs" / "proposals.jsonl"
    cache = campaign_run.evidence.directory / "cache"
    requests = 0

    def counted(request: adapters.Request) -> adapters.Exchange:
        nonlocal requests
        requests += 1
        return (send or adapters.send)(request)

    all_rows: list[dict[str, object]] = []
    consistent = True
    for name, program in sorted(tasks.items()):
        campaign_run.evidence.write_text(f"sources/{name}.c", ir.print_c(strip(program)))
    for number in range(1, runs + 1):
        for name, program in sorted(tasks.items()):
            if progress:
                progress(f"proposer {condition} run {number}/{runs}: {name}")
            if replay is not None:
                result = recheck(program, campaign_run.target, groups[(name, number)], timeout_ms=timeout_ms)
                consistent = consistent and replay_consistent(groups[(name, number)], result)
            else:
                result = run_task(
                    program,
                    campaign_run.target,
                    condition=condition,
                    settings=settings,
                    sample_id=f"{identity}/{condition}/run-{number}/{name}",
                    cache=cache,
                    live=True,
                    send=counted,
                    timeout_ms=timeout_ms,
                )
            for attempt in result.attempts:
                record(log, name, result.condition, attempt)
            for row in rows(
                name, program, number, result, origin="replay" if replay else None, target=campaign_run.target
            ):
                campaign_run.evidence.record("attempt", name, f"{result.condition}.run-{number}.{row['attempt']}", row)
                all_rows.append(row)
            final = result.attempts[-1].admission if result.attempts else None
            if result.success and final is not None and final.program is not None:
                campaign_run.evidence.write_text(f"proposals/run-{number}/{name}.c", ir.print_c(final.program))
    summary = summarize(all_rows, runs=runs, seed=seed)
    summary["requests"] = requests
    summary["replay_consistent"] = consistent if replay else None
    summary["passed"] = consistent and (replay is not None or requests >= 0)
    campaign.finish(campaign_run, summary, render_table(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run)
