"""B1: bounded Houdini-style elimination over a fixed template family (roadmap E6, design §5, architecture D4).

Candidates are enumerated per loop over the scalar variables and length parameters in scope. Each
refinement round builds one probe program: every loop carries the conjunction of its current candidates
as `INVARIANT`, and every candidate appears twice as an `ASSERT`, once immediately before the loop
(initiation under the entry context) and once at the end of the loop body (preservation under the whole
current conjunction at the loop head, the guard and the body). One frozen `smt.check` per round yields one
`assert` obligation per candidate and phase. A candidate is dropped only when its obligation fails with a
genuine model; timeout, unknown and error keep it and are counted. Rounds stop at a fixpoint (no removal)
or at the bound, which is reported as a round limit, not a fixpoint. Final success always goes through the
unchanged `admit()`; the exploratory queries confer no authority.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import z3

from pfc import ir, smt, vc, workflow
from pfc.config import DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.diagnostics import FrontendError
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import SpecKind, parse_preprocessed, parse_source
from pfc.propose.invariants import Admission, admit, strip
from pfc.target import Target
from pfc_eval import campaign, daikon, evidence, expressions, fuzz, generate, inject

FAMILY_TEXT = "`c <= v`, `v <= w + c` over the loop's scalar variables and length parameters, c ∈ {−1, 0, 1}"
CONSTANTS = (-1, 0, 1)
MAX_ROUNDS = 5
CHUNK = 24  # Candidates asserted per probe; bounds the VC term budget of one check while the conjunction stays whole.
PROBE_PARAMETER = "pfc_probe"
Phase = Literal["initiation", "preservation"]
Status = Literal["fixpoint", "inconclusive", "round-limit"]
loops = inject.loops
render = expressions.render

SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("function", "string"),
        evidence.Column("loop_index", "integer"),
        evidence.Column("loop_line", "integer"),
        evidence.Column("candidates_initial", "integer"),
        evidence.Column("candidates_remaining", "integer"),
        evidence.Column("removed_initiation", "integer"),
        evidence.Column("removed_preservation", "integer"),
        evidence.Column("rounds", "integer"),
        evidence.Column("status", "string", allowed=("fixpoint", "inconclusive", "round-limit")),
        evidence.Column("timeouts", "integer"),
        evidence.Column("unknown", "integer"),
        evidence.Column("errors", "integer"),
        evidence.Column("probe_splits", "integer"),
        evidence.Column("invariant", "string"),
        evidence.Column("hand_invariant", "string"),
        evidence.Column("hand_in_family", "boolean"),
        evidence.Column("task_accepted", "boolean"),
        evidence.Column("rejection_class", "string"),
        evidence.Column("queries", "integer"),
        evidence.Column("solver_ms", "number"),
        evidence.Column("family", "string", allowed=("b1", "b2")),
        evidence.Column("states_observed", "integer", nullable=True),
        evidence.Column("candidates_truncated", "integer", nullable=True),
        evidence.Column("guards_total", "integer", nullable=True),
        evidence.Column("guards_removed", "integer", nullable=True),
    )
)


@dataclass(frozen=True)
class Probe:
    program: ir.Program
    table: dict[int, tuple[int, int, Phase]]  # source line -> (loop, candidate index, phase)


@dataclass(frozen=True)
class Round:
    number: int
    removed: dict[int, tuple[str, ...]]
    failed_initiation: dict[int, tuple[str, ...]]
    failed_preservation: dict[int, tuple[str, ...]]
    timeouts: int
    unknown: int
    errors: int
    solver_ms: float
    remaining: tuple[int, ...]


@dataclass(frozen=True)
class KernelHoudini:
    kernel: str
    initial: tuple[tuple[str, ...], ...]
    remaining: tuple[tuple[str, ...], ...]
    rounds: tuple[Round, ...]
    status: Status
    invariants: tuple[str, ...]
    hand_invariants: tuple[str, ...]
    queries: int
    solver_ms: float
    accepted: bool
    rejection_class: str
    admission: Admission
    probe_splits: int = 0


def candidates(function: ir.Function) -> tuple[tuple[str, ...], ...]:
    """The fixed family per loop, in a deterministic order: `c <= v`, then `v <= w + c` with v != w."""
    family = []
    for loop in loops(function):
        names = [symbol.name for symbol in loop.scope]
        texts = [f"({c} <= {v})" for v in names for c in CONSTANTS]
        for v in names:
            for w in names:
                if v == w:
                    continue
                for c in CONSTANTS:
                    bound = w if c == 0 else f"({w} + 1)" if c == 1 else f"({w} - 1)"
                    texts.append(f"({v} <= {bound})")
        family.append(tuple(dict.fromkeys(texts)))
    return tuple(family)


def _conjoin(parts: Sequence[str]) -> str:
    """Balanced conjunction: nesting depth grows with log2(n), within the frontend's AST depth limit."""
    if not parts:
        return "1"
    if len(parts) == 1:
        return parts[0]
    middle = (len(parts) + 1) // 2
    return f"({_conjoin(parts[:middle])} && {_conjoin(parts[middle:])})"


