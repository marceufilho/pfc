"""Reference interpreter for the typed IR (evaluation-architecture.md D1, roadmap E2).

Executes a lowered program over unbounded Python integers and rejects, instead of guessing, any
execution that C would leave undefined or that the contracts exclude:

- every C arithmetic result is checked against its IR type and the target widths (`Overflow`);
- `/` truncates toward zero; a zero divisor is `DivisionByZero`; MIN / -1 is `Overflow`;
- `&&` and `||` short-circuit; `!` and comparisons yield 0/1 of type int;
- every read/write index is checked against the declared `<array>_len` value (`OutOfBounds`);
- `REQUIRES` of the entry function and of every callee is evaluated with mathematical (unbounded)
  arithmetic; a false precondition is `PreconditionViolated`; a zero divisor inside an annotation is
  `AnnotationUndefined` rather than being totalized;
- `SEPARATED(a, a_len, b, b_len)` holds when either length is zero or the two buffers are distinct
  objects; the same buffer passed under two names is not separated;
- `INVARIANT`, `ASSERT` and `ENSURES` are evaluated mathematically and reported as annotation
  violations without stopping the execution; they are proof annotations, not runtime monitors;
- a step budget guards non-termination (`Inconclusive`).

Independence: this module shares the frontend (`parse`, `lower`, `ir`) and the target widths with the
compiler, and nothing else. It does not import the VC generator, the solver layer, the emitter, the
authorization module or the compiler's replay evaluator, and it is not a proof of anything: it is the
reference observation for differential experiments.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from pfc import ir
from pfc.diagnostics import SourceLocation
from pfc.parse import SpecKind
from pfc.target import Target

DEFAULT_FUEL = 100_000_000
RejectionKind = Literal[
    "InvalidInput",
    "PreconditionViolated",
    "OutOfBounds",
    "Overflow",
    "DivisionByZero",
    "AnnotationUndefined",
    "Inconclusive",
    "Unsupported",
]


@dataclass(frozen=True)
class Inputs:
    """Concrete arguments by parameter name; arrays are sequences, lengths are ordinary scalars."""

    scalars: Mapping[str, int]
    arrays: Mapping[str, Sequence[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    result: int
    arrays: dict[str, tuple[int, ...]]
    steps: int
    annotation_violations: tuple[str, ...]


@dataclass(frozen=True)
class Rejection:
    kind: RejectionKind
    message: str
    location: SourceLocation | None
    annotation_violations: tuple[str, ...] = ()  # Observed before the rejection; annotations never stop execution.


class _Reject(Exception):
    def __init__(self, kind: RejectionKind, message: str, location: SourceLocation | None) -> None:
        super().__init__(message)
        self.rejection = Rejection(kind, message, location)


class _Return(Exception):
    def __init__(self, value: int) -> None:
        super().__init__()
        self.value = value


@dataclass(frozen=True)
class LoopHead:
    """One loop-head state handed to an observer: function, INVARIANT location and the named scalars in scope."""

    function: str
    location: SourceLocation
    scalars: Mapping[str, int]


Observer = Callable[[LoopHead], None]


@dataclass
class _Frame:
    scalars: dict[str, int]
    arrays: dict[str, list[int]]
    lengths: dict[str, str]  # array symbol id -> length symbol id
    result: int | None = None


class _Interpreter:
    def __init__(self, program: ir.Program, target: Target, fuel: int, observer: Observer | None = None) -> None:
        self.functions = {function.name: function for function in program.functions}
        self.target = target
        self.fuel = fuel
        self.steps = 0
        self.violations: list[str] = []
        self.observer = observer
        self.names = _symbol_names(program) if observer is not None else {}
        self.stack: list[str] = []

    # -- ranges -------------------------------------------------------------------------------------

    def _range(self, type_: ir.CType) -> tuple[int, int]:
        if type_ is ir.ScalarType.LONG:
            return self.target.long_range()
        if type_ is ir.ScalarType.INT:
            return -(2 ** (self.target.int_bits - 1)), 2 ** (self.target.int_bits - 1) - 1
        if type_ is ir.ScalarType.BOOL:
            return 0, 1
        raise _Reject("Unsupported", f"no value range for {type_}", None)

    def _fits(self, value: int, type_: ir.CType, location: SourceLocation) -> int:
        low, high = self._range(type_)
        if not low <= value <= high:
            raise _Reject("Overflow", f"{type_} result {value} is outside [{low}, {high}]", location)
        return value

    # -- expressions ----------------------------------------------------------------------------------

    def evaluate(self, expression: ir.Expr, frame: _Frame, *, annotation: bool) -> int:
        self._tick(expression.location)
        if isinstance(expression, ir.Literal):
            return expression.value
        if isinstance(expression, ir.Result):
            if not annotation or frame.result is None:
                raise _Reject("Unsupported", "RESULT outside a postcondition", expression.location)
            return frame.result
        if isinstance(expression, ir.Variable):
            if isinstance(expression.type, ir.ArrayType):
                raise _Reject("Unsupported", "array used as a scalar value", expression.location)
            return frame.scalars[expression.symbol.id]
        if isinstance(expression, ir.Index):
            return self._read(expression, frame, annotation)
        if isinstance(expression, ir.Convert):
            return self._convert(expression, frame, annotation)
        if isinstance(expression, ir.Unary):
            return self._unary(expression, frame, annotation)
        if isinstance(expression, ir.Binary):
            return self._binary(expression, frame, annotation)
        if isinstance(expression, ir.Separated):
            return self._separated(expression, frame)
        if isinstance(expression, ir.Call):
            raise _Reject("Unsupported", "call outside a statement boundary", expression.location)
        raise _Reject("Unsupported", f"expression {type(expression).__name__}", expression.location)

    def _index(self, expression: ir.Index, frame: _Frame, annotation: bool) -> tuple[list[int], int]:
        index = self.evaluate(expression.index, frame, annotation=annotation)
        buffer = frame.arrays[expression.array.id]
        declared = frame.scalars[frame.lengths[expression.array.id]]
        if not 0 <= index < declared or index >= len(buffer):
            raise _Reject(
                "OutOfBounds", f"{expression.array.name}[{index}] with declared length {declared}", expression.location
            )
        return buffer, index

    def _read(self, expression: ir.Index, frame: _Frame, annotation: bool) -> int:
        buffer, index = self._index(expression, frame, annotation)
        return buffer[index]

    def _convert(self, expression: ir.Convert, frame: _Frame, annotation: bool) -> int:
        operand = self.evaluate(expression.operand, frame, annotation=annotation)
        if expression.type is ir.ScalarType.BOOL:
            return int(operand != 0)
        if expression.type is ir.ScalarType.LONG:
            return operand  # int and bool values are always representable in long.
        raise _Reject("Unsupported", f"conversion to {expression.type}", expression.location)

    def _unary(self, expression: ir.Unary, frame: _Frame, annotation: bool) -> int:
        operand = self.evaluate(expression.operand, frame, annotation=annotation)
        if expression.operator == "!":
            return int(operand == 0)
        if expression.operator == "+":
            return operand
        if expression.operator == "-":
            return -operand if annotation else self._fits(-operand, expression.type, expression.location)
        raise _Reject("Unsupported", f"unary operator {expression.operator}", expression.location)

    def _binary(self, expression: ir.Binary, frame: _Frame, annotation: bool) -> int:
        operator = expression.operator
        left = self.evaluate(expression.left, frame, annotation=annotation)
        if operator == "&&":
            return int(left != 0 and self.evaluate(expression.right, frame, annotation=annotation) != 0)
        if operator == "||":
            return int(left != 0 or self.evaluate(expression.right, frame, annotation=annotation) != 0)
        right = self.evaluate(expression.right, frame, annotation=annotation)
        comparisons = {
            "==": left == right,
            "!=": left != right,
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
        }
        if operator in comparisons:
            return int(comparisons[operator])
        if operator == "/":
            if right == 0:
                kind: RejectionKind = "AnnotationUndefined" if annotation else "DivisionByZero"
                raise _Reject(kind, "division by zero", expression.location)
            quotient = abs(left) // abs(right)
            result = quotient if (left < 0) == (right < 0) else -quotient
        elif operator == "+":
            result = left + right
        elif operator == "-":
            result = left - right
        elif operator == "*":
            result = left * right
        else:
            raise _Reject("Unsupported", f"binary operator {operator}", expression.location)
        return result if annotation else self._fits(result, expression.type, expression.location)

    def _separated(self, expression: ir.Separated, frame: _Frame) -> int:
        arguments = expression.arguments
        if (
            len(arguments) != 4
            or not isinstance(arguments[0], ir.Variable)
            or not isinstance(arguments[2], ir.Variable)
        ):
            raise _Reject("Unsupported", "SEPARATED needs two array/length pairs", expression.location)
        n = self.evaluate(arguments[1], frame, annotation=True)
        m = self.evaluate(arguments[3], frame, annotation=True)
        if n < 0 or m < 0:
            return 0
        left, right = frame.arrays[arguments[0].symbol.id], frame.arrays[arguments[2].symbol.id]
        return int(n == 0 or m == 0 or left is not right)

    # -- statements -----------------------------------------------------------------------------------

    def _tick(self, location: SourceLocation) -> None:
        self.steps += 1
        if self.steps > self.fuel:
            raise _Reject("Inconclusive", f"step budget of {self.fuel} exhausted", location)

    def _truth(self, expression: ir.Expr, frame: _Frame, *, annotation: bool = False) -> bool:
        return self.evaluate(expression, frame, annotation=annotation) != 0

    def _annotation(self, contract: ir.Contract, frame: _Frame) -> None:
        if not self._truth(contract.condition, frame, annotation=True):
            location = contract.location
            text = f"{contract.kind} at {location.line}:{location.column}"
            if text not in self.violations:  # One entry per annotation site, in order of first violation.
                self.violations.append(text)

    def _head(self, invariant: ir.Contract | None, frame: _Frame) -> bool:
        if invariant is not None:
            self._annotation(invariant, frame)
            if self.observer is not None:
                # Observation only: the state is copied by name and the observer cannot alter the run; an
                # exception it raises propagates (it is a harness defect, never a program outcome).
                scalars = {self.names[k]: v for k, v in frame.scalars.items() if k in self.names}
                self.observer(LoopHead(self.stack[-1], invariant.location, scalars))
        return True

    def _value(self, expression: ir.Expr, frame: _Frame) -> int:
        if isinstance(expression, ir.Call):
            return self._call(expression, frame)
        return self.evaluate(expression, frame, annotation=False)

    def _call(self, call: ir.Call, frame: _Frame) -> int:
        self._tick(call.location)
        callee = self.functions.get(call.function)
        if callee is None:
            raise _Reject("Unsupported", f"call to undefined function {call.function}", call.location)
        scalars: dict[str, int] = {}
        arrays: dict[str, list[int]] = {}
        for parameter, argument in zip(callee.parameters, call.arguments, strict=True):
            if isinstance(parameter.symbol.type, ir.ArrayType):
                if not isinstance(argument, ir.Variable):
                    raise _Reject("Unsupported", "array argument must name an array", call.location)
                arrays[parameter.symbol.id] = frame.arrays[argument.symbol.id]
            else:
                scalars[parameter.symbol.id] = self.evaluate(argument, frame, annotation=False)
        inner = _Frame(scalars, arrays, _lengths(callee))
        return self._execute(callee, inner, call.location)

    def _execute(self, function: ir.Function, frame: _Frame, call_site: SourceLocation | None) -> int:
        for contract in function.contracts:
            if contract.kind is SpecKind.REQUIRES and not self._truth(contract.condition, frame, annotation=True):
                where = call_site or contract.location
                raise _Reject("PreconditionViolated", f"REQUIRES of {function.name} is false", where)
        self.stack.append(function.name)
        try:
            self._block(function.body, frame)
        except _Return as returned:
            frame.result = self._fits(returned.value, function.return_type, function.location)
            for contract in function.contracts:
                if contract.kind is SpecKind.ENSURES:
                    self._annotation(contract, frame)
            return frame.result
        finally:
            self.stack.pop()
        raise _Reject("Unsupported", f"{function.name} finished without a return statement", function.location)

    def _block(self, block: ir.Block, frame: _Frame) -> None:
        for statement in block.statements:
            self._statement(statement, frame)

    def _statement(self, statement: ir.Statement, frame: _Frame) -> None:
        self._tick(statement.location)
        if isinstance(statement, ir.Local):
            frame.scalars[statement.symbol.id] = self._fits(
                self._value(statement.initializer, frame), statement.symbol.type, statement.location
            )
        elif isinstance(statement, ir.Assign):
            self._assign(statement, frame)
        elif isinstance(statement, ir.Block):
            self._block(statement, frame)
        elif isinstance(statement, ir.If):
            if self._truth(statement.condition, frame):
                self._block(statement.then_body, frame)
            elif statement.else_body is not None:
                self._block(statement.else_body, frame)
        elif isinstance(statement, ir.While):
            # The invariant is a loop-head annotation: checked before every condition evaluation, exit included.
            while self._head(statement.invariant, frame) and self._truth(statement.condition, frame):
                self._block(statement.body, frame)
        elif isinstance(statement, ir.For):
            if statement.initializer is not None:
                self._statement(statement.initializer, frame)
            while self._head(statement.invariant, frame) and (
                statement.condition is None or self._truth(statement.condition, frame)
            ):
                if statement.condition is None:
                    self._tick(statement.location)  # nothing else is evaluated per iteration of `for (;;)`
                self._block(statement.body, frame)
                if statement.update is not None:
                    self._statement(statement.update, frame)
        elif isinstance(statement, ir.Return):
            raise _Return(self._value(statement.value, frame))
        elif isinstance(statement, ir.Contract):
            self._annotation(statement, frame)
        else:
            raise _Reject("Unsupported", f"statement {type(statement).__name__}", statement.location)

    def _assign(self, statement: ir.Assign, frame: _Frame) -> None:
        value = self._value(statement.value, frame)
        target = statement.target
        if isinstance(target, ir.Variable):
            frame.scalars[target.symbol.id] = self._fits(value, target.type, statement.location)
            return
        buffer, index = self._index(target, frame, False)
        buffer[index] = self._fits(value, target.type, statement.location)


def _symbol_names(program: ir.Program) -> dict[str, str]:
    """Symbol id -> source name for every parameter and local of the program (observer reporting only)."""
    names: dict[str, str] = {}

    def block(statements: ir.Block) -> None:
        for statement in statements.statements:
            if isinstance(statement, ir.Local):
                names[statement.symbol.id] = statement.symbol.name
            elif isinstance(statement, ir.Block):
                block(statement)
            elif isinstance(statement, ir.If):
                block(statement.then_body)
                if statement.else_body is not None:
                    block(statement.else_body)
            elif isinstance(statement, ir.While):
                block(statement.body)
            elif isinstance(statement, ir.For):
                if isinstance(statement.initializer, ir.Local):
                    names[statement.initializer.symbol.id] = statement.initializer.symbol.name
                block(statement.body)

    for function in program.functions:
        for parameter in function.parameters:
            names[parameter.symbol.id] = parameter.symbol.name
        block(function.body)
    return names


def _lengths(function: ir.Function) -> dict[str, str]:
    return {p.symbol.id: p.length.id for p in function.parameters if p.length is not None}


def _bind(function: ir.Function, inputs: Inputs, target: Target) -> _Frame:
    """Validate the concrete entry state before any statement runs."""
    names = {p.symbol.name for p in function.parameters}
    given = set(inputs.scalars) | set(inputs.arrays)
    if given != names or set(inputs.scalars) & set(inputs.arrays):
        extra = sorted(given - names)
        missing = sorted(names - given)
        raise _Reject("InvalidInput", f"missing parameters {missing}, unexpected {extra}", function.location)
    low, high = target.long_range()
    scalars: dict[str, int] = {}
    arrays: dict[str, list[int]] = {}
    copies: dict[int, list[int]] = {}  # One buffer object passed under two names stays one buffer (aliasing).
    for parameter in function.parameters:
        symbol = parameter.symbol
        if isinstance(symbol.type, ir.ArrayType):
            values = inputs.arrays[symbol.name]
            element_low, element_high = (0, 1) if symbol.type.element is ir.ScalarType.BOOL else (low, high)
            if any(type(v) is not int or not element_low <= v <= element_high for v in values):
                kind = "_Bool" if symbol.type.element is ir.ScalarType.BOOL else "long"
                raise _Reject("InvalidInput", f"{symbol.name} element outside the {kind} range", symbol.location)
            arrays[symbol.id] = copies.setdefault(id(values), list(values))
            continue
        value = inputs.scalars[symbol.name]
        bounds = (0, 1) if symbol.type is ir.ScalarType.BOOL else (low, high)
        if type(value) is not int or not bounds[0] <= value <= bounds[1]:
            raise _Reject("InvalidInput", f"{symbol.name}={value!r} outside the {symbol.type} range", symbol.location)
        scalars[symbol.id] = value
    for parameter in function.parameters:
        if parameter.length is None:
            continue
        declared = scalars[parameter.length.id]
        if declared < 0:
            raise _Reject("InvalidInput", f"{parameter.length.name} is negative", parameter.length.location)
        if len(arrays[parameter.symbol.id]) < declared:
            raise _Reject(
                "InvalidInput",
                f"buffer {parameter.symbol.name} is shorter than its declared length {declared}",
                parameter.symbol.location,
            )
    return _Frame(scalars, arrays, _lengths(function))


def run(
    program: ir.Program,
    inputs: Inputs,
    *,
    target: Target,
    function: str | None = None,
    fuel: int = DEFAULT_FUEL,
    observer: Observer | None = None,
) -> Observation | Rejection:
    """Execute one function on concrete inputs; return the full final state or a typed rejection.

    `observer`, when given, is called with every loop-head state (before each condition evaluation, exit
    included) and never changes the outcome; it exists for the B2 dynamic-inference baseline.
    """
    functions = {f.name: f for f in program.functions}
    if not functions:
        raise ValueError("program has no functions")
    name = function if function is not None else program.functions[0].name
    if name not in functions:
        raise ValueError(f"unknown function {name!r}")
    if type(fuel) is not int or fuel <= 0:
        raise ValueError("fuel must be a positive integer")
    entry = functions[name]
    interpreter = _Interpreter(program, target, fuel, observer)
    try:
        frame = _bind(entry, inputs, target)
        result = interpreter._execute(entry, frame, None)
    except _Reject as rejected:
        return replace(rejected.rejection, annotation_violations=tuple(interpreter.violations))
    arrays = {p.symbol.name: tuple(frame.arrays[p.symbol.id]) for p in entry.parameters if p.length is not None}
    return Observation(result, arrays, interpreter.steps, tuple(interpreter.violations))


def evaluate_condition(
    program: ir.Program, inputs: Inputs, expression: ir.Expr, *, target: Target, function: str | None = None
) -> bool | None:
    """Truth of an annotation expression (a REQUIRES conjunct) on concrete inputs at function entry.

    Mathematical reading, as in annotations; returns None when the inputs are invalid for the signature or the
    expression is undefined on them (division by zero inside the annotation). Used by the expected-trap campaign to
    decide whether a rendered entry check must fire; never consults the compiler.
    """
    functions = {f.name: f for f in program.functions}
    name = function if function is not None else program.functions[0].name
    if name not in functions:
        raise ValueError(f"unknown function {name!r}")
    interpreter = _Interpreter(program, target, DEFAULT_FUEL, None)
    try:
        frame = _bind(functions[name], inputs, target)
        return interpreter._truth(expression, frame, annotation=True)
    except _Reject:
        return None
