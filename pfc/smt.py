"""Z3 boundary: typed encoding, per-query limits, results and abstract models.

Only UNSAT of hypotheses AND NOT(goal) proves a formula. Such a result remains
conditional on the VC model and is not an authorization for C check removal.
"""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Literal, Protocol, cast

import z3

from pfc import ir, vc
from pfc.config import MAX_SOLVER_CACHE_ENTRIES, MAX_SOLVER_TIMEOUT_MS, SOLVER_TIMEOUT_MS
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.target import Target

ENCODING = "z3-int-shared-memory-v2; C division truncates to zero; division by zero totalized to 0"
Status = Literal["proved", "failed", "timeout", "unknown", "error"]


def solver_version() -> str:
    """Return the solver version used to bind evidence to this adapter."""
    return str(z3.get_full_version())


def query_id(obligation: vc.Obligation, target: Target, timeout_ms: int, version: str) -> str:
    """Identify the complete query and the fixed encoding/seed configuration."""
    return vc._digest((obligation, target, ENCODING, version, timeout_ms, 0))


class Solver(Protocol):
    def set(self, **options: int) -> None: ...
    def add(self, *expressions: z3.ExprRef) -> None: ...
    def check(self) -> object: ...
    def model(self) -> z3.ModelRef: ...
    def reason_unknown(self) -> str: ...


@dataclass(frozen=True)
class ScalarValue:
    symbol_id: str
    name: str
    value: int
    origin: Literal["model", "entry-derived"] = "model"


@dataclass(frozen=True)
class ArrayCell:
    symbol_id: str
    name: str
    index: int
    value: int


@dataclass(frozen=True)
class Counterexample:
    kind: Literal["entry-observation", "abstract-state"]
    scalars: tuple[ScalarValue, ...]
    reads: tuple[ArrayCell, ...]
    replayed: bool = False


@dataclass(frozen=True)
class Result:
    obligation: vc.Obligation
    query_id: str
    status: Status
    elapsed_ms: float
    counterexample: Counterexample | None = None
    reason: str | None = None
    dependencies_satisfied: bool = False
    cache_hit: bool = False


@dataclass(frozen=True)
class EntryResult:
    query: vc.EntryCheck
    query_id: str
    status: Literal["sat", "unsat", "timeout", "unknown", "error"]
    elapsed_ms: float
    reason: str | None = None
    cache_hit: bool = False


def entry_query_id(query: vc.EntryCheck, target: Target, timeout_ms: int, version: str) -> str:
    """Distinguish satisfiability of H from validity of H => G in every cache/identity."""
    return vc._digest(("entry-satisfiability", query, target, ENCODING, version, timeout_ms, 0))


class SolverCache:
    """Bounded, process-local cache of solver-produced results, never imported reports.

    Cache lifetime and ownership are explicit. Full query identities prevent stale
    reuse. Factory changes invalidate the cache so injected solvers cannot mix.
    Failed proofs remain failed; dependency admission is recomputed on every check.
    """

    def __init__(self, *, capacity: int = MAX_SOLVER_CACHE_ENTRIES) -> None:
        if type(capacity) is not int or not 1 <= capacity <= MAX_SOLVER_CACHE_ENTRIES:
            raise ValueError(f"cache capacity must be 1..{MAX_SOLVER_CACHE_ENTRIES}")
        self.capacity = capacity
        self._results: OrderedDict[str, Result | EntryResult] = OrderedDict()
        self._factory: Callable[[], Solver] | None = None

    def _lookup(self, identity: str, factory: Callable[[], Solver]) -> Result | EntryResult | None:
        if factory is not self._factory:
            self._results.clear()
            self._factory = factory
        existing = self._results.get(identity)
        if existing is not None:
            self._results.move_to_end(identity)
        return existing

    def _remember(self, identity: str, result: Result | EntryResult) -> None:
        if result.status in {"proved", "failed", "sat", "unsat"}:
            self._results[identity] = result
            if len(self._results) > self.capacity:
                self._results.popitem(last=False)

    def solve(
        self, obligation: vc.Obligation, target: Target, timeout_ms: int, version: str, factory: Callable[[], Solver]
    ) -> Result:
        identity = query_id(obligation, target, timeout_ms, version)
        existing = self._lookup(identity, factory)
        if isinstance(existing, Result) and existing.obligation == obligation:
            return replace(existing, cache_hit=True)
        result = _solve(obligation, target, timeout_ms, version, obligation.observations, factory)
        self._remember(identity, result)
        return result

    def entry(
        self, query: vc.EntryCheck, target: Target, timeout_ms: int, version: str, factory: Callable[[], Solver]
    ) -> EntryResult:
        identity = entry_query_id(query, target, timeout_ms, version)
        existing = self._lookup(identity, factory)
        if isinstance(existing, EntryResult) and existing.query == query:
            return replace(existing, cache_hit=True)
        result = _entry(query, target, timeout_ms, version, factory)
        self._remember(identity, result)
        return result