def _relocate(block: ir.Block, counts: Sequence[int], counter: list[int]) -> ir.Block:
    """Move each loop's probe asserts: the first n before the loop, the next n to the end of its body."""
    statements: list[ir.Statement] = []
    for statement in block.statements:
        if isinstance(statement, (ir.While, ir.For)):
            index = counter[0]
            counter[0] += 1
            n = counts[index]
            body = statement.body.statements
            asserts = body[: 2 * n]
            if any(not (isinstance(a, ir.Contract) and a.kind is SpecKind.ASSERT) for a in asserts):
                raise ValueError("probe body does not start with the expected ASSERT statements")
            rest = tuple(body[2 * n :])
            if isinstance(statement, ir.For):
                # Preservation must hold after the update. A `for` becomes the equivalent `while` whose body
                # ends with the update, so the preservation asserts observe the post-update state.
                if statement.initializer is not None:
                    statements.append(statement.initializer)
                update = (statement.update,) if statement.update is not None else ()
                condition = statement.condition
                if condition is None:
                    condition = ir.Literal(ir.ScalarType.INT, "1", 1, location=statement.location)
                inner = ir.Block(rest + update + tuple(asserts[n:]), location=statement.body.location)
                inner = _relocate(inner, counts, counter)
                statements.extend(asserts[:n])
                statements.append(ir.While(condition, statement.invariant, inner, location=statement.location))
                continue
            inner = ir.Block(rest + tuple(asserts[n:]), location=statement.body.location)
            inner = _relocate(inner, counts, counter)
            statements.extend(asserts[:n])
            statements.append(replace(statement, body=inner))
        elif isinstance(statement, ir.If):
            then_body = _relocate(statement.then_body, counts, counter)
            else_body = _relocate(statement.else_body, counts, counter) if statement.else_body else None
            statements.append(replace(statement, then_body=then_body, else_body=else_body))
        elif isinstance(statement, ir.Block):
            statements.append(_relocate(statement, counts, counter))
        else:
            statements.append(statement)
    return replace(block, statements=tuple(statements))


def build_probe(
    program: ir.Program,
    function: str,
    current: Mapping[int, Sequence[str]],
    asserted: Mapping[int, Sequence[int]] | None = None,
) -> Probe:
    """Probe program for one chunk: INVARIANT(whole conjunction) per loop plus two ASSERTs per asserted candidate."""
    entry = next(f for f in program.functions if f.name == function)
    count = len(loops(entry))
    source = ir.print_c(strip(program, placeholders=True)).replace('#include "spec.h"', "").split("*/", 1)[1]
    source = _with_probe_parameter(source, entry)
    parts = source.split("INVARIANT(0);")
    if len(parts) - 1 != count:
        raise ValueError("placeholder count differs from the loop count")
    text = parts[0]
    table: dict[int, tuple[int, int, Phase]] = {}
    counts = []
    position = 0
    for loop_index in range(count):
        texts = list(current.get(loop_index, ()))
        chosen = list(asserted.get(loop_index, ())) if asserted is not None else list(range(len(texts)))
        counts.append(len(chosen))
        text += f"INVARIANT({_conjoin(texts)});"
        phase: Phase
        for phase in ("initiation", "preservation"):
            for candidate_index in chosen:
                # The frozen WP assumes each ASSERT afterwards. Guarding by a fresh parameter keeps the queries
                # independent: refuting candidate j needs pfc_probe == j, under which every other guarded assert
                # is trivially true, so no refuted or inconsistent candidate can make a later check vacuous.
                text += "\n"
                table[text.count("\n") + 1] = (loop_index, candidate_index, phase)
                text += f"ASSERT({PROBE_PARAMETER} != {position} || {texts[candidate_index]});"
                position += 1
        text += parts[loop_index + 1]
    for macro in SpecKind:
        text = text.replace(str(macro) + "(", "__spec_" + str(macro).lower() + "(")
    lowered = lower(parse_preprocessed(text, "<houdini>"))
    functions = tuple(
        replace(f, body=_relocate(f.body, counts, [0])) if f.name == function else f for f in lowered.functions
    )
    return Probe(replace(lowered, functions=functions), table)


