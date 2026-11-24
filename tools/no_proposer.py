"""The no-proposer control.

How many executable guard sites does the frozen compiler remove when nobody proposes an invariant? The compiler
refuses a loop without one, so "nobody" is the trivial invariant `1` at every loop, sent through the frozen
`admit()` like any other candidate. Beside it, the same program with the invariant written in its file (the hand
invariant of a development kernel; the mechanical counter bounds, B0, of a held-out port). The difference between a
method's removed sites and the trivial row is what that method's invariants add to the compilation.

Evaluation only: no model, no network, deterministic for a fixed toolchain. Writes a sealed evidence directory.

    uv run --locked python tools/no_proposer.py heldout --output out/no-proposer
    uv run --locked python tools/no_proposer.py kernels bench/external/acsl --output NEW --dry-run

`--rule counter` adds a second mechanical source: the syntactic counter-bound rule `COUNTER_RULE`, fixed in this
file before any run and recorded in the configuration. Its candidates go through the same `admit()`.

    uv run --locked python tools/no_proposer.py bench/external/zilu --rule counter --output NEW
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pfc import ir, smt  # noqa: E402
from pfc.config import CLANG_ENV_VAR, CPP_ENV_VAR, DEFAULT_CLANG, DEFAULT_CPP, SOLVER_TIMEOUT_MS  # noqa: E402
from pfc.emit import emit  # noqa: E402
from pfc.lower import lower  # noqa: E402
from pfc.parse import parse_source  # noqa: E402
from pfc.propose.invariants import admit, loop_count  # noqa: E402
from pfc.target import Target  # noqa: E402
from pfc.wp import walk  # noqa: E402
from pfc_eval import campaign, evidence, heldout  # noqa: E402
from pfc_eval.expressions import render, strip_convert  # noqa: E402
from pfc_eval.proposer import guard_counts  # noqa: E402

EXPERIMENT = "no-proposer"
SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("loops", "integer"),
        evidence.Column("guards_total", "integer"),
        evidence.Column("trivial_accepted", "boolean"),
        evidence.Column("trivial_removed", "integer"),
        evidence.Column("trivial_problem", "string", nullable=True),
        evidence.Column("written_accepted", "boolean"),
        evidence.Column("written_removed", "integer"),
    )
)
COUNTER_SCHEMA = evidence.Schema(
    SCHEMA.columns
    + (
        evidence.Column("counter_accepted", "boolean"),
        evidence.Column("counter_matched_loops", "integer"),
        evidence.Column("counter_problem", "string", nullable=True),
        evidence.Column("counter_invariants", "string"),
    )
)
RULES = ("counter",)
COUNTER_RULE = (
    "Syntactic counter bound, one candidate per loop in preorder, no solver and no execution. A while/for loop "
    "matches when (a) its condition, conversions aside, is a single comparison `i OP e` with OP in {<, <=, >, >=, !=} "
    "(or the mirrored `e OP i`, read as `i OP' e`; the left operand is tried as the counter first) where `i` is a "
    "scalar local or parameter and `e` has no array read, no call, no occurrence of `i` and no variable assigned "
    "inside the loop; (b) the loop (body at any depth, plus a for update) assigns `i` exactly once, at the top level "
    "of the body or as the for update, as `i = i + c` or `i = i - c` with a positive literal `c`; (c) the initial "
    "value `i0` is the for initializer when it sets `i`, otherwise the last top-level initializer or assignment of "
    "`i` before the loop in the enclosing block, with no nested statement assigning `i` after it, and `i0` is a "
    "literal (possibly negated) or an expression with no array read, no call, no occurrence of `i` and no variable "
    "assigned inside the loop or between that statement and the loop. Candidate, increasing counter (`i + c`): "
    "`<` or `!=` gives `i0 <= i && i <= e`; `<=` gives `i0 <= i && i <= e + 1` when c == 1, else `i0 <= i`. "
    "Decreasing counter (`i - c`): `>` or `!=` gives `e <= i && i <= i0`; `>=` gives `e - 1 <= i && i <= i0` when "
    "c == 1, else `i <= i0`. Any other loop (including an increasing counter under `>`/`>=` or a decreasing one "
    "under `<`/`<=`, a conjunction as condition, or a counter whose initial value comes only from REQUIRES) does "
    "not match and gets `1`. Texts use pfc_eval.expressions.render. The candidate is only a proposal: the frozen "
    "admit() decides."
)
_MIRROR = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "!=": "!="}
Loop = ir.While | ir.For


def _assigned(statements: Sequence[ir.Node]) -> list[str]:
    """Symbol ids written by these statements (scalars and arrays), one entry per writing statement."""
    found: list[str] = []
    for statement in statements:
        if isinstance(statement, ir.Local):
            found.append(statement.symbol.id)
        elif isinstance(statement, ir.Assign):
            target = statement.target
            found.append(target.symbol.id if isinstance(target, ir.Variable) else target.array.id)
    return found


def _nested(statement: ir.Node) -> tuple[ir.Node, ...]:
    return walk(ir.Block(statements=(statement,), location=statement.location))  # type: ignore[arg-type]


def _names(expression: ir.Expr) -> set[str] | None:
    """Symbol ids read by a scalar expression; None when it reads an array or calls."""
    expression = strip_convert(expression)
    if isinstance(expression, ir.Literal):
        return set()
    if isinstance(expression, ir.Variable):
        return {expression.symbol.id}
    if isinstance(expression, ir.Unary):
        return _names(expression.operand)
    if isinstance(expression, ir.Binary):
        left, right = _names(expression.left), _names(expression.right)
        return None if left is None or right is None else left | right
    return None


def _is_literal(expression: ir.Expr) -> bool:
    expression = strip_convert(expression)
    if isinstance(expression, ir.Unary) and expression.operator == "-":
        expression = strip_convert(expression.operand)
    return isinstance(expression, ir.Literal)


def _step(loop: Loop, counter: ir.Symbol) -> int | None:
    """Signed literal step of the counter's single top-level update; None when the loop does not match (b)."""
    update = (loop.update,) if isinstance(loop, ir.For) and loop.update else ()
    if _assigned(walk(loop.body) + update).count(counter.id) != 1:
        return None
    for statement in loop.body.statements + update:
        if not isinstance(statement, ir.Assign) or _assigned((statement,)) != [counter.id]:
            continue
        value = strip_convert(statement.value)
        if not isinstance(value, ir.Binary) or value.operator not in {"+", "-"}:
            return None
        left, right = strip_convert(value.left), strip_convert(value.right)
        if not isinstance(left, ir.Variable) or left.symbol.id != counter.id:
            return None
        if not isinstance(right, ir.Literal) or right.value <= 0:
            return None
        return right.value if value.operator == "+" else -right.value
    return None  # The single assignment is nested in a branch or an inner loop.


