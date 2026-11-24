"""B2 baseline: Daikon-style dynamic inference of loop invariants filtered by the B1 Houdini loop (plan phase 4).

B1 (`houdini.py`) cannot express equalities, non-unit constants, three-variable relations or disjunctions, so
"the LLM beats B1 outside the template family" is close to a tautology. B2 observes loop-head states with the
reference interpreter on sampled inputs (small-domain fuzz draws plus, when a profile binds to the program,
profile draws), keeps every candidate of a fixed grammar that holds on *all* observed states (Daikon's
criterion), and hands the candidates to the same Houdini elimination and the same frozen admission as B1.
Candidates are text; nothing from the inference has proof authority.

The grammar, bounds and sample sizes are pre-registered in `paper/AMENDMENTS.md` (`FAMILY_B2_TEXT` is tested
against that file character for character). States come only from executions the interpreter completed; a
rejected run (precondition violation, overflow, out of bounds) contributes nothing.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from random import Random

from pfc import ir
from pfc.target import Target
from pfc_eval import expressions, fuzz, generate, inject, interpret

FAMILY_B2_TEXT = (
    "dynamic inference over loop-head states: `v == c`, `v == w + c`, `v == a*w + b` (a in {-2, -1, 2}), "
    "`c <= v`, `v <= c`, `v <= w + c` with observed extremes, `v == w + x`, `v == w - x`, `v == w * x`, and "
    "implications `cond => e` from call-free scalar branch conditions in the loop body (written as disjunctions); "
    "every literal of absolute value at most 8; at most 40 candidates per loop; 200 fuzz draws plus 200 profile "
    "draws when a profile "
    "binds; a loop with fewer than 20 observed head states gets no candidate"
)
CONSTANT_BOUND = 8
CAP = 40
MIN_STATES = 20
SAMPLES_FUZZ = 200
SAMPLES_PROFILE = 200
MULTIPLIERS = (-2, -1, 2)
State = Mapping[str, int]
render = expressions.render


@dataclass(frozen=True)
class States:
    per_loop: dict[int, list[dict[str, int]]]
    runs: int
    accepted: int
    sources: dict[str, int]


@dataclass(frozen=True)
class Inference:
    candidates: tuple[str, ...]
    truncated: int


@dataclass(frozen=True)
class Family:
    candidates: dict[int, list[str]]
    states_observed: dict[int, int]
    truncated: dict[int, int]
    runs: int
    accepted: int
    sources: dict[str, int]


# -- evaluation of scalar IR on a named state -------------------------------------------------------------------


def evaluate(expression: ir.Expr, state: State) -> int:
    """Mathematical value of a call-free scalar expression on a state keyed by variable name."""
    if isinstance(expression, ir.Literal):
        return expression.value
    if isinstance(expression, ir.Variable):
        return state[expression.symbol.name]
    if isinstance(expression, ir.Convert):
        value = evaluate(expression.operand, state)
        return int(value != 0) if expression.type is ir.ScalarType.BOOL else value
    if isinstance(expression, ir.Unary):
        value = evaluate(expression.operand, state)
        if expression.operator == "!":
            return int(value == 0)
        if expression.operator == "-":
            return -value
        if expression.operator == "+":
            return value
    if isinstance(expression, ir.Binary):
        if expression.operator == "&&":
            return int(evaluate(expression.left, state) != 0 and evaluate(expression.right, state) != 0)
        if expression.operator == "||":
            return int(evaluate(expression.left, state) != 0 or evaluate(expression.right, state) != 0)
        left, right = evaluate(expression.left, state), evaluate(expression.right, state)
        comparisons = {
            "==": left == right,
            "!=": left != right,
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
        }
        if expression.operator in comparisons:
            return int(comparisons[expression.operator])
        if expression.operator == "+":
            return left + right
        if expression.operator == "-":
            return left - right
        if expression.operator == "*":
            return left * right
    raise ValueError(f"unsupported expression for state evaluation: {render(expression)}")


def _scalar_condition(expression: ir.Expr) -> bool:
    """Call-free, memory-free, division-free: exactly what `evaluate` accepts."""
    if isinstance(expression, (ir.Literal, ir.Variable)):
        return True
    if isinstance(expression, ir.Convert):
        return _scalar_condition(expression.operand)
    if isinstance(expression, ir.Unary):
        return expression.operator in ("!", "-", "+") and _scalar_condition(expression.operand)
    if isinstance(expression, ir.Binary):
        return expression.operator not in ("/", "%") and all(
            _scalar_condition(side) for side in (expression.left, expression.right)
        )
    return False


def _loop_statements(block: ir.Block, found: list[ir.While | ir.For]) -> None:
    """Loops in the same preorder as `inject.loops`."""
    for statement in block.statements:
        if isinstance(statement, ir.Block):
            _loop_statements(statement, found)
        elif isinstance(statement, ir.If):
            _loop_statements(statement.then_body, found)
            if statement.else_body is not None:
                _loop_statements(statement.else_body, found)
        elif isinstance(statement, (ir.While, ir.For)):
            found.append(statement)
            _loop_statements(statement.body, found)


def _conditions(block: ir.Block, found: list[ir.Expr]) -> None:
    for statement in block.statements:
        if isinstance(statement, ir.If):
            if _scalar_condition(statement.condition):
                found.append(statement.condition)
            _conditions(statement.then_body, found)
            if statement.else_body is not None:
                _conditions(statement.else_body, found)
        elif isinstance(statement, (ir.Block, ir.While, ir.For)):
            _conditions(statement if isinstance(statement, ir.Block) else statement.body, found)


def loop_conditions(function: ir.Function, loop_index: int) -> tuple[ir.Expr, ...]:
    """Call-free scalar `if` conditions inside the body of the loop with this preorder index."""
    loops: list[ir.While | ir.For] = []
    _loop_statements(function.body, loops)
    found: list[ir.Expr] = []
    _conditions(loops[loop_index].body, found)
    return tuple(found)


# -- state collection --------------------------------------------------------------------------------------------


def collect(
    program: ir.Program,
    target: Target,
    *,
    function: str | None = None,
    seed: int = 0,
    samples: int = SAMPLES_FUZZ,
    profile: generate.Profile | None = None,
    profile_samples: int = SAMPLES_PROFILE,
) -> States:
    """Loop-head states, per loop index, from executions the interpreter completed.

    Fuzz draws come from `fuzz.draw_inputs`; profile draws are added only when the profile binds to this
    program (digest and parameter names), so a profile of another kernel is silently irrelevant, never misused.
    """
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    index_of = {loop.location: loop.index for loop in inject.loops(entry)}
    scope_of = {loop.index: [symbol.name for symbol in loop.scope] for loop in inject.loops(entry)}
    rng = Random(seed)
    draws = [("fuzz", fuzz.draw_inputs(entry, rng)) for _ in range(samples)]
    bound = profile is not None
    if profile is not None:
        try:
            generate.bind(profile, program)
        except ValueError:
            bound = False
    if profile is not None and bound:
        draws += [("profile", generate.draw(profile, rng)) for _ in range(profile_samples)]
    per_loop: dict[int, list[dict[str, int]]] = {k: [] for k in scope_of}
    sources = {"fuzz": 0, "profile": 0}
    accepted = 0
    for source, inputs in draws:
        sources[source] += 1
        buffer: list[tuple[int, dict[str, int]]] = []

        def observe(head: interpret.LoopHead, buffer: list[tuple[int, dict[str, int]]] = buffer) -> None:
            if head.function == name and head.location in index_of:
                k = index_of[head.location]
                buffer.append((k, {v: head.scalars[v] for v in scope_of[k] if v in head.scalars}))

        outcome = interpret.run(program, inputs, target=target, function=name, observer=observe)
        if isinstance(outcome, interpret.Observation):
            accepted += 1
            for k, state in buffer:
                per_loop[k].append(state)
    return States(per_loop, len(draws), accepted, sources)


# -- candidate grammar -------------------------------------------------------------------------------------------


def _small(c: int) -> bool:
    return abs(c) <= CONSTANT_BOUND


def _offset(base: str, c: int) -> str:
    return base if c == 0 else f"({base} + {c})" if c > 0 else f"({base} - {-c})"


def _equalities(names: Sequence[str], states: Sequence[State]) -> list[str]:
    texts: list[str] = []
    constant = {v: len({s[v] for s in states}) == 1 for v in names}
    for v in names:
        if constant[v] and _small(states[0][v]):
            texts.append(f"({v} == {states[0][v]})")
    for v in names:
        for w in names:
            if v == w or constant[w]:
                continue
            diffs = {s[v] - s[w] for s in states}
            if len(diffs) == 1:
                c = diffs.pop()
                if _small(c):
                    texts.append(f"({v} == {_offset(w, c)})")
            for a in MULTIPLIERS:
                offsets = {s[v] - a * s[w] for s in states}
                if len(offsets) == 1:
                    b = offsets.pop()
                    if _small(b):
                        scaled = f"(2 * {w})" if a == 2 else f"(2 * {w})" if a == -2 else w
                        if a > 0:
                            texts.append(f"({v} == {_offset(scaled, b)})")
                        else:
                            texts.append(f"({v} == ({b} - {scaled}))")
    return texts


def _bounds(names: Sequence[str], states: Sequence[State]) -> list[str]:
    texts: list[str] = []
    constant = {v: len({s[v] for s in states}) == 1 for v in names}
    for v in names:
        if constant[v]:
            continue
        low, high = min(s[v] for s in states), max(s[v] for s in states)
        if _small(low):
            texts.append(f"({low} <= {v})")
        if _small(high):
            texts.append(f"({v} <= {high})")
    for v in names:
        for w in names:
            if v == w:
                continue
            diffs = {s[v] - s[w] for s in states}
            if len(diffs) == 1:
                continue  # an equality already states this relation exactly
            c = max(diffs)
            if _small(c):
                texts.append(f"({v} <= {_offset(w, c)})")
    return texts


def _ternary(names: Sequence[str], states: Sequence[State]) -> list[str]:
    texts: list[str] = []
    for i, v in enumerate(names):
        for j, w in enumerate(names):
            for k, x in enumerate(names):
                if len({i, j, k}) != 3:
                    continue
                if j < k and all(s[v] == s[w] + s[x] for s in states):
                    texts.append(f"({v} == ({w} + {x}))")
                if all(s[v] == s[w] - s[x] for s in states):
                    texts.append(f"({v} == ({w} - {x}))")
                if j < k and all(s[v] == s[w] * s[x] for s in states):
                    texts.append(f"({v} == ({w} * {x}))")
    return texts


def _implications(names: Sequence[str], states: Sequence[State], conditions: Iterable[ir.Expr]) -> list[str]:
    texts: list[str] = []
    everywhere = set(_equalities(names, states))
    for condition in conditions:
        try:
            truth = [evaluate(condition, s) != 0 for s in states]
        except (KeyError, ValueError):
            continue
        if all(truth) or not any(truth):
            continue
        text = render(condition)
        when_true = [s for s, t in zip(states, truth, strict=True) if t]
        when_false = [s for s, t in zip(states, truth, strict=True) if not t]
        texts += [f"((!{text}) || {e})" for e in _equalities(names, when_true) if e not in everywhere]
        texts += [f"({text} || {e})" for e in _equalities(names, when_false) if e not in everywhere]
    return texts


def infer(
    names: Sequence[str], states: Sequence[State], conditions: Sequence[ir.Expr] = (), *, cap: int = CAP
) -> Inference:
    """Every grammar candidate that holds on all states, deduplicated in a fixed order and capped."""
    if len(states) < MIN_STATES:
        return Inference((), 0)
    ordered = [n for n in names if all(n in s for s in states)]
    texts = _equalities(ordered, states) + _bounds(ordered, states) + _ternary(ordered, states)
    texts += _implications(ordered, states, conditions)
    unique = list(dict.fromkeys(texts))
    return Inference(tuple(unique[:cap]), max(0, len(unique) - cap))


def family(
    program: ir.Program,
    target: Target,
    *,
    function: str | None = None,
    seed: int = 0,
    profile: generate.Profile | None = None,
    samples: int = SAMPLES_FUZZ,
    profile_samples: int = SAMPLES_PROFILE,
) -> Family:
    """The B2 candidate set per loop of one program, with the state counts the inference saw."""
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    states = collect(
        program, target, function=name, seed=seed, samples=samples, profile=profile, profile_samples=profile_samples
    )
    candidates: dict[int, list[str]] = {}
    truncated: dict[int, int] = {}
    for loop in inject.loops(entry):
        inference = infer([s.name for s in loop.scope], states.per_loop[loop.index], loop_conditions(entry, loop.index))
        candidates[loop.index] = list(inference.candidates)
        truncated[loop.index] = inference.truncated
    observed = {k: len(v) for k, v in states.per_loop.items()}
    return Family(candidates, observed, truncated, states.runs, states.accepted, states.sources)