def _with_probe_parameter(source: str, entry: ir.Function) -> str:
    """Add the fresh `long pfc_probe` parameter to every declaration of the probed function."""
    parameters = ", ".join(ir._declaration(p.symbol) for p in entry.parameters) or "void"
    header = f"{entry.return_type} {entry.name}({parameters})"
    if header not in source:
        raise ValueError("cannot locate the probed function header")
    replacement = (
        f"{entry.return_type} {entry.name}({parameters + ', ' if parameters != 'void' else ''}long {PROBE_PARAMETER})"
    )
    return source.replace(header, replacement)


def run_kernel(
    program: ir.Program,
    target: Target,
    *,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    candidates: Mapping[int, Sequence[str]] | None = None,
    max_rounds: int = MAX_ROUNDS,
    solver_factory: Callable[[], smt.Solver] = z3.Solver,
    function: str | None = None,
    generate: Callable[[ir.Program], vc.VCReport] = vc.generate,
) -> KernelHoudini:
    """Bounded elimination rounds, then the frozen admission of the surviving conjunctions.

    A probe the frozen generator refuses with `vc-complexity` (two-loop programs with 24 asserted candidates
    exceed its cumulative formula cap) is split in halves and retried; candidates are independent in the probe
    (each is isolated by the probe parameter), so splitting changes cost, never verdicts. A single-candidate
    probe still over the cap counts as one error and keeps its candidate. Splits are recorded.
    """
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    found = loops(entry)
    if not found:
        raise ValueError("B1 needs at least one loop")
    if type(max_rounds) is not int or max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer")
    family = candidates if candidates is not None else {k: list(texts) for k, texts in enumerate(self_family(entry))}
    current: dict[int, list[str]] = {k: list(family.get(k, ())) for k in range(len(found))}
    initial = tuple(tuple(current[k]) for k in range(len(found)))
    rounds: list[Round] = []
    status: Status = "round-limit"
    solver_ms = 0.0
    queries = 0
    exploratory = smt.SolverCache()
    version = smt.solver_version()
    probe_splits = 0
    for number in range(1, max_rounds + 1):
        removed: dict[int, list[str]] = {}
        failed: dict[Phase, dict[int, list[str]]] = {"initiation": {}, "preservation": {}}
        timeouts = unknown = errors = 0
        round_ms = 0.0
        pairs = [(k, i) for k in range(len(found)) for i in range(len(current[k]))]
        pending = [pairs[j : j + CHUNK] for j in range(0, len(pairs), CHUNK)] or [[]]
        while pending:
            chunk = pending.pop(0)
            asserted: dict[int, list[int]] = {}
            for k, i in chunk:
                asserted.setdefault(k, []).append(i)
            probe = build_probe(program, name, current, asserted)
            # Only the candidate asserts are solved; the probe's other obligations are not evaluated at all.
            # A fresh exploratory cache is used so nothing here ever reaches the compiler's own results.
            try:
                conditions = generate(probe.program)
            except FrontendError as error:
                if error.diagnostic.rule != "vc-complexity":
                    raise
                if len(chunk) > 1:
                    middle = len(chunk) // 2
                    pending[:0] = [chunk[:middle], chunk[middle:]]
                    probe_splits += 1
                else:
                    errors += 1
                continue
            queries += 1
            for obligation in conditions.obligations:
                if obligation.kind != "assert" or obligation.location.line not in probe.table:
                    continue
                result = exploratory.solve(obligation, target, timeout_ms, version, solver_factory)
                round_ms += result.elapsed_ms
                loop_index, candidate_index, phase = probe.table[result.obligation.location.line]
                text = current[loop_index][candidate_index]
                if result.status == "failed" and result.counterexample is not None:
                    # A site reached through several structured paths yields several obligations (ADR-0023).
                    if text not in failed[phase].setdefault(loop_index, []):
                        failed[phase][loop_index].append(text)
                    if text not in removed.setdefault(loop_index, []):
                        removed[loop_index].append(text)
                elif result.status == "timeout":
                    timeouts += 1
                elif result.status == "unknown":
                    unknown += 1
                elif result.status == "error":
                    errors += 1
        solver_ms += round_ms
        for loop_index, texts in removed.items():
            current[loop_index] = [c for c in current[loop_index] if c not in texts]
        rounds.append(
            Round(
                number,
                {k: tuple(v) for k, v in sorted(removed.items())},
                {k: tuple(v) for k, v in sorted(failed["initiation"].items())},
                {k: tuple(v) for k, v in sorted(failed["preservation"].items())},
                timeouts,
                unknown,
                errors,
                round_ms,
                tuple(len(current[k]) for k in range(len(found))),
            )
        )
        if not removed:
            # No removal ends the elimination; it is a fixpoint only when every query was decided.
            status = "fixpoint" if timeouts == unknown == errors == 0 else "inconclusive"
            break
    invariants = tuple(_conjoin(current[k]) for k in range(len(found)))
    admission = admit(program, json.dumps({"invariants": list(invariants)}), target, timeout_ms=timeout_ms)
    queries += 1
    if admission.proof is not None:
        solver_ms += sum(r.elapsed_ms for r in admission.proof.results)
    invariant_proved = admission.proof is not None and all(
        r.status == "proved" and r.dependencies_satisfied
        for r in admission.proof.results
        if r.obligation.category == "required" and r.obligation.kind in ("entry", "preservation")
    )
    return KernelHoudini(
        name,
        initial,
        tuple(tuple(current[k]) for k in range(len(found))),
        tuple(rounds),
        status,
        invariants,
        tuple(render(loop.invariant.condition) for loop in found),
        queries,
        solver_ms,
        admission.accepted,
        inject.rejection_class(admission, invariant_proved),
        admission,
        probe_splits,
    )