def _initial(loop: Loop, before: Sequence[ir.Statement], counter: ir.Symbol, written: set[str]) -> ir.Expr | None:
    """Initial value by clause (c); `written` holds the ids assigned inside the loop."""
    value: ir.Expr | None = None
    between = set(written)
    initializer = loop.initializer if isinstance(loop, ir.For) else None
    if initializer is not None and _assigned((initializer,)) == [counter.id]:
        value = initializer.initializer if isinstance(initializer, ir.Local) else initializer.value
    else:
        if initializer is not None:
            between |= set(_assigned((initializer,)))
        for statement in reversed(before):
            if isinstance(statement, (ir.Local, ir.Assign)) and _assigned((statement,)) == [counter.id]:
                value = statement.initializer if isinstance(statement, ir.Local) else statement.value
                break
            inner = set(_assigned(_nested(statement)))
            if counter.id in inner:
                return None
            between |= inner
    if value is None:
        return None
    if _is_literal(value):
        return value
    names = _names(value)
    return value if names is not None and counter.id not in names and not names & between else None


def _candidate(loop: Loop, before: Sequence[ir.Statement]) -> str | None:
    if loop.condition is None:
        return None
    condition = strip_convert(loop.condition)
    if not isinstance(condition, ir.Binary) or condition.operator not in _MIRROR:
        return None
    update = (loop.update,) if isinstance(loop, ir.For) and loop.update else ()
    written = set(_assigned(walk(loop.body) + update))
    sides = ((condition.left, condition.right, condition.operator), (condition.right, condition.left, None))
    for variable, limit, operator in sides:
        variable = strip_convert(variable)
        operator = operator or _MIRROR[condition.operator]
        if not isinstance(variable, ir.Variable) or not isinstance(variable.symbol.type, ir.ScalarType):
            continue
        counter = variable.symbol
        names = _names(limit)
        if names is None or counter.id in names or names & written:
            continue
        step = _step(loop, counter)
        start = _initial(loop, before, counter, written) if step is not None else None
        if step is None or start is None:
            continue
        i, e, i0 = counter.name, render(limit), render(start)
        if step > 0 and operator in {"<", "!="}:
            return f"{i0} <= {i} && {i} <= {e}"
        if step > 0 and operator == "<=":
            return f"{i0} <= {i} && {i} <= {e} + 1" if step == 1 else f"{i0} <= {i}"
        if step < 0 and operator in {">", "!="}:
            return f"{e} <= {i} && {i} <= {i0}"
        if step < 0 and operator == ">=":
            return f"{e} - 1 <= {i} && {i} <= {i0}" if step == -1 else f"{i} <= {i0}"
    return None


