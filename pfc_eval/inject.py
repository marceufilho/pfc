"""Systematic false-invariant injection and counterexample reproduction (roadmap E5, EV-R13, design E1.3).

For every loop of a function, candidate invariants are generated from the hand invariant by a fixed
operator family (literal false, the curated near-miss, off-by-one at each bound, reversed inequality,
dropped conjunct, conjunct over the wrong variable), at most twenty per loop with a seeded selection.
Every candidate goes through the frozen `propose.invariants.admit` path. Its class is established
independently of the solver: a candidate is `false` when the reference interpreter observes a violation
of it on a reachable state (fuzzed inputs accepted by the unchanged `REQUIRES`); otherwise it is
`valid-and-sufficient` when admitted, `valid-but-insufficient` when its initiation and preservation
proofs pass but admission still fails, and `unclassified` when nothing refutes it and its own proofs
fail. Dropping a conjunct is not presumed false. The 100 % rejection criterion applies to the `false`
class, with the actual denominator reported.

Counterexample models are classified as `entry` (initiation, or safety obligations whose model binds only
parameters and entry-derived values) or `abstract` (loop states that need not be reachable). Entry models
are reconstructed conservatively and must reproduce: an invariant violation observed by the interpreter at
the loop head, or, for safety obligations, the interpreter's rejection at the site plus a trap of the
checked binary. An entry model that does not reproduce is a discrepancy requiring diagnosis, never
silently relabelled abstract.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from typing import Literal

from pfc import ir, smt, workflow
from pfc.config import C_FLAGS, DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.corpus import NEAR_MISS_INVARIANTS
from pfc.diagnostics import SourceLocation
from pfc.digest import sha256_text
from pfc.emit import emit
from pfc.environment import resolve_compiler
from pfc.lower import lower
from pfc.parse import SpecKind, parse_preprocessed, parse_source
from pfc.propose.invariants import Admission, admit, strip
from pfc.target import Target
from pfc_eval import campaign, differential, evidence, expressions, fuzz, generate, interpret

ROOT = Path(__file__).resolve().parents[1]
OPERATORS = ("literal_false", "near_miss", "off_by_one", "reversed", "dropped", "wrong_variable")
CANDIDATES_PER_LOOP = 20
REFUTATION_DRAWS = 1000
RECONSTRUCT_MAX_LENGTH = 4096
COMPARISONS = ("<", "<=", ">", ">=", "==", "!=")
REVERSED = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}
CandidateClass = Literal["false", "valid-and-sufficient", "valid-but-insufficient", "unclassified", "unparseable"]
ModelClass = Literal["entry", "abstract", "none"]
ReproductionStatus = Literal["reproduced", "discrepancy", "abstract", "unreconstructible", "none"]

SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("loop_index", "integer"),
        evidence.Column("loop_line", "integer"),
        evidence.Column("operator", "string", allowed=OPERATORS),
        evidence.Column("candidate_text", "string"),
        evidence.Column(
            "candidate_class",
            "string",
            allowed=("false", "valid-and-sufficient", "valid-but-insufficient", "unclassified", "unparseable"),
        ),
        evidence.Column("refutation_loop", "integer", nullable=True),
        evidence.Column("admitted", "boolean"),
        evidence.Column(
            "rejection_class",
            "string",
            allowed=("accepted", "unparseable", "not-inductive", "insufficient", "guards-retained"),
        ),
        evidence.Column("failed_kinds", "string"),
        evidence.Column("real_model", "boolean"),
        evidence.Column("model_kind", "string", nullable=True),
        evidence.Column("model_class", "string", allowed=("entry", "abstract", "none")),
        evidence.Column(
            "reproduction", "string", allowed=("reproduced", "discrepancy", "abstract", "unreconstructible", "none")
        ),
        evidence.Column("checked_trap", "boolean", nullable=True),
    )
)


@dataclass(frozen=True)
class Loop:
    index: int
    location: SourceLocation
    invariant: ir.Contract
    scope: tuple[ir.Symbol, ...]


@dataclass(frozen=True)
class Candidate:
    loop: int
    operator: str
    text: str
    invariants: tuple[str, ...]


@dataclass(frozen=True)
class Refutation:
    loop: int  # Preorder index of the violated loop; stable across the re-lowered candidate program.
    inputs: interpret.Inputs


@dataclass(frozen=True)
class Reproduction:
    status: ReproductionStatus
    reason: str
    checked_trap: bool | None = None


@dataclass(frozen=True)
class CandidateResult:
    candidate: Candidate
    admitted: bool
    candidate_class: CandidateClass
    refutation: Refutation | None
    rejection_class: str
    failed_kinds: tuple[str, ...]
    real_model: bool
    model_kind: str | None
    model_class: ModelClass
    reproduction: Reproduction


@dataclass(frozen=True)
class KernelInjection:
    kernel: str
    loops: int
    candidates: tuple[CandidateResult, ...]
    false_total: int
    false_admitted: int
    entry_models: int
    abstract_models: int
    reproduced: int
    unreconstructible: int
    discrepancies: int
    controls_rejected: bool
    passed: bool


# -- loops, rendering and the mutation family -----------------------------------------------------------------


def _scope_walk(block: ir.Block, scope: list[ir.Symbol], found: list[Loop]) -> None:
    for statement in block.statements:
        if isinstance(statement, ir.Local):
            scope.append(statement.symbol)
        elif isinstance(statement, ir.Block):
            _scope_walk(statement, list(scope), found)
        elif isinstance(statement, ir.If):
            _scope_walk(statement.then_body, list(scope), found)
            if statement.else_body is not None:
                _scope_walk(statement.else_body, list(scope), found)
        elif isinstance(statement, (ir.While, ir.For)):
            inner = list(scope)
            if isinstance(statement, ir.For) and isinstance(statement.initializer, ir.Local):
                inner.append(statement.initializer.symbol)
            if statement.invariant is None:
                raise ValueError("injection needs a hand invariant on every loop")
            found.append(Loop(len(found), statement.invariant.location, statement.invariant, tuple(inner)))
            _scope_walk(statement.body, inner, found)


def loops(function: ir.Function) -> tuple[Loop, ...]:
    """Loops in preorder with their hand invariant and the scalar symbols in scope at the loop head."""
    found: list[Loop] = []
    scope = [p.symbol for p in function.parameters if not isinstance(p.symbol.type, ir.ArrayType)]
    _scope_walk(function.body, scope, found)
    return tuple(found)


def render(expression: ir.Expr, swap: tuple[int, str] | None = None) -> str:
    return expressions.render(expression, swap)


def _canonical(text: str) -> str:
    return text.replace("(", "").replace(")", "").replace(" ", "")


def _conjoin(parts: Sequence[str]) -> str:
    joined = parts[0]
    for part in parts[1:]:
        joined = f"({joined} && {part})"
    return joined


def _comparison(expression: ir.Expr) -> ir.Binary | None:
    inner = expression
    while isinstance(inner, ir.Convert):
        inner = inner.operand
    return inner if isinstance(inner, ir.Binary) and inner.operator in COMPARISONS else None


def _literal(expression: ir.Expr) -> ir.Literal | None:
    inner = expression
    while isinstance(inner, ir.Convert):
        inner = inner.operand
    return inner if isinstance(inner, ir.Literal) else None


def _mutations(loop: Loop) -> list[tuple[str, str]]:
    """(operator, text) pairs over the loop's own invariant, in a fixed order."""
    parts = [render(p) for p in expressions.flatten(loop.invariant.condition)]
    nodes = expressions.flatten(loop.invariant.condition)
    found: list[tuple[str, str]] = []

    def replaced(index: int, text: str) -> str:
        return _conjoin(parts[:index] + [text] + parts[index + 1 :])

    for index, node in enumerate(nodes):
        comparison = _comparison(node)
        if comparison is None:
            continue
        for delta in (1, -1):
            left_literal, right_literal = _literal(comparison.left), _literal(comparison.right)
            if left_literal is not None and right_literal is None:
                value = left_literal.value + delta
                text = f"({value} {comparison.operator} {render(comparison.right)})"
            elif right_literal is not None:
                value = right_literal.value + delta
                text = f"({render(comparison.left)} {comparison.operator} {value})"
            else:
                sign = "+" if delta > 0 else "-"
                text = f"({render(comparison.left)} {comparison.operator} ({render(comparison.right)} {sign} 1))"
            found.append(("off_by_one", replaced(index, text)))
    for index, node in enumerate(nodes):
        comparison = _comparison(node)
        if comparison is not None and comparison.operator in REVERSED:
            text = f"({render(comparison.left)} {REVERSED[comparison.operator]} {render(comparison.right)})"
            found.append(("reversed", replaced(index, text)))
    for index in range(len(parts)):
        remaining = parts[:index] + parts[index + 1 :]
        found.append(("dropped", _conjoin(remaining) if remaining else "1"))
    names = [s.name for s in loop.scope]
    for index, node in enumerate(nodes):
        current = render(node)
        for occurrence in range(expressions.variables(node)):
            for name in names:
                candidate = render(node, (occurrence, name))
                if candidate != current:
                    found.append(("wrong_variable", replaced(index, candidate)))
    return found