@dataclass(frozen=True)
class CheckReport:
    context_id: str
    function: str
    target: Target
    solver_version: str
    encoding: str
    timeout_ms: int
    results: tuple[Result, ...]
    required_passed: bool
    check_removal_authorized: bool = False
    vc_model: str = vc.MODEL
    entry_results: tuple[EntryResult, ...] = ()


class Encoder:
    """Encode internal terms, rejecting arity/sort mismatches instead of coercing."""

    def __init__(self, target: Target) -> None:
        self.target = target
        self.symbols: dict[str, tuple[ir.Symbol, z3.ExprRef]] = {}
        self.reads: list[tuple[ir.Symbol, z3.ArithRef, z3.ArithRef]] = []

    @staticmethod
    def integer(value: z3.ExprRef) -> z3.ArithRef:
        if not z3.is_int(value):
            raise ValueError("expected an integer term")
        return cast(z3.ArithRef, value)

    @staticmethod
    def boolean(value: z3.ExprRef) -> z3.BoolRef:
        if not z3.is_bool(value):
            raise ValueError("expected a Boolean term")
        return cast(z3.BoolRef, value)

    def encode(self, term: vc.Term) -> z3.ExprRef:
        if isinstance(term, vc.Integer):
            return z3.IntVal(term.value)
        if isinstance(term, vc.Boolean):
            return z3.BoolVal(term.value)
        if isinstance(term, vc.Limit):
            if term.type not in {ir.ScalarType.INT, ir.ScalarType.LONG} or term.side not in {"MIN", "MAX"}:
                raise ValueError("invalid target limit")
            bits = self.target.int_bits if term.type is ir.ScalarType.INT else self.target.long_bits
            maximum = 2 ** (bits - 1) - 1
            return z3.IntVal(maximum if term.side == "MAX" else -maximum - 1)
        if isinstance(term, vc.Reference):
            symbol = term.symbol
            if symbol.id in self.symbols:
                existing, value = self.symbols[symbol.id]
                if symbol != existing:
                    raise ValueError("conflicting symbol identity")
                return value
            if isinstance(symbol.type, ir.ArrayType):
                value = z3.Array(symbol.id, z3.IntSort(), z3.IntSort())
            else:
                value = z3.Int(symbol.id)  # C bool is integer 0/1, constrained by VC domains/conversions.
            self.symbols[symbol.id] = symbol, value
            return value
        if isinstance(term, vc.Read):
            memory = self.encode(term.memory)
            if not z3.is_array(memory):
                raise ValueError("read requires memory")
            index = self.integer(self.encode(term.index))
            result = self.integer(z3.Select(memory, self.integer(self.encode(term.address))))
            self.reads.append((term.array, index, result))
            return result
        return self._operation(term)

    def _operation(self, term: vc.Operation) -> z3.ExprRef:
        op = term.operator
        arities = {
            "neg": 1,
            "!": 1,
            "ite": 3,
            "select": 2,
            "store": 3,
            "c_div": 2,
            "+": 2,
            "-": 2,
            "*": 2,
            "<": 2,
            "<=": 2,
            ">": 2,
            ">=": 2,
            "==": 2,
            "!=": 2,
            "&&": 2,
            "||": 2,
            "=>": 2,
        }
        if op not in arities or len(term.arguments) != arities[op]:
            raise ValueError("unknown operator or incorrect arity")
        args = tuple(self.encode(argument) for argument in term.arguments)
        if op == "!":
            return z3.Not(self.boolean(args[0]))
        if op in {"&&", "||", "=>"}:
            left_bool, right_bool = self.boolean(args[0]), self.boolean(args[1])
            if op == "&&":
                return z3.And(left_bool, right_bool)
            return z3.Or(left_bool, right_bool) if op == "||" else z3.Implies(left_bool, right_bool)
        if op == "ite":
            return z3.If(self.boolean(args[0]), self.integer(args[1]), self.integer(args[2]))
        if op == "store":
            if not z3.is_array(args[0]):
                raise ValueError("store requires an array")
            return z3.Store(args[0], self.integer(args[1]), self.integer(args[2]))
        if op == "select":
            if not z3.is_array(args[0]):
                raise ValueError("select requires an array")
            index = self.integer(args[1])
            value = self.integer(z3.Select(args[0], index))
            reference = term.arguments[0]
            if not isinstance(reference, vc.Reference):
                raise ValueError("this encoding requires a named read-only array")
            self.reads.append((reference.symbol, index, value))
            return value
        left = self.integer(args[0])
        if op == "neg":
            return -left
        right = self.integer(args[1])
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "c_div":
            magnitude = z3.Abs(left) / z3.Abs(right)
            quotient = z3.If((left < 0) == (right < 0), magnitude, -magnitude)
            return z3.If(right == 0, 0, quotient)
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        return left == right if op == "==" else left != right


