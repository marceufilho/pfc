"""Structured path VCs using delayed assignment substitution and loop cutpoints.

The environment composes the same substitutions as straight-line WP. Branches
split paths; loops introduce arbitrary states and induction obligations. No
termination or frame property is inferred. See ADR-0013 for memory and admission.
"""

from dataclasses import dataclass, field, replace

from pfc import ir, vc
from pfc.config import MAX_VC_PATHS
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.parse import SpecKind

op = vc._op


@dataclass
class State:
    values: dict[str, vc.Term] = field(default_factory=dict)
    bases: dict[str, vc.Term] = field(default_factory=dict)
    lengths: dict[str, vc.Term] = field(default_factory=dict)
    memory: dict[ir.ScalarType, vc.Term] = field(default_factory=dict)
    hypotheses: tuple[vc.Hypothesis, ...] = ()
    dependencies: tuple[str, ...] = ()
    induction: tuple[str, ...] = ()
    result: vc.Term | None = None

    def copy(self) -> "State":
        return replace(
            self,
            values=self.values.copy(),
            bases=self.bases.copy(),
            lengths=self.lengths.copy(),
            memory=self.memory.copy(),
        )

    def assume(self, kind: str, formula: vc.Term) -> None:
        self.hypotheses += (vc.Hypothesis(kind, formula),)  # type: ignore[arg-type]


def value(expression: ir.Expr, state: State) -> vc.Term:
    """Evaluate with composed substitutions; annotation arithmetic is mathematical."""
    if isinstance(expression, ir.Literal):
        return vc.Integer(expression.value)
    if isinstance(expression, ir.Result):
        if state.result is None:
            raise FrontendError("vc-result", "RESULT requires a return binding", expression.location)
        return state.result
    if isinstance(expression, ir.Variable):
        return state.values.get(expression.symbol.id, vc.Reference(expression.symbol))
    if isinstance(expression, ir.Index):
        assert isinstance(expression.array.type, ir.ArrayType)
        index = value(expression.index, state)
        if not state.memory:
            # Standalone substitution algebra: no program or alias assumptions.
            return op("select", vc.Reference(expression.array), index)
        return vc.Read(
            expression.array,
            state.memory[expression.array.type.element],
            op("+", state.bases[expression.array.id], index),
            index,
        )
    if isinstance(expression, ir.Convert):
        return (
            op("ite", truth(expression.operand, state), vc.Integer(1), vc.Integer(0))
            if expression.type is ir.ScalarType.BOOL
            else value(expression.operand, state)
        )
    if isinstance(expression, ir.Unary):
        if expression.operator == "!":
            return op("ite", truth(expression, state), vc.Integer(1), vc.Integer(0))
        operand = value(expression.operand, state)
        return operand if expression.operator == "+" else op("neg", operand)
    if isinstance(expression, ir.Binary):
        if expression.operator not in {"+", "-", "*", "/"}:
            return op("ite", truth(expression, state), vc.Integer(1), vc.Integer(0))
        return op(
            "c_div" if expression.operator == "/" else expression.operator,
            value(expression.left, state),
            value(expression.right, state),
        )
    if isinstance(expression, ir.Separated):
        left, n, right, m = expression.arguments
        assert isinstance(left, ir.Variable) and isinstance(right, ir.Variable)
        if left.type != right.type:
            # Only typed objects, no casts/character accesses in the subset.
            return op(
                "ite",
                vc._and(op(">=", value(n, state), vc.Integer(0)), op(">=", value(m, state), vc.Integer(0))),
                vc.Integer(1),
                vc.Integer(0),
            )
        a, b = state.bases[left.symbol.id], state.bases[right.symbol.id]
        x, y = value(n, state), value(m, state)
        separated = vc._and(
            op(">=", x, vc.Integer(0)),
            op(">=", y, vc.Integer(0)),
            op(
                "||",
                op("||", op("==", x, vc.Integer(0)), op("==", y, vc.Integer(0))),
                op("||", op("<=", op("+", a, x), b), op("<=", op("+", b, y), a)),
            ),
        )
        return op("ite", separated, vc.Integer(1), vc.Integer(0))
    raise FrontendError("vc-expression", "call must be evaluated at a statement boundary", expression.location)