def family(program: ir.Program, function: str, loop: Loop, *, near_miss: Sequence[str] | None) -> tuple[Candidate, ...]:
    """Controls first, then the systematic mutations; duplicates and the original are removed."""
    entry = next(f for f in program.functions if f.name == function)
    hand = [render(item.invariant.condition) for item in loops(entry)]

    def full(text: str) -> tuple[str, ...]:
        return tuple(text if k == loop.index else h for k, h in enumerate(hand))

    candidates: list[Candidate] = [Candidate(loop.index, "literal_false", "0", full("0"))]
    if (
        near_miss is not None
        and loop.index < len(near_miss)
        and _canonical(near_miss[loop.index]) != _canonical(hand[loop.index])
    ):
        # The curated list is a whole-program control; an entry that repeats the hand invariant is no near-miss.
        candidates.append(Candidate(loop.index, "near_miss", near_miss[loop.index], full(near_miss[loop.index])))
    seen = {c.text for c in candidates} | {hand[loop.index]}
    for operator, text in _mutations(loop):
        if text in seen:
            continue
        seen.add(text)
        candidates.append(Candidate(loop.index, operator, text, full(text)))
    return tuple(candidates)


def select(candidates: Sequence[Candidate], *, seed: int, limit: int = CANDIDATES_PER_LOOP) -> tuple[Candidate, ...]:
    """Keep the controls and a seeded sample of the mutations when the family exceeds the limit."""
    controls = [c for c in candidates if c.operator in ("literal_false", "near_miss")]
    rest = [c for c in candidates if c.operator not in ("literal_false", "near_miss")]
    room = max(limit - len(controls), 0)
    if len(rest) > room:
        chosen = set(Random(seed).sample(range(len(rest)), room))
        rest = [c for k, c in enumerate(rest) if k in chosen]
    return tuple(controls + rest)


