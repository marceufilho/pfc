"""Parse invariant proposals as bounded data and recheck the complete program."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pfc import ir, smt, vc
from pfc.config import MAX_PROPOSAL_BYTES, PROPOSER_ATTEMPTS, SOLVER_TIMEOUT_MS
from pfc.diagnostics import FrontendError
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import SpecKind, parse_preprocessed
from pfc.propose.llm import Exchange, Request, Settings, complete, send_request
from pfc.target import Target
from pfc.wp import walk


@dataclass(frozen=True)
class Admission:
    accepted: bool
    program: ir.Program | None
    proof: smt.CheckReport | None
    problem: str | None = None
    retained: tuple[str, ...] = ()  # Emission reasons for guards that stayed, e.g. unproved arithmetic.
    arithmetic: tuple[str, ...] = ()  # Unproved overflow obligations that block the removals.


def loop_count(program: ir.Program) -> int:
    return sum(isinstance(s, (ir.While, ir.For)) for f in program.functions for s in walk(f.body))


def strip(program: ir.Program, *, placeholders: bool = False) -> ir.Program:
    """Remove every handwritten invariant, recursively including nested loops."""

    def block(body: ir.Block) -> ir.Block:
        statements: list[ir.Statement] = []
        for statement in body.statements:
            if isinstance(statement, (ir.While, ir.For)):
                invariant = None
                if placeholders:
                    invariant = ir.Contract(
                        SpecKind.INVARIANT,
                        ir.Literal(ir.ScalarType.INT, "0", 0, location=statement.location),
                        location=statement.location,
                    )
                statement = replace(statement, invariant=invariant, body=block(statement.body))
            elif isinstance(statement, ir.If):
                statement = replace(
                    statement,
                    then_body=block(statement.then_body),
                    else_body=block(statement.else_body) if statement.else_body else None,
                )
            elif isinstance(statement, ir.Block):
                statement = block(statement)
            statements.append(statement)
        return replace(body, statements=tuple(statements))

    return replace(program, functions=tuple(replace(f, body=block(f.body)) for f in program.functions))


def admit(program: ir.Program, reply: str, target: Target, *, timeout_ms: int = SOLVER_TIMEOUT_MS) -> Admission:
    """All loops receive a complete proposal; no handwritten annotation survives."""
    try:
        if len(reply.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ValueError("proposal exceeds 16384 bytes")
        data = json.loads(reply)
        if not isinstance(data, dict) or set(data) != {"invariants"}:
            raise ValueError("expected an object containing only invariants")
        expressions = data["invariants"]
        if not isinstance(expressions, list) or len(expressions) != loop_count(program) or not expressions:
            raise ValueError("expected one expression per loop, in preorder")
        for expression in expressions:
            if (
                not isinstance(expression, str)
                or not expression.strip()
                or not re.fullmatch(r"[a-zA-Z0-9_ ()\[\]+*/<>=!&|\-]+", expression)
                or any(token in expression for token in ("//", "/*", "*/"))
                or re.search(r"[a-zA-Z_]\w*\s*\(", expression)
            ):
                raise ValueError("invariants must be call-free C subset expressions")
        source = ir.print_c(strip(program, placeholders=True)).replace('#include "spec.h"', "").split("*/", 1)[1]
        parts = source.split("INVARIANT(0);")
        source = parts[0] + "".join(
            f"INVARIANT({expression});" + suffix for expression, suffix in zip(expressions, parts[1:], strict=True)
        )
        for macro in SpecKind:
            source = source.replace(str(macro) + "(", "__spec_" + str(macro).lower() + "(")
        candidate = lower(parse_preprocessed(source, "<proposal>"))
        proof = smt.check(candidate, target, timeout_ms=timeout_ms)
        emission = emit(candidate, target, mode="verified", proof=proof)
        accepted = proof.required_passed and all(d.removed for d in emission.decisions)
        statuses = {r.obligation.id: r.status for r in proof.results}
        return Admission(
            accepted,
            candidate,
            proof,
            retained=tuple(dict.fromkeys(d.reason for d in emission.decisions if not d.removed)),
            arithmetic=tuple(i for i in emission.arithmetic_dependencies if statuses.get(i) != "proved"),
        )
    except (ValueError, FrontendError, RecursionError) as error:
        return Admission(False, None, None, str(error))


@dataclass(frozen=True)
class Attempt:
    number: int
    request: Request
    exchange: Exchange | None
    admission: Admission | None
    error: str | None = None


@dataclass(frozen=True)
class Synthesis:
    found: bool
    attempts: tuple[Attempt, ...]
    baseline: Admission


def feedback(admission: Admission) -> str:
    """Only real failed formulas/models are feedback; timings do not perturb cache keys.

    A candidate can pass every required formula and still be rejected because a guard
    stays: overflow of arithmetic it made relevant, or an ambiguous site. Those reasons
    and the blocking overflow obligations are reported too, or the proposer retries blind.
    """
    if admission.problem:
        return admission.problem
    if admission.proof:
        blocking = {
            r.obligation.id
            for r in admission.proof.results
            if r.obligation.category == "required" and (r.status != "proved" or not r.dependencies_satisfied)
        } | set(admission.arithmetic)
        failures = [
            {
                "kind": r.obligation.kind,
                "goal": vc.render_term(r.obligation.goal),
                "hypotheses": [vc.render_term(h.formula) for h in r.obligation.hypotheses],
                "status": r.status,
                "counterexample": ir.to_data(r.counterexample),
            }
            for r in admission.proof.results
            if r.obligation.id in blocking
        ]
        return json.dumps({"retained_guards": list(admission.retained), "failures": failures[:3]}, sort_keys=True)
    return "No solver evidence available."


def synthesize(
    program: ir.Program,
    target: Target,
    *,
    settings: Settings,
    sample_id: str,
    cache: Path,
    log: Path,
    live: bool,
    send: Callable[[Request], Exchange] = send_request,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
) -> Synthesis:
    """At most five proposals; only admission can declare a candidate usable."""
    from pfc.corpus import profile

    name = profile(program)  # Full transcripts are restricted to reviewed research kernels.
    baseline = admit(program, json.dumps({"invariants": ["1"] * loop_count(program)}), target, timeout_ms=timeout_ms)
    source = ir.print_c(strip(program))
    instruction = (
        f"Propose inductive loop invariants for the curated C function {name}. "
        f"Return JSON with an invariants array of {loop_count(program)} expressions, one per loop in preorder. "
        "Use only call-free C integer/Boolean expressions over in-scope names. "
        "Do not change code or contracts. Prove initiation, preservation and bounds/division; "
        "annotation arithmetic is mathematical. No quantifiers. Mention only variables that the bounds and "
        "division proofs need: every C arithmetic operation on a mentioned variable must also be proved free of "
        "overflow, so avoid accumulators.\nFunction without handwritten invariants:\n" + source
    )
    history = "\nChecker feedback for true at every loop:\n" + feedback(baseline)
    attempts = []
    for number in range(1, PROPOSER_ATTEMPTS + 1):
        request = Request(instruction + history, f"{sample_id}/{number}", settings)
        exchange = None
        admission = None
        error = None
        try:
            exchange = complete(request, cache, live=live, send=send)
            admission = admit(program, exchange.text, target, timeout_ms=timeout_ms)
            history = "\nPrevious proposal:\n" + exchange.text + "\nChecker feedback:\n" + feedback(admission)
        except (ValueError, OSError) as problem:
            error = str(problem)
            history = "\nPrevious request failed. Produce a fresh proposal."
        attempt = Attempt(number, request, exchange, admission, error)
        attempts.append(attempt)
        record_attempt(log, name, attempt)
        if admission and admission.accepted:
            return Synthesis(True, tuple(attempts), baseline)
    return Synthesis(False, tuple(attempts), baseline)


def record_attempt(log: Path, name: str, attempt: Attempt) -> None:
    request, exchange, admission = attempt.request, attempt.exchange, attempt.admission
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        json.dump(
            {
                "vc_model": vc.MODEL,
                "smt_encoding": smt.ENCODING,
                "sample_id": request.sample_id,
                "kernel": name,
                "attempt": attempt.number,
                "settings": ir.to_data(request.settings),
                "prompt": request.prompt,
                "exchange": ir.to_data(exchange),
                "accepted": bool(admission and admission.accepted),
                "proof": ir.to_data(admission.proof) if admission else None,
                "problem": admission.problem if admission else attempt.error,
            },
            stream,
            sort_keys=True,
        )
        stream.write("\n")