def truth(expression: ir.Expr, state: State) -> vc.Term:
    if isinstance(expression, ir.Convert):
        return truth(expression.operand, state)
    if isinstance(expression, ir.Unary) and expression.operator == "!":
        return op("!", truth(expression.operand, state))
    if isinstance(expression, ir.Binary):
        if expression.operator in {"&&", "||"}:
            return op(expression.operator, truth(expression.left, state), truth(expression.right, state))
        if expression.operator in {"==", "!=", "<", "<=", ">", ">="}:
            return op(expression.operator, value(expression.left, state), value(expression.right, state))
    return op("!=", value(expression, state), vc.Integer(0))


def walk(block: ir.Block) -> tuple[ir.Statement, ...]:
    result: list[ir.Statement] = []
    for statement in block.statements:
        result.append(statement)
        if isinstance(statement, (ir.While, ir.For)):
            result.extend(walk(statement.body))
            if isinstance(statement, ir.For):
                result.extend(item for item in (statement.initializer, statement.update) if item)
        elif isinstance(statement, ir.If):
            result.extend(walk(statement.then_body))
            if statement.else_body:
                result.extend(walk(statement.else_body))
        elif isinstance(statement, ir.Block):
            result.extend(walk(statement))
    return tuple(result)


def calls(expression: ir.Expr) -> tuple[ir.Call, ...]:
    return ((expression,) if isinstance(expression, ir.Call) else ()) + tuple(
        call for child in vc._children(expression) for call in calls(child)
    )


def statement_expressions(statement: ir.Statement) -> tuple[ir.Expr, ...]:
    if isinstance(statement, ir.Local):
        return (statement.initializer,)
    if isinstance(statement, (ir.Return, ir.Assign)):
        return (statement.value,)
    return ()