def with_invariants(program: ir.Program, function: str, invariants: Sequence[str]) -> ir.Program:
    """The program with every loop invariant replaced, built exactly as the frozen admission does."""
    source = ir.print_c(strip(program, placeholders=True)).replace('#include "spec.h"', "").split("*/", 1)[1]
    parts = source.split("INVARIANT(0);")
    if len(parts) - 1 != len(invariants):
        raise ValueError("one invariant per loop, in preorder")
    source = parts[0] + "".join(
        f"INVARIANT({text});" + suffix for text, suffix in zip(invariants, parts[1:], strict=True)
    )
    for macro in SpecKind:
        source = source.replace(str(macro) + "(", "__spec_" + str(macro).lower() + "(")
    return lower(parse_preprocessed(source, "<candidate>"))


# -- independent classification -------------------------------------------------------------------------------


def refute(
    mutant: ir.Program, target: Target, *, seed: int, draws: int = REFUTATION_DRAWS, function: str | None = None
) -> Refutation | None:
    """A reachable state violating the candidate invariant: fuzzed inputs accepted by the unchanged REQUIRES."""
    name = function if function is not None else mutant.functions[0].name
    entry = next(f for f in mutant.functions if f.name == name)
    heads = {f"INVARIANT at {loop.location.line}:{loop.location.column}": loop.index for loop in loops(entry)}
    rng = Random(seed)
    for _ in range(draws):
        inputs = fuzz.draw_inputs(entry, rng)
        outcome = interpret.run(mutant, inputs, target=target, function=name)
        if isinstance(outcome, interpret.Rejection):
            continue
        for violation in outcome.annotation_violations:
            if violation in heads:
                return Refutation(heads[violation], inputs)
    return None