def counter_invariants(program: ir.Program) -> list[str]:
    """One candidate per loop in the preorder of `loop_count`; `1` where `COUNTER_RULE` does not match."""
    found: list[str] = []

    def block(body: ir.Block) -> None:
        for index, statement in enumerate(body.statements):
            if isinstance(statement, (ir.While, ir.For)):
                found.append(_candidate(statement, body.statements[:index]) or "1")
                block(statement.body)
            elif isinstance(statement, ir.If):
                block(statement.then_body)
                if statement.else_body:
                    block(statement.else_body)
            elif isinstance(statement, ir.Block):
                block(statement)

    for function in program.functions:
        block(function.body)
    if len(found) != loop_count(program):
        raise ValueError("counter rule and loop_count disagree on the number of loops")
    return found


def measure(
    name: str, program: ir.Program, target: Target, *, timeout_ms: int = SOLVER_TIMEOUT_MS, rule: str | None = None
) -> dict[str, object]:
    """One program under the trivial invariant and under the invariant its file carries (and, on request, a rule)."""
    row = _measure(name, program, target, timeout_ms)
    if rule == "counter":
        invariants = counter_invariants(program)
        if invariants:
            result = admit(program, json.dumps({"invariants": invariants}), target, timeout_ms=timeout_ms)
            accepted, problem = result.accepted, result.problem
        else:  # admit() refuses an empty proposal; a loop-free program has nothing to propose.
            accepted, problem = bool(row["written_accepted"]), None
        row |= {
            "counter_accepted": accepted,
            "counter_matched_loops": sum(invariant != "1" for invariant in invariants),
            "counter_problem": problem,
            "counter_invariants": " ;; ".join(invariants),
        }
    elif rule is not None:
        raise ValueError(f"unknown rule {rule}")
    return row


def _measure(name: str, program: ir.Program, target: Target, timeout_ms: int) -> dict[str, object]:
    loops = loop_count(program)
    trivial = admit(program, json.dumps({"invariants": ["1"] * loops}), target, timeout_ms=timeout_ms)
    total, removed = guard_counts(program, trivial, target)
    proof = smt.check(program, target, timeout_ms=timeout_ms)
    decisions = emit(program, target, mode="verified", proof=proof).decisions
    written = sum(decision.removed for decision in decisions)
    return {
        "kernel": name,
        "loops": loops,
        "guards_total": total,
        "trivial_accepted": trivial.accepted,
        "trivial_removed": removed or 0,
        "trivial_problem": trivial.problem,
        "written_accepted": proof.required_passed and written == len(decisions),
        "written_removed": written,
    }