def _counterexample(
    encoder: Encoder, model: z3.ModelRef, observations: tuple[tuple[ir.Symbol, z3.ExprRef], ...], entry: bool
) -> Counterexample:
    def number(expression: z3.ExprRef) -> int:
        evaluated = model.eval(expression, model_completion=True)
        if not z3.is_int_value(evaluated):
            raise ValueError("model observation is not an integer")
        return int(evaluated.as_long())

    scalars = [
        ScalarValue(symbol.id, symbol.name, number(expression))
        for symbol, expression in encoder.symbols.values()
        if not isinstance(symbol.type, ir.ArrayType)
    ]
    scalars.extend(
        ScalarValue(symbol.id, symbol.name, number(expression), "entry-derived") for symbol, expression in observations
    )
    reads = tuple(
        dict.fromkeys(
            ArrayCell(symbol.id, symbol.name, number(index), number(value)) for symbol, index, value in encoder.reads
        )
    )
    return Counterexample("entry-observation" if entry else "abstract-state", tuple(scalars), reads)


def _solve(
    obligation: vc.Obligation,
    target: Target,
    timeout_ms: int,
    version: str,
    observations: tuple[tuple[ir.Symbol, vc.Term], ...],
    factory: Callable[[], Solver],
) -> Result:
    identity = query_id(obligation, target, timeout_ms, version)
    started = perf_counter()
    counterexample = None
    reason = None
    status: Status = "error"
    try:
        encoder = Encoder(target)
        hypotheses = tuple(encoder.boolean(encoder.encode(item.formula)) for item in obligation.hypotheses)
        goal = encoder.boolean(encoder.encode(obligation.goal))
        observed = tuple((symbol, encoder.encode(term)) for symbol, term in observations)
        solver = factory()
        solver.set(timeout=timeout_ms, random_seed=0)
        solver.add(*hypotheses, z3.Not(goal))
        verdict = solver.check()
        if verdict == z3.unsat:
            status = "proved"
        elif verdict == z3.sat:
            status = "failed"
            counterexample = _counterexample(encoder, solver.model(), observed, obligation.kind == "entry")
        elif verdict == z3.unknown:
            diagnostic = solver.reason_unknown()
            status = "timeout" if diagnostic.strip().lower() == "timeout" else "unknown"
            reason = "solver timeout" if status == "timeout" else "solver returned unknown"
        else:
            reason = "solver returned no recognized verdict"
    except (z3.Z3Exception, ValueError, TypeError, RuntimeError) as error:
        status = "error"
        counterexample = None
        reason = f"solver/encoding error ({type(error).__name__})"
    return Result(obligation, identity, status, (perf_counter() - started) * 1000, counterexample, reason)


def _entry(
    query: vc.EntryCheck, target: Target, timeout_ms: int, version: str, factory: Callable[[], Solver]
) -> EntryResult:
    """Check H alone. SAT establishes logical consistency, not reachability or valid allocation."""
    started = perf_counter()
    status: Literal["sat", "unsat", "timeout", "unknown", "error"] = "error"
    reason = None
    try:
        encoder = Encoder(target)
        solver = factory()
        solver.set(timeout=timeout_ms, random_seed=0)
        solver.add(*(encoder.boolean(encoder.encode(h.formula)) for h in query.hypotheses))
        verdict = solver.check()
        if verdict == z3.sat:
            status = "sat"
        elif verdict == z3.unsat:
            status, reason = "unsat", "inconsistent entry hypotheses"
        elif verdict == z3.unknown:
            status = "timeout" if solver.reason_unknown().strip().lower() == "timeout" else "unknown"
            reason = "entry consistency not established"
        else:
            reason = "solver returned no recognized entry verdict"
    except (z3.Z3Exception, ValueError, TypeError, RuntimeError) as error:
        reason = f"entry solver/encoding error ({type(error).__name__})"
    return EntryResult(
        query, entry_query_id(query, target, timeout_ms, version), status, (perf_counter() - started) * 1000, reason
    )