def classify(*, refuted: bool, accepted: bool, invariant_proved: bool) -> CandidateClass:
    if refuted:
        return "false"
    if accepted:
        return "valid-and-sufficient"
    if invariant_proved:
        return "valid-but-insufficient"
    return "unclassified"


def _entry_level(scalar: smt.ScalarValue, function: ir.Function) -> bool:
    """Parameters and the VC's memory bases (`vc:` ids named `base_<array>`) describe the entry state.

    Every other model-origin scalar is a body-declared local or a havocked loop copy (a `vc:` id that
    keeps the variable's name), so it denotes loop state. Entry-derived prefix locals never disqualify.
    """
    if scalar.origin == "entry-derived":
        return True
    if scalar.symbol_id in {p.symbol.id for p in function.parameters}:
        return True
    return scalar.symbol_id.startswith("vc:") and scalar.name.startswith("base_")


def model_class(counterexample: smt.Counterexample, function: ir.Function, kind: str) -> Literal["entry", "abstract"]:
    """Entry state: initiation or safety models whose free scalars are all entry-level."""
    if kind not in ("entry", "bounds", "division"):
        return "abstract"
    if all(_entry_level(scalar, function) for scalar in counterexample.scalars):
        return "entry"
    return "abstract"


def reconstruct(function: ir.Function, counterexample: smt.Counterexample, target: Target) -> interpret.Inputs | str:
    """Parameters from the model, zero where unconstrained; array cells from the model's reads."""
    values = {s.symbol_id: s.value for s in counterexample.scalars if s.origin == "model"}
    low, high = target.long_range()
    scalars: dict[str, int] = {}
    for parameter in function.parameters:
        if isinstance(parameter.symbol.type, ir.ArrayType):
            continue
        value = values.get(parameter.symbol.id, 0)
        bounds = (0, 1) if parameter.symbol.type is ir.ScalarType.BOOL else (low, high)
        if not bounds[0] <= value <= bounds[1]:
            return f"model value for {parameter.symbol.name} is outside its C range"
        scalars[parameter.symbol.name] = value
    arrays: dict[str, tuple[int, ...]] = {}
    for parameter in function.parameters:
        if parameter.length is None:
            continue
        length = scalars[parameter.length.name]
        if not 0 <= length <= RECONSTRUCT_MAX_LENGTH:
            return f"array length {length} for {parameter.symbol.name} is outside the bounded reconstruction profile"
        contents = [0] * length
        for cell in counterexample.reads:
            if cell.symbol_id == parameter.symbol.id and 0 <= cell.index < length:
                if not low <= cell.value <= high:
                    return "array model value is outside the C long range"
                contents[cell.index] = cell.value
        arrays[parameter.symbol.name] = tuple(contents)
    return interpret.Inputs(scalars, arrays)


def _trap(
    program: ir.Program,
    function: ir.Function,
    inputs: interpret.Inputs,
    target: Target,
    compiler: str,
    runner: differential.Runner,
) -> bool | None:
    """Compile the checked build with the file-based driver and run the single case; SIGILL is the trap."""
    checked = emit(program, target, mode="checked")
    case = generate.Case("0000", False, inputs, interpret.Observation(0, {}, 0, ()))
    with TemporaryDirectory(prefix="pfc-eval-inject-") as temporary:
        directory = Path(temporary)
        (directory / "checked.c").write_text(checked.c, encoding="utf-8")
        (directory / "driver.c").write_text(differential.driver_source(function, target), encoding="utf-8")
        (directory / "case.bin").write_bytes(differential.encode_cases((case,), function, target))
        executable = resolve_compiler(compiler)
        compiled = runner(
            (
                executable,
                *C_FLAGS,
                str(directory / "checked.c"),
                str(directory / "driver.c"),
                "-o",
                str(directory / "checked"),
            ),
            directory,
            differential.PROCESS_TIMEOUT_S,
        )
        if compiled.status != "ok":
            return None
        outcome = runner(
            (str(directory / "checked"), str(directory / "case.bin"), str(directory / "out.bin")), directory, 60.0
        )
    return outcome.status == "crash" and outcome.returncode == -4