def summarize(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    def total(key: str) -> int:
        return sum(int(str(row[key])) for row in rows)

    def complete(key: str) -> int:
        return sum(row[key] == row["guards_total"] for row in rows)

    counter: dict[str, object] = {}
    if rows and all("counter_accepted" in row for row in rows):
        counter = {
            "counter_rule": COUNTER_RULE,
            "counter_programs_accepted": sum(bool(row["counter_accepted"]) for row in rows),
            "counter_loops_matched": total("counter_matched_loops"),
            "loops_total": total("loops"),
        }
    return counter | {
        "programs": len(rows),
        "guards_total": total("guards_total"),
        "trivial_removed": total("trivial_removed"),
        "written_removed": total("written_removed"),
        "trivial_programs_accepted": sum(bool(row["trivial_accepted"]) for row in rows),
        "trivial_programs_all_removed": complete("trivial_removed"),
        "written_programs_all_removed": complete("written_removed"),
        "additional_sites_written_over_trivial": total("written_removed") - total("trivial_removed"),
    }


def table(summary: Mapping[str, object]) -> str:
    return _table(summary) + (
        ""
        if "counter_rule" not in summary
        else "\n## Admission (every required obligation, frozen admit())\n\n"
        "| Invariant source | Programs admitted | Loops matched by the rule |\n| --- | ---: | ---: |\n"
        f"| trivial invariant `1` at every loop | {summary['trivial_programs_accepted']} of {summary['programs']} | |\n"
        f"| counter rule | {summary['counter_programs_accepted']} of {summary['programs']} "
        f"| {summary['counter_loops_matched']} of {summary['loops_total']} |\n\n"
        f"Counter rule (fixed before the run): {summary['counter_rule']}\n"
    )


def _table(summary: Mapping[str, object]) -> str:
    return (
        "# No-proposer control (post hoc)\n\n"
        f"Programs: {summary['programs']}; executable guard sites: {summary['guards_total']}.\n\n"
        "| Condition | Guard sites removed | Programs with every site removed |\n| --- | ---: | ---: |\n"
        f"| trivial invariant `1` at every loop | {summary['trivial_removed']} "
        f"| {summary['trivial_programs_all_removed']} |\n"
        f"| invariant written in the file | {summary['written_removed']} "
        f"| {summary['written_programs_all_removed']} |\n"
    )


def kernels(directories: Sequence[Path]) -> list[Path]:
    found: list[Path] = []
    for directory in directories:
        own = directory / "kernel.c"
        found += [own] if own.is_file() else sorted(directory.glob("*/kernel.c"))
    return found


def derive(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "program"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guard sites removed with a trivial invariant at every loop.")
    parser.add_argument("directories", nargs="+", type=Path, help="kernel directories or their parents")
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--rule", choices=RULES, help="also measure a mechanical invariant rule (counter)")
    parser.add_argument("--dry-run", action="store_true", help="measure and print; write nothing")
    arguments = parser.parse_args(argv)
    compiler = os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
    cpp = os.environ.get(CPP_ENV_VAR, DEFAULT_CPP)
    heldout.require_intact(arguments.directories)
    paths = kernels(arguments.directories)
    if not paths:
        print("no_proposer: no kernel.c found", file=sys.stderr)
        return 1
    run = None
    if not arguments.dry_run:
        settings = {"timeout_ms": SOLVER_TIMEOUT_MS, "kernels": [p.parent.name for p in paths], "post_hoc": True}
        if arguments.rule == "counter":
            settings |= {"rule": arguments.rule, "counter_rule": COUNTER_RULE}
        schema = COUNTER_SCHEMA if arguments.rule == "counter" else SCHEMA
        run = campaign.start(
            EXPERIMENT, arguments.output, compiler=compiler, cpp=cpp, seed=None, settings=settings, schema=schema
        )
    target = run.target if run is not None else campaign.read_target(compiler)
    rows = []
    for path in paths:
        row = measure(path.parent.name, lower(parse_source(path, cpp=cpp)), target, rule=arguments.rule)
        rows.append(row)
        if run is not None:
            run.evidence.record("program", path.parent.name, "row", row)
        campaign.progress(f"{row['kernel']}: trivial {row['trivial_removed']}/{row['guards_total']}")
    summary = summarize(rows)
    if run is not None:
        campaign.finish(run, summary, table(summary), derive)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