def self_family(function: ir.Function) -> tuple[tuple[str, ...], ...]:
    return candidates(function)


def _family_form(part: ir.Expr) -> str:
    """Text of a hand conjunct in the family's spelling: strict comparisons become `<=` with a −1 bound."""
    inner = expressions.strip_convert(part)
    if isinstance(inner, ir.Binary) and inner.operator in ("<", ">"):
        left, right = (inner.left, inner.right) if inner.operator == "<" else (inner.right, inner.left)
        right_text = render(right)
        bound = f"({right_text} - 1)" if isinstance(expressions.strip_convert(right), ir.Variable) else right_text
        return f"({render(left)} <= {bound})" if bound != right_text else render(part)
    return render(part)


def _hand_in_family(loop: inject.Loop, family: Sequence[str]) -> bool:
    """Every conjunct of the hand invariant is a member of the enumerated family (a syntactic check up to `<`)."""
    return all(_family_form(part) in family for part in expressions.flatten(loop.invariant.condition))


def rows(
    result: KernelHoudini,
    kernel: str,
    function: ir.Function,
    *,
    family: str = "b1",
    states: Mapping[int, int] | None = None,
    truncated: Mapping[int, int] | None = None,
    target: Target | None = None,
) -> list[dict[str, object]]:
    guards_total: int | None = None
    guards_removed: int | None = None
    if target is not None:
        program_of_function = ir.Program((function,))
        guards_total = len(emit(program_of_function, target, mode="checked").decisions)
        admission = result.admission
        if admission.program is not None and admission.proof is not None:
            verified = emit(admission.program, target, mode="verified", proof=admission.proof)
            guards_removed = sum(d.removed for d in verified.decisions)
        else:
            guards_removed = 0
    table: list[dict[str, object]] = []
    for loop in loops(function):
        k = loop.index
        table.append(
            {
                "kernel": kernel,
                "function": result.kernel,
                "loop_index": k,
                "loop_line": loop.location.line,
                "candidates_initial": len(result.initial[k]),
                "candidates_remaining": len(result.remaining[k]),
                "removed_initiation": sum(len(r.failed_initiation.get(k, ())) for r in result.rounds),
                "removed_preservation": sum(len(r.failed_preservation.get(k, ())) for r in result.rounds),
                "rounds": len(result.rounds),
                "status": result.status,
                "timeouts": sum(r.timeouts for r in result.rounds),
                "unknown": sum(r.unknown for r in result.rounds),
                "errors": sum(r.errors for r in result.rounds),
                "probe_splits": result.probe_splits,
                "invariant": result.invariants[k],
                "hand_invariant": result.hand_invariants[k],
                "hand_in_family": _hand_in_family(loop, result.initial[k]),
                "task_accepted": result.accepted,
                "rejection_class": result.rejection_class,
                "queries": result.queries,
                "solver_ms": result.solver_ms,
                "family": family,
                "states_observed": states.get(k) if states is not None else None,
                "candidates_truncated": truncated.get(k) if truncated is not None else None,
                "guards_total": guards_total,
                "guards_removed": guards_removed,
            }
        )
    return table