def reproduce(
    program: ir.Program,
    function: ir.Function,
    kind: str,
    location: SourceLocation,
    counterexample: smt.Counterexample,
    target: Target,
    *,
    compiler: str | None,
    runner: differential.Runner | None = None,
) -> Reproduction:
    """Reproduce an entry-state model; a reproducible model that does not reproduce is a discrepancy."""
    if model_class(counterexample, function, kind) != "entry":
        return Reproduction("abstract", f"{kind} model binds loop state; not a reproduction target")
    inputs = reconstruct(function, counterexample, target)
    if isinstance(inputs, str):
        return Reproduction("unreconstructible", inputs)
    outcome = interpret.run(program, inputs, target=target, function=function.name)
    if kind == "entry":
        expected = f"INVARIANT at {location.line}:{location.column}"
        if expected in outcome.annotation_violations:
            # The head violation is observed before the body runs; a later rejection (typically the step budget,
            # when Z3 bound an unconstrained loop bound to a huge value) does not undo that observation.
            tail = (
                f" before rejecting ({outcome.kind}: {outcome.message})"
                if isinstance(outcome, interpret.Rejection)
                else ""
            )
            return Reproduction("reproduced", f"interpreter observed {expected}{tail}")
        if isinstance(outcome, interpret.Rejection):
            return Reproduction("discrepancy", f"reconstructed entry state rejected: {outcome.kind}: {outcome.message}")
        return Reproduction(
            "discrepancy",
            f"no invariant violation at {location.line}:{location.column} on the reconstructed entry state",
        )
    rejection_kind = {"bounds": "OutOfBounds", "division": "DivisionByZero"}[kind]
    if not isinstance(outcome, interpret.Rejection) or outcome.kind not in (rejection_kind, "Overflow"):
        return Reproduction(
            "discrepancy", f"interpreter did not reject at the {kind} site on the reconstructed entry state"
        )
    if outcome.location is None or (outcome.location.line, outcome.location.column) != (location.line, location.column):
        return Reproduction("discrepancy", f"interpreter rejected elsewhere: {outcome.message}")
    trap = None
    if compiler is not None:
        trap = _trap(program, function, inputs, target, compiler, runner or differential.invoke)
        if trap is not True:
            return Reproduction("discrepancy", f"checked binary did not trap (observed {trap})", trap)
    return Reproduction("reproduced", f"interpreter rejected with {outcome.kind} at the site", trap)


# -- per-kernel run --------------------------------------------------------------------------------------------


def _pick_model(admission: Admission) -> tuple[str, SourceLocation, smt.Counterexample] | None:
    assert admission.proof is not None
    order = {"entry": 0, "bounds": 1, "division": 1, "preservation": 2}
    failed = [
        r
        for r in admission.proof.results
        if r.obligation.category == "required" and r.status == "failed" and r.counterexample is not None
    ]
    failed.sort(key=lambda r: order.get(r.obligation.kind, 3))
    if not failed:
        return None
    first = failed[0]
    assert first.counterexample is not None
    return str(first.obligation.kind), first.obligation.location, first.counterexample


def rejection_class(admission: Admission, invariant_proved: bool) -> str:
    """Shared vocabulary for why the frozen admission refused a candidate list."""
    if admission.problem is not None:
        return "unparseable"
    if admission.accepted:
        return "accepted"
    if not invariant_proved:
        return "not-inductive"
    assert admission.proof is not None
    if not admission.proof.required_passed:
        return "insufficient"
    return "guards-retained"