def check(
    program: ir.Program,
    target: Target,
    *,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    solver_factory: Callable[[], Solver] = z3.Solver,
    cache: SolverCache | None = None,
) -> CheckReport:
    """Check every generated formula; retain raw results and invariant admission separately."""
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= MAX_SOLVER_TIMEOUT_MS:
        location = program.functions[0].location if program.functions else SourceLocation("<IR>")
        raise FrontendError("solver-config", f"timeout must be 1..{MAX_SOLVER_TIMEOUT_MS} milliseconds", location)
    conditions = vc.generate(program)
    version = solver_version()
    raw = tuple(
        (
            cache.solve(item, target, timeout_ms, version, solver_factory)
            if cache is not None
            else _solve(item, target, timeout_ms, version, item.observations, solver_factory)
        )
        for item in conditions.obligations
    )
    by_id = {result.obligation.id: result for result in raw}

    def admitted(identifier: str, visiting: frozenset[str] = frozenset()) -> bool:
        if identifier in visiting or identifier not in by_id:
            return False
        result = by_id[identifier]
        return result.status == "proved" and all(
            admitted(dep, visiting | {identifier}) for dep in result.obligation.dependencies
        )

    results = tuple(
        replace(item, dependencies_satisfied=all(admitted(dep) for dep in item.obligation.dependencies)) for item in raw
    )
    entries = tuple(
        (
            cache.entry(query, target, timeout_ms, version, solver_factory)
            if cache is not None
            else _entry(query, target, timeout_ms, version, solver_factory)
        )
        for query in conditions.entry_checks
    )
    passed = all(item.status == "sat" for item in entries) and all(
        item.status == "proved" and item.dependencies_satisfied
        for item in results
        if item.obligation.category == "required"
    )
    return CheckReport(
        conditions.context_id,
        conditions.function,
        target,
        version,
        ENCODING,
        timeout_ms,
        results,
        passed,
        vc_model=conditions.model,
        entry_results=entries,
    )


def render_report(report: CheckReport) -> str:
    """Render conditional formula results without claiming C safety or check deletion."""
    required = [item for item in report.results if item.obligation.category == "required"]
    proved = sum(item.status == "proved" for item in required)
    dependencies_passed = all(item.dependencies_satisfied for item in required)
    entry_consistent = all(item.status == "sat" for item in report.entry_results)
    lines = [
        f"{report.function}: {proved}/{len(required)} required formulas proved; "
        f'required dependencies {"passed" if dependencies_passed else "not passed"}; '
        f'entry consistency {"established" if entry_consistent else "not established"}',
        f"Target: {report.target.triple}, int={report.target.int_bits}, long={report.target.long_bits}",
        f"Z3: {report.solver_version}; timeout={report.timeout_ms} ms per query",
        f"VC model: {report.vc_model}",
        "Conditional mathematical results; overflow reported separately. No check removal authorized.",
    ]
    for entry in report.entry_results:
        detail = entry.reason or "hypotheses satisfiable"
        lines.append(f"Entry consistency {entry.query.function}: {entry.status}; {detail}")
    for item in report.results:
        obligation = item.obligation
        lines.append(
            f"{obligation.category} {obligation.kind}: {item.status} ({item.elapsed_ms:.3f} ms) "
            f"at {obligation.location}; invariant dependencies={item.dependencies_satisfied}"
        )
        lines.append(f"  obligation: {obligation.id}; query: {item.query_id}")
        if item.cache_hit:
            lines.append("  cache: process-local hit; displayed time is the original solver duration")
        if item.reason:
            lines.append(f"  {item.reason}")
        if item.counterexample:
            lines.append(f"  model: {item.counterexample.kind}; not replayed")
            lines.extend(f"    {value.name}={value.value} ({value.origin})" for value in item.counterexample.scalars)
            lines.extend(f"    {read.name}[{read.index}]={read.value}" for read in item.counterexample.reads)
    return "\n".join(lines) + "\n"