FAMILIES = {"b1": FAMILY_TEXT, "b2": daikon.FAMILY_B2_TEXT}


def _profile(name: str, profiles_dir: Path | None) -> generate.Profile | None:
    """The reviewed generation profile of a kernel when one exists; B2 adds profile draws only then."""
    try:
        if profiles_dir is None:
            return generate.committed_profile(name)
        path = profiles_dir / f"{name}.json"
        return generate.load_profile(path) if path.is_file() else None
    except (ValueError, OSError):
        return None


def summarize(
    results: Mapping[str, KernelHoudini], *, max_rounds: int = MAX_ROUNDS, family: str = "b1"
) -> dict[str, object]:
    kernels: dict[str, object] = {}
    for name, result in results.items():
        kernels[name] = {
            "loops": len(result.initial),
            "candidates_initial": [len(c) for c in result.initial],
            "candidates_remaining": [len(c) for c in result.remaining],
            "rounds": len(result.rounds),
            "status": result.status,
            "timeouts": sum(r.timeouts for r in result.rounds),
            "unknown": sum(r.unknown for r in result.rounds),
            "errors": sum(r.errors for r in result.rounds),
            "probe_splits": result.probe_splits,
            "queries": result.queries,
            "solver_ms": result.solver_ms,
            "accepted": result.accepted,
            "rejection_class": result.rejection_class,
            "invariants": list(result.invariants),
        }
    return {
        "experiment": "houdini",
        "family": FAMILIES[family],
        "family_label": family,
        "max_rounds": max_rounds,
        "preregistered_max_rounds": MAX_ROUNDS,
        "kernels": kernels,
        "success_at_1": sum(r.accepted for r in results.values()),
        "tasks": len(results),
    }