def run_kernel(
    program: ir.Program,
    kernel: str,
    target: Target,
    *,
    compiler: str | None,
    seed: int = 0,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    draws: int = REFUTATION_DRAWS,
    near_miss: Sequence[str] | None | Literal["curated"] = "curated",
    runner: differential.Runner | None = None,
) -> KernelInjection:
    function = program.functions[0]
    controls = NEAR_MISS_INVARIANTS.get(kernel) if near_miss == "curated" else near_miss
    results: list[CandidateResult] = []
    found = loops(function)
    for loop in found:
        for candidate in select(family(program, function.name, loop, near_miss=controls), seed=seed):
            admission = admit(
                program, json.dumps({"invariants": list(candidate.invariants)}), target, timeout_ms=timeout_ms
            )
            if admission.problem is not None or admission.program is None or admission.proof is None:
                results.append(
                    CandidateResult(
                        candidate,
                        False,
                        "unparseable",
                        None,
                        "unparseable",
                        (),
                        False,
                        None,
                        "none",
                        Reproduction("none", admission.problem or "no program"),
                    )
                )
                continue
            mutant = admission.program
            mutant_function = next(f for f in mutant.functions if f.name == function.name)
            invariant_proved = all(
                r.status == "proved" and r.dependencies_satisfied
                for r in admission.proof.results
                if r.obligation.category == "required" and r.obligation.kind in ("entry", "preservation")
            )
            refutation = refute(mutant, target, seed=seed, draws=draws, function=function.name)
            candidate_class = classify(
                refuted=refutation is not None, accepted=admission.accepted, invariant_proved=invariant_proved
            )
            failed_kinds = tuple(
                sorted(
                    {
                        r.obligation.kind
                        for r in admission.proof.results
                        if r.obligation.category == "required" and r.status != "proved"
                    }
                )
            )
            picked = _pick_model(admission)
            real_model = picked is not None
            model_kind = picked[0] if picked else None
            klass: ModelClass = "none"
            reproduction = Reproduction("none", "no failed required obligation with a model")
            if picked is not None:
                kind, location, counterexample = picked
                klass = model_class(counterexample, mutant_function, kind)
                reproduction = reproduce(
                    mutant, mutant_function, kind, location, counterexample, target, compiler=compiler, runner=runner
                )
            results.append(
                CandidateResult(
                    candidate,
                    admission.accepted,
                    candidate_class,
                    refutation,
                    rejection_class(admission, invariant_proved),
                    failed_kinds,
                    real_model,
                    model_kind,
                    klass,
                    reproduction,
                )
            )
    false = [r for r in results if r.candidate_class == "false"]
    control_results = [r for r in results if r.candidate.operator in ("literal_false", "near_miss")]
    controls_ok = all(not r.admitted and r.real_model and r.candidate_class == "false" for r in control_results)
    discrepancies = sum(r.reproduction.status == "discrepancy" for r in results)
    false_admitted = sum(r.admitted for r in false)
    return KernelInjection(
        kernel,
        len(found),
        tuple(results),
        len(false),
        false_admitted,
        sum(r.model_class == "entry" for r in results),
        sum(r.model_class == "abstract" for r in results),
        sum(r.reproduction.status == "reproduced" for r in results),
        sum(r.reproduction.status == "unreconstructible" for r in results),
        discrepancies,
        controls_ok,
        false_admitted == 0 and discrepancies == 0 and controls_ok,
    )


# -- evidence ----------------------------------------------------------------------------------------------------


def rows(result: KernelInjection, loop_lines: Mapping[int, int]) -> list[dict[str, object]]:
    table: list[dict[str, object]] = []
    for item in result.candidates:
        table.append(
            {
                "kernel": result.kernel,
                "loop_index": item.candidate.loop,
                "loop_line": loop_lines[item.candidate.loop],
                "operator": item.candidate.operator,
                "candidate_text": item.candidate.text,
                "candidate_class": item.candidate_class,
                "refutation_loop": item.refutation.loop if item.refutation else None,
                "admitted": item.admitted,
                "rejection_class": item.rejection_class,
                "failed_kinds": ",".join(item.failed_kinds),
                "real_model": item.real_model,
                "model_kind": item.model_kind,
                "model_class": item.model_class,
                "reproduction": item.reproduction.status,
                "checked_trap": item.reproduction.checked_trap,
            }
        )
    return table


def summarize(results: Mapping[str, KernelInjection]) -> dict[str, object]:
    kernels: dict[str, object] = {}
    for name, result in results.items():
        classes = {
            klass: {
                "total": sum(r.candidate_class == klass for r in result.candidates),
                "admitted": sum(r.candidate_class == klass and r.admitted for r in result.candidates),
            }
            for klass in ("false", "valid-and-sufficient", "valid-but-insufficient", "unclassified", "unparseable")
        }
        kernels[name] = {
            "loops": result.loops,
            "candidates": len(result.candidates),
            **classes,
            "controls_rejected": result.controls_rejected,
            "entry_models": result.entry_models,
            "abstract_models": result.abstract_models,
            "reproduced": result.reproduced,
            "unreconstructible": result.unreconstructible,
            "discrepancies": result.discrepancies,
            "passed": result.passed,
        }
    return {"experiment": "inject", "kernels": kernels, "passed": all(r.passed for r in results.values())}