class Generator:
    def __init__(self, context_id: str, program: ir.Program) -> None:
        self.context_id = context_id
        self.program = program
        self.functions = {function.name: function for function in program.functions}
        self.obligations: list[vc.Obligation] = []
        self.entry_checks: list[vc.EntryCheck] = []
        self.required: dict[str, tuple[str, ...]] = {}
        self.effects: dict[str, set[ir.ScalarType]] = {}
        self.serial = 0
        self.term_count = 0

    def fresh(self, name: str, type_: ir.CType, location: SourceLocation) -> vc.Reference:
        self.serial += 1
        return vc.Reference(ir.Symbol(f"vc:{self.serial}", name, type_, location=location))

    def add(
        self,
        kind: vc.Kind,
        location: SourceLocation,
        state: State,
        goal: vc.Term,
        c_type: ir.ScalarType | None = None,
        *,
        dependencies: tuple[str, ...] | None = None,
        observations: tuple[tuple[ir.Symbol, vc.Term], ...] = (),
    ) -> str:
        self.term_count += vc._measure(goal) + sum(vc._measure(h.formula) for h in state.hypotheses)
        if self.term_count > vc.MAX_VC_TERM_NODES:
            raise FrontendError("vc-complexity", "VC report exceeds the cumulative formula limit", location)
        identifier = str(len(self.obligations))  # Internal slot; replaced by complete content identity in finish().
        self.obligations.append(
            vc.Obligation(
                identifier,
                self.context_id,
                kind,
                "reported" if kind == "overflow" else "required",
                location,
                state.hypotheses,
                goal,
                state.dependencies if dependencies is None else dependencies,
                c_type,
                observations=observations,
            )
        )
        return identifier

    def safety(self, expression: ir.Expr, state: State, path: vc.Term = vc.TRUE, *, annotation: bool = False) -> None:
        for index, child in enumerate(vc._children(expression)):
            child_path = path
            if isinstance(expression, ir.Binary) and index == 1 and expression.operator in {"&&", "||"}:
                condition = truth(expression.left, state)
                child_path = vc._and(path, condition if expression.operator == "&&" else op("!", condition))
            self.safety(child, state, child_path, annotation=annotation)

        def record(kind: vc.Kind, goal: vc.Term, type_: ir.ScalarType | None = None) -> None:
            self.add(kind, expression.location, state, vc._implies(path, goal), type_)

        if isinstance(expression, ir.Index):
            index_value = value(expression.index, state)
            record(
                "bounds",
                vc._and(op("<=", vc.Integer(0), index_value), op("<", index_value, state.lengths[expression.array.id])),
            )
            # vc-v3 (ADR-0025): on this path, the object read holds a value representable in its type.
            # The target rejects padding bits and treats the minimum as a normal value (target.py), so
            # every defined read of a long/bool object lies in its range; this only adds a hypothesis.
            assert isinstance(expression.array.type, ir.ArrayType)
            if state.memory:
                domain = vc._range(value(expression, state), expression.array.type.element)
                state.assume("type-domain", vc._implies(path, domain))
        if isinstance(expression, ir.Binary) and expression.operator == "/":
            left, right = value(expression.left, state), value(expression.right, state)
            goal: vc.Term = op("!=", right, vc.Integer(0))
            if not annotation:
                assert isinstance(expression.type, ir.ScalarType)
                goal = vc._and(
                    goal,
                    op("!", vc._and(op("==", left, vc.Limit(expression.type, "MIN")), op("==", right, vc.Integer(-1)))),
                )
            record("division", goal)
        arithmetic = (
            isinstance(expression, ir.Binary)
            and expression.operator in {"+", "-", "*", "/"}
            or isinstance(expression, ir.Unary)
            and expression.operator == "-"
        )
        if arithmetic and not annotation:
            assert isinstance(expression.type, ir.ScalarType)
            record("overflow", vc._range(value(expression, state), expression.type), expression.type)

    def contract_safety(self, expression: ir.Expr, state: State) -> None:
        start = len(self.obligations)
        premise = state.copy()
        premise.dependencies = tuple(dep for dep in state.dependencies if dep not in state.induction)
        self.safety(expression, premise, annotation=True)
        # safety only appends read domains to these hypotheses. Keep them for the
        # annotation's goal, while retaining its definedness obligations below.
        state.hypotheses = premise.hypotheses
        state.dependencies += tuple(item.id for item in self.obligations[start:])

    def evaluate(self, expression: ir.Expr, state: State) -> vc.Term:
        if isinstance(expression, ir.Convert):
            operand = self.evaluate(expression.operand, state)
            return (
                op("ite", op("!=", operand, vc.Integer(0)), vc.Integer(1), vc.Integer(0))
                if expression.type is ir.ScalarType.BOOL
                else operand
            )
        if isinstance(expression, ir.Call):
            return self.call(expression, state)
        self.safety(expression, state)
        return value(expression, state)

    def call(self, expression: ir.Call, state: State) -> vc.Term:
        callee = self.functions.get(expression.function)
        if callee is None or callee.name not in self.required:
            raise FrontendError("vc-expression", "callee requires an acyclic in-unit definition", expression.location)
        bound = state.copy()
        bound.dependencies = tuple(dep for dep in state.dependencies if dep not in state.induction)
        bound.values = {}
        bound.bases = {}
        bound.lengths = {}
        for parameter, argument in zip(callee.parameters, expression.arguments, strict=True):
            symbol = parameter.symbol
            if isinstance(symbol.type, ir.ArrayType):
                assert isinstance(argument, ir.Variable)
                bound.bases[symbol.id] = state.bases[argument.symbol.id]
                bound.lengths[symbol.id] = state.lengths[argument.symbol.id]
            else:
                bound.values[symbol.id] = self.evaluate(argument, state)
        for parameter in callee.parameters:
            if parameter.length:
                length = bound.values[parameter.length.id]
                identifier = self.add(
                    "call-precondition",
                    expression.location,
                    bound,
                    vc._and(op("<=", vc.Integer(0), length), op("<=", length, bound.lengths[parameter.symbol.id])),
                )
                bound.dependencies += (identifier,)
                bound.lengths[parameter.symbol.id] = length
        for contract in callee.contracts:
            if contract.kind is SpecKind.REQUIRES:
                self.contract_safety(contract.condition, bound)
                condition = truth(contract.condition, bound)
                identifier = self.add("call-precondition", expression.location, bound, condition)
                bound.dependencies += (identifier,)
                bound.assume("requires", condition)
        state.dependencies = tuple(
            dict.fromkeys((*state.dependencies, *bound.dependencies, *self.required[callee.name]))
        )
        for type_ in sorted(self.effects[callee.name]):
            state.memory[type_] = self.fresh("memory", ir.ArrayType(type_), expression.location)
        bound.memory = state.memory.copy()
        bound.hypotheses = state.hypotheses
        bound.dependencies = state.dependencies
        # By-value parameters may change. Postconditions refer to their final values.
        for statement in walk(callee.body):
            if isinstance(statement, ir.Assign) and isinstance(statement.target, ir.Variable):
                symbol = statement.target.symbol
                if symbol.id in bound.values:
                    bound.values[symbol.id] = self.fresh(symbol.name, symbol.type, expression.location)
        result = self.fresh("result", callee.return_type, expression.location)
        bound.result = result
        state.assume("type-domain", vc._range(result, callee.return_type))
        for contract in callee.contracts:
            if contract.kind is SpecKind.ENSURES:
                state.assume("postcondition", truth(contract.condition, bound))
        return result

    def sequence(self, block: ir.Block, states: list[State], function: ir.Function) -> list[State]:
        for statement in block.statements:
            next_states: list[State] = []
            for state in states:
                next_states.extend(self.statement(statement, state, function))
            states = next_states
            if len(states) > MAX_VC_PATHS:
                raise FrontendError("vc-complexity", "too many structured execution paths", statement.location)
        return states

    def statement(self, statement: ir.Statement, state: State, function: ir.Function) -> list[State]:
        if isinstance(statement, ir.Block):
            return self.sequence(statement, [state], function)
        if isinstance(statement, ir.Local):
            state.values[statement.symbol.id] = self.evaluate(statement.initializer, state)
        elif isinstance(statement, ir.Assign):
            result = self.evaluate(statement.value, state)
            if isinstance(statement.target, ir.Variable):
                state.values[statement.target.symbol.id] = result
            else:
                self.safety(statement.target, state)
                target = value(statement.target, state)
                assert isinstance(target, vc.Read) and isinstance(statement.target.array.type, ir.ArrayType)
                state.memory[statement.target.array.type.element] = op("store", target.memory, target.address, result)
        elif isinstance(statement, ir.Contract):
            self.contract_safety(statement.condition, state)
            condition = truth(statement.condition, state)
            identifier = self.add(
                "assert",
                statement.location,
                state,
                condition,
                dependencies=tuple(dep for dep in state.dependencies if dep not in state.induction),
            )
            state.dependencies += (identifier,)
            state.assume("assert", condition)
        elif isinstance(statement, ir.Return):
            state.result = self.evaluate(statement.value, state)
            for contract in function.contracts:
                if contract.kind is SpecKind.ENSURES:
                    self.contract_safety(contract.condition, state)
                    self.add("postcondition", statement.location, state, truth(contract.condition, state))
            return []
        elif isinstance(statement, ir.If):
            self.safety(statement.condition, state)
            condition = truth(statement.condition, state)
            left, right = state.copy(), state.copy()
            left.assume("guard", condition)
            right.assume("guard", op("!", condition))
            return self.sequence(statement.then_body, [left], function) + (
                self.sequence(statement.else_body, [right], function) if statement.else_body else [right]
            )
        elif isinstance(statement, (ir.While, ir.For)):
            if isinstance(statement, ir.For) and statement.initializer:
                self.statement(statement.initializer, state, function)
            return self.loop(statement, state, function)
        else:
            raise FrontendError("vc-subset", "unsupported statement", statement.location)
        return [state]

    def modified(self, block: ir.Block) -> tuple[dict[str, ir.Symbol], set[ir.ScalarType]]:
        scalars: dict[str, ir.Symbol] = {}
        memory: set[ir.ScalarType] = set()
        for statement in walk(block):
            if isinstance(statement, ir.Assign):
                if isinstance(statement.target, ir.Variable):
                    scalars[statement.target.symbol.id] = statement.target.symbol
                else:
                    assert isinstance(statement.target.array.type, ir.ArrayType)
                    memory.add(statement.target.array.type.element)
            for expression in statement_expressions(statement):
                for call in calls(expression):
                    if call.function not in self.effects:
                        raise FrontendError("vc-expression", "callee effects are unavailable", call.location)
                    memory.update(self.effects[call.function])
        return scalars, memory

    def loop(self, loop: ir.While | ir.For, state: State, function: ir.Function) -> list[State]:
        if loop.invariant is None:
            raise FrontendError("vc-invariant", "a loop invariant is required to generate dependent VCs", loop.location)
        invariant = loop.invariant.condition
        self.contract_safety(invariant, state)
        # Outer induction is a premise when validating a nested loop, not a circular certificate.
        validation_deps = tuple(dep for dep in state.dependencies if dep not in state.induction)
        symbols = {item.symbol.id: item.symbol for item in walk(function.body) if isinstance(item, ir.Local)}
        observations = tuple((symbols[key], term) for key, term in state.values.items() if key in symbols)
        entry = self.add(
            "entry",
            loop.invariant.location,
            state,
            truth(invariant, state),
            dependencies=validation_deps,
            observations=observations,
        )
        head = state.copy()
        body = loop.body
        if isinstance(loop, ir.For) and loop.update:
            body = replace(body, statements=(*body.statements, loop.update))
        modified, memory = self.modified(body)
        for key, symbol in modified.items():
            if key in head.values:
                fresh = self.fresh(symbol.name, symbol.type, symbol.location)
                head.values[key] = fresh
                assert isinstance(symbol.type, ir.ScalarType)
                head.assume("type-domain", vc._range(fresh, symbol.type))
        for type_ in sorted(memory):
            head.memory[type_] = self.fresh("memory", ir.ArrayType(type_), loop.location)
        head.assume("invariant", truth(invariant, head))
        guard = truth(loop.condition, head) if loop.condition else vc.TRUE
        induction = head.copy()
        induction.hypotheses = tuple(
            replace(h, kind="induction-hypothesis") if h.kind == "invariant" else h for h in induction.hypotheses
        )
        induction.assume("guard", guard)
        preservation = self.add(
            "preservation", loop.invariant.location, induction, vc.TRUE, dependencies=validation_deps
        )
        head.dependencies += (entry, preservation)
        after = head.copy()
        after.assume("guard", op("!", guard))
        if loop.condition:
            self.safety(loop.condition, head)
        active = head.copy()
        active.induction += (entry, preservation)
        active.assume("guard", guard)
        premise_count = len(active.hypotheses)
        induction_ids = active.induction
        endings = self.sequence(body, [active], function)
        goals: list[vc.Term] = []
        dependencies = list(validation_deps)
        for ending in endings:
            self.contract_safety(invariant, ending)
            extra = ending.hypotheses[premise_count:]
            goals.append(vc._implies(vc._and(*(h.formula for h in extra)), truth(invariant, ending)))
            dependencies.extend(dep for dep in ending.dependencies if dep not in induction_ids)
        item = self.obligations[int(preservation)]
        goal = vc._and(*goals)
        vc._measure(goal)
        self.obligations[int(preservation)] = replace(item, goal=goal, dependencies=tuple(dict.fromkeys(dependencies)))
        return [after]

    def function(self, function: ir.Function) -> None:
        state = State()
        for type_ in (ir.ScalarType.LONG, ir.ScalarType.BOOL):
            state.memory[type_] = self.fresh("memory", ir.ArrayType(type_), function.location)
        for parameter in function.parameters:
            symbol = parameter.symbol
            if isinstance(symbol.type, ir.ArrayType):
                state.bases[symbol.id] = self.fresh(f"base_{symbol.name}", ir.ScalarType.LONG, symbol.location)
                assert parameter.length is not None
                state.lengths[symbol.id] = vc.Reference(parameter.length)
            else:
                state.values[symbol.id] = vc.Reference(symbol)
                state.assume("type-domain", vc._range(vc.Reference(symbol), symbol.type))
        start = len(self.obligations)
        for contract in function.contracts:
            if contract.kind is SpecKind.REQUIRES:
                self.contract_safety(contract.condition, state)
                state.assume("requires", truth(contract.condition, state))
        self.entry_checks.append(vc.EntryCheck(self.context_id, function.name, function.location, state.hypotheses))
        self.sequence(function.body, [state], function)
        self.required[function.name] = tuple(
            item.id for item in self.obligations[start:] if item.category == "required"
        )
        self.effects[function.name] = self.modified(function.body)[1]

    def run(self) -> vc.VCReport:
        visiting: set[str] = set()

        def visit(function: ir.Function) -> None:
            if function.name in self.required:
                return
            if function.name in visiting:
                raise FrontendError("vc-call-cycle", "recursive verification is unsupported", function.location)
            visiting.add(function.name)
            for statement in walk(function.body):
                for expression in statement_expressions(statement):
                    for call in calls(expression):
                        callee = self.functions.get(call.function)
                        if callee is None:
                            raise FrontendError("vc-expression", "callee definition is missing", call.location)
                        visit(callee)
            self.function(function)
            visiting.remove(function.name)

        for function in self.program.functions:
            visit(function)
        return self.finish()

    def finish(self) -> vc.VCReport:
        finished: dict[str, vc.Obligation] = {}

        def resolve(identifier: str, visiting: frozenset[str] = frozenset()) -> vc.Obligation:
            if identifier in visiting:
                raise FrontendError(
                    "vc-dependency", "cyclic proof dependency", self.obligations[int(identifier)].location
                )
            if identifier not in finished:
                item = self.obligations[int(identifier)]
                dependencies = tuple(resolve(dep, visiting | {identifier}).id for dep in item.dependencies)
                canonical = replace(item, dependencies=dependencies)
                finished[identifier] = replace(canonical, id=f"{item.kind}:{vc._digest(canonical)}")
            return finished[identifier]

        return vc.VCReport(
            self.context_id,
            ", ".join(f.name for f in self.program.functions),
            tuple(resolve(item.id) for item in self.obligations),
            entry_checks=tuple(self.entry_checks),
        )