def render_table(summary: Mapping[str, object]) -> str:
    lines = [
        "| kernel | loops | candidates (initial → remaining) | rounds | status | timeouts/unknown/errors | queries "
        "| probe splits | solver ms | admitted |",
        "| --- | ---: | --- | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    for name, e in kernels.items():
        pairs = ", ".join(f"{a} → {b}" for a, b in zip(e["candidates_initial"], e["candidates_remaining"], strict=True))
        lines.append(
            f"| {name} | {e['loops']} | {pairs} | {e['rounds']} | {e['status']} | "
            f"{e['timeouts']}/{e['unknown']}/{e['errors']} | {e['queries']} | {e.get('probe_splits', 0)} | "
            f"{e['solver_ms']:.1f} | "
            f"{'yes' if e['accepted'] else 'no (' + e['rejection_class'] + ')'} |"
        )
    lines.append("")
    bound = summary.get("max_rounds", MAX_ROUNDS)
    amendment = (
        "" if bound == MAX_ROUNDS else f" (exploratory sensitivity run; the pre-registered bound is {MAX_ROUNDS})"
    )
    label = str(summary.get("family_label", "b1")).upper()
    lines.append(
        f"{label} family: {summary.get('family', FAMILY_TEXT)}; at most {bound} refinement rounds{amendment}. "
        "A round limit is not a fixpoint; "
        "a round without removals but with undecided queries is inconclusive. "
        "Only a failed obligation with a model removes a candidate; the surviving conjunction is admitted or rejected "
        "by the unchanged frozen admission, which alone defines success. A probe over the frozen VC size cap is split "
        "in halves (recorded as probe splits); a single-candidate probe over the cap is an error."
    )
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "loop"]


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
    max_rounds: int = MAX_ROUNDS,
    progress: Callable[[str], None] | None = None,
    family: str = "b1",
    seed: int = 0,
    profiles_dir: Path | None = None,
) -> bool:
    if family not in FAMILIES:
        raise ValueError(f"unknown candidate family {family!r}; choose b1 or b2")
    sources = workflow.discover(tuple(directories))
    programs = [(source.parent.name, lower(parse_source(source, cpp=cpp))) for source in sources]
    if dry_run:
        for name, program in programs:
            if family == "b1":
                counts = [len(c) for c in candidates(program.functions[0])]
                print(f"{name}: {len(counts)} loop(s); candidates per loop {counts}")
            else:
                print(f"{name}: {len(loops(program.functions[0]))} loop(s); B2 candidates come from sampled states")
        print(
            f"Preview: family {FAMILIES[family]}; at most {max_rounds} rounds; {CHUNK} asserted candidates per probe."
        )
        if max_rounds != MAX_ROUNDS:
            print(f"Amendment: the pre-registered bound is {MAX_ROUNDS} rounds; this run is exploratory.")
        print(f"Solver timeout {timeout_ms} ms per obligation.")
        print("No files were written.")
        return True
    run = campaign.start(
        "houdini",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=None,
        settings={
            "timeout_ms": timeout_ms,
            "family": FAMILIES[family],
            "family_label": family,
            "constants": list(CONSTANTS),
            "b2": (
                {
                    "seed": seed,
                    "samples_fuzz": daikon.SAMPLES_FUZZ,
                    "samples_profile": daikon.SAMPLES_PROFILE,
                    "min_states": daikon.MIN_STATES,
                    "constant_bound": daikon.CONSTANT_BOUND,
                    "cap": daikon.CAP,
                    "multipliers": list(daikon.MULTIPLIERS),
                    "fuzz_domain": fuzz.domain(),
                }
                if family == "b2"
                else None
            ),
            "max_rounds": max_rounds,
            "preregistered_max_rounds": MAX_ROUNDS,
            "chunk": CHUNK,
            "probe_parameter": PROBE_PARAMETER,
            "kernels": [name for name, _ in programs],
        },
        schema=SCHEMA,
    )
    results: dict[str, KernelHoudini] = {}
    for name, program in programs:
        if progress:
            progress(f"houdini: {name}")
        states: dict[int, int] | None = None
        truncated: dict[int, int] | None = None
        chosen: dict[int, list[str]] | None = None
        if family == "b2":
            inferred = daikon.family(program, run.target, seed=seed, profile=_profile(name, profiles_dir))
            chosen, states, truncated = inferred.candidates, inferred.states_observed, inferred.truncated
            run.evidence.record(
                "states",
                name,
                "summary",
                {
                    "runs": inferred.runs,
                    "accepted": inferred.accepted,
                    "sources": inferred.sources,
                    "states_observed": {str(k): v for k, v in states.items()},
                    "candidates": {str(k): v for k, v in chosen.items()},
                    "truncated": {str(k): v for k, v in truncated.items()},
                },
            )
        result = run_kernel(program, run.target, timeout_ms=timeout_ms, max_rounds=max_rounds, candidates=chosen)
        results[name] = result
        for row in rows(
            result, name, program.functions[0], family=family, states=states, truncated=truncated, target=run.target
        ):
            run.evidence.record("loop", name, str(row["loop_index"]), row)
        for item in result.rounds:
            run.evidence.record(
                "round",
                name,
                f"{item.number:02d}",
                {
                    "removed": {str(k): list(v) for k, v in item.removed.items()},
                    "failed_initiation": {str(k): list(v) for k, v in item.failed_initiation.items()},
                    "failed_preservation": {str(k): list(v) for k, v in item.failed_preservation.items()},
                    "timeouts": item.timeouts,
                    "unknown": item.unknown,
                    "errors": item.errors,
                    "solver_ms": item.solver_ms,
                    "remaining": list(item.remaining),
                },
            )
        if result.admission.program is not None:
            run.evidence.write_text(f"sources/{name}-{family}-candidate.c", ir.print_c(result.admission.program))
            if result.accepted and result.admission.proof is not None:
                verified = emit(result.admission.program, run.target, mode="verified", proof=result.admission.proof)
                run.evidence.write_text(f"sources/{name}-{family}-verified.c", verified.c)
    summary = summarize(results, max_rounds=max_rounds, family=family)
    campaign.finish(run, summary, render_table(summary), derive_rows)
    return all(r.accepted for r in results.values())


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