def render_table(summary: Mapping[str, object]) -> str:
    lines = [
        "| kernel | loops | candidates | false (admitted) | valid+sufficient | valid, insufficient | unclassified "
        "| unparseable | entry models reproduced / reconstructible | unreconstructible | abstract models "
        "| discrepancies | passed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    for name, e in kernels.items():
        lines.append(
            f"| {name} | {e['loops']} | {e['candidates']} | {e['false']['total']} ({e['false']['admitted']}) | "
            f"{e['valid-and-sufficient']['total']} | {e['valid-but-insufficient']['total']} | "
            f"{e['unclassified']['total']} | "
            f"{e['unparseable']['total']} | {e['reproduced']}/{e['entry_models'] - e['unreconstructible']} | "
            f"{e['unreconstructible']} | {e['abstract_models']} | {e['discrepancies']} | "
            f"{'yes' if e['passed'] else 'no'} |"
        )
    lines.append("")
    lines.append(
        "Classes are independent of the solver: `false` has a reachable violating state found by the interpreter; "
        "the 100% rejection criterion applies to that class with the shown denominator. Entry models are "
        "reconstructed and must reproduce; preservation and loop-state models are abstract, not reproduction targets. "
        "An entry model whose array lengths exceed the bounded reconstruction profile is unreconstructible and "
        "excluded from the denominator, never relabelled."
    )
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "candidate"]


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = 0,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    sources = workflow.discover(tuple(directories))
    programs = [(source.parent.name, lower(parse_source(source, cpp=cpp))) for source in sources]
    selected: dict[str, list[dict[str, object]]] = {}
    for name, program in programs:
        controls = NEAR_MISS_INVARIANTS.get(name)
        selected[name] = [
            asdict(c)
            for loop in loops(program.functions[0])
            for c in select(family(program, program.functions[0].name, loop, near_miss=controls), seed=seed)
        ]
    canonical = json.dumps(selected, sort_keys=True, indent=2) + "\n"
    digest = sha256_text(canonical)
    if dry_run:
        for name, items in selected.items():
            print(f"{name}: {len(items)} candidates over {len(loops(dict(programs)[name].functions[0]))} loop(s)")
        print(f"Preview: seed {seed}, operators {', '.join(OPERATORS)}, candidate set sha256 {digest[:16]}.")
        print("No files were written.")
        return True
    run = campaign.start(
        "inject",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "timeout_ms": timeout_ms,
            "operators": list(OPERATORS),
            "candidates_per_loop": CANDIDATES_PER_LOOP,
            "refutation_draws": REFUTATION_DRAWS,
            "refutation_domain": fuzz.domain(),
            "reconstruct_max_length": RECONSTRUCT_MAX_LENGTH,
            "candidates_sha256": digest,
            "near_miss_source": "pfc.corpus.NEAR_MISS_INVARIANTS",
            "kernels": [name for name, _ in programs],
        },
        schema=SCHEMA,
    )
    run.evidence.write_text("candidates.json", canonical)
    results: dict[str, KernelInjection] = {}
    for name, program in programs:
        if progress:
            progress(f"inject: {name}")
        result = run_kernel(program, name, run.target, compiler=compiler, seed=seed, timeout_ms=timeout_ms)
        results[name] = result
        lines = {loop.index: loop.location.line for loop in loops(program.functions[0])}
        for index, row in enumerate(rows(result, lines)):
            run.evidence.record("candidate", name, f"{index:03d}", row)
        for index, item in enumerate(result.candidates):
            run.evidence.record(
                "detail",
                name,
                f"{index:03d}",
                {
                    "candidate": asdict(item.candidate),
                    "refutation": (
                        {
                            "loop": item.refutation.loop,
                            "inputs": {
                                "scalars": dict(item.refutation.inputs.scalars),
                                "arrays": {k: list(v) for k, v in item.refutation.inputs.arrays.items()},
                            },
                        }
                        if item.refutation
                        else None
                    ),
                    "reproduction": asdict(item.reproduction),
                },
            )
    summary = summarize(results)
    campaign.finish(run, summary, render_table(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
