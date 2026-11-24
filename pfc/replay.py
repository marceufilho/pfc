"""Conservative reconstruction of entry countermodels and real checked-C replay.

Only one straight-line return and one runtime guard are currently reconstructible.
Abstract loop states stay abstract. No unchecked invalid binary is ever executed.
"""

import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pfc import difftest, ir, smt, vc
from pfc.config import C_FLAGS, MAX_DIFF_LENGTH
from pfc.emit import emit
from pfc.environment import resolve_compiler
from pfc.parse import SpecKind
from pfc.target import Target, read_target


@dataclass(frozen=True)
class ConcreteInput:
    scalars: tuple[tuple[str, int], ...]
    arrays: tuple[tuple[str, tuple[int, ...]], ...]


@dataclass(frozen=True)
class Reconstruction:
    input: ConcreteInput | None
    reason: str


@dataclass(frozen=True)
class ReplayReport:
    obligation_id: str
    input: ConcreteInput | None
    replayed: bool
    reason: str
    compilation: difftest.ProcessResult | None = None
    execution: difftest.ProcessResult | None = None


class InvalidExecution(ValueError):
    """Concrete evaluation stopped before executing an undefined operation."""

    def __init__(self, kind: str, expression: ir.Expr) -> None:
        super().__init__(kind)
        self.kind = kind
        self.location = expression.location


def _evaluate(expression: ir.Expr, inputs: ConcreteInput, target: Target, *, annotation: bool = False) -> int:
    """Small independent concrete evaluator, with C short circuit and definedness."""

    def evaluate(child: ir.Expr) -> int:
        return _evaluate(child, inputs, target, annotation=annotation)

    if isinstance(expression, ir.Literal):
        result = expression.value
    elif isinstance(expression, ir.Variable):
        result = dict(inputs.scalars)[expression.symbol.name]
    elif isinstance(expression, ir.Index):
        index = evaluate(expression.index)
        array = dict(inputs.arrays)[expression.array.name]
        if not 0 <= index < len(array):
            raise InvalidExecution("bounds", expression)
        result = array[index]
    elif isinstance(expression, ir.Convert):
        operand = evaluate(expression.operand)
        result = int(bool(operand)) if expression.type is ir.ScalarType.BOOL else operand
    elif isinstance(expression, ir.Unary):
        operand = evaluate(expression.operand)
        result = int(not operand) if expression.operator == "!" else -operand if expression.operator == "-" else operand
    elif isinstance(expression, ir.Binary):
        left = evaluate(expression.left)
        if expression.operator == "&&":
            return int(bool(left) and bool(evaluate(expression.right)))
        if expression.operator == "||":
            return int(bool(left) or bool(evaluate(expression.right)))
        right = evaluate(expression.right)
        operator = expression.operator
        if operator == "/":
            bits = target.int_bits if expression.type is ir.ScalarType.INT else target.long_bits
            if right == 0 or not annotation and left == -(2 ** (bits - 1)) and right == -1:
                raise InvalidExecution("division", expression)
            quotient = abs(left) // abs(right)
            result = quotient if (left < 0) == (right < 0) else -quotient
        elif operator == "+":
            result = left + right
        elif operator == "-":
            result = left - right
        elif operator == "*":
            result = left * right
        else:
            result = int(
                {
                    "==": left == right,
                    "!=": left != right,
                    "<": left < right,
                    "<=": left <= right,
                    ">": left > right,
                    ">=": left >= right,
                }[operator]
            )
    else:
        raise ValueError("unsupported concrete expression")
    if not annotation:
        bits = target.int_bits if expression.type is ir.ScalarType.INT else target.long_bits
        if not -(2 ** (bits - 1)) <= result < 2 ** (bits - 1):
            raise InvalidExecution("overflow", expression)
    return result


def reconstruct(program: ir.Program, report: smt.CheckReport, identifier: str) -> Reconstruction:
    """Validate context and a bounded concrete candidate before permitting a replay."""
    conditions = vc.generate(program)
    expected = next((item for item in conditions.obligations if item.id == identifier), None)
    result = next((item for item in report.results if item.obligation.id == identifier), None)
    if (
        expected is None
        or result is None
        or result.obligation != expected
        or report.context_id != conditions.context_id
        or report.encoding != smt.ENCODING
        or report.solver_version != smt.solver_version()
        or result.query_id != smt.query_id(expected, report.target, report.timeout_ms, report.solver_version)
    ):
        return Reconstruction(None, "source, query or encoding identity mismatch")
    if result.status != "failed" or result.counterexample is None or expected.kind not in {"bounds", "division"}:
        return Reconstruction(None, "a failed safety obligation with a real model is required")
    function = program.functions[0]
    if (
        len(program.functions) != 1
        or function.name == "main"
        or len(function.body.statements) != 1
        or not isinstance(function.body.statements[0], ir.Return)
        or sum(isinstance(p.symbol.type, ir.ArrayType) for p in function.parameters) > 1
    ):
        return Reconstruction(None, "only one straight-line return with at most one array is reconstructible")
    observations = {item.symbol_id: item.value for item in result.counterexample.scalars if item.origin == "model"}
    scalars = tuple(
        (p.symbol.name, observations.get(p.symbol.id, 0))
        for p in function.parameters
        if isinstance(p.symbol.type, ir.ScalarType)
    )
    low, high = report.target.long_range()
    if any(not low <= number <= high for _, number in scalars):
        return Reconstruction(None, "model input is outside the C long range")
    if any(
        p.symbol.type is ir.ScalarType.BOOL and dict(scalars)[p.symbol.name] not in {0, 1} for p in function.parameters
    ):
        return Reconstruction(None, "model bool is not 0 or 1")
    arrays = []
    for parameter in function.parameters:
        if parameter.length is None:
            continue
        length = dict(scalars)[parameter.length.name]
        if not 0 <= length <= MAX_DIFF_LENGTH:
            return Reconstruction(None, "array capacity is outside the bounded reconstruction profile")
        contents = [0] * length
        for read in result.counterexample.reads:
            if read.symbol_id == parameter.symbol.id and 0 <= read.index < length:
                if not low <= read.value <= high:
                    return Reconstruction(None, "array model value is outside C long range")
                if parameter.symbol.type == ir.ArrayType(ir.ScalarType.BOOL) and read.value not in {0, 1}:
                    return Reconstruction(None, "array model bool is not 0 or 1")
                contents[read.index] = read.value
        arrays.append((parameter.symbol.name, tuple(contents)))
    inputs = ConcreteInput(scalars, tuple(arrays))
    try:
        if any(
            not _evaluate(c.condition, inputs, report.target, annotation=True)
            for c in function.contracts
            if c.kind is SpecKind.REQUIRES
        ):
            return Reconstruction(None, "candidate does not satisfy entry contracts")
    except (ValueError, KeyError):
        return Reconstruction(None, "entry contracts cannot be evaluated concretely")
    try:
        _evaluate(function.body.statements[0].value, inputs, report.target)
    except InvalidExecution as error:
        if error.kind == expected.kind and error.location == expected.location:
            return Reconstruction(inputs, "entry inputs reach exactly the reported invalid operation")
        return Reconstruction(None, "candidate reaches a different invalid operation")
    except (ValueError, KeyError):
        return Reconstruction(None, "unsupported concrete expression")
    return Reconstruction(None, "candidate does not reproduce the reported failure")


def _driver(function: ir.Function, inputs: ConcreteInput, target: Target) -> str:
    parameters = ", ".join(ir._declaration(p.symbol) for p in function.parameters) or "void"
    lines = [
        "#include <sys/resource.h>",
        f"{function.return_type} {function.name}({parameters});",
        "int main(void) {",
        "struct rlimit limit = {0, 0};",
        "if (setrlimit(RLIMIT_CORE, &limit) != 0) return 2;",
    ]
    for name, contents in inputs.arrays:
        parameter = next(p for p in function.parameters if p.symbol.name == name)
        assert isinstance(parameter.symbol.type, ir.ArrayType)
        values = ", ".join(target.long_literal(v) for v in contents) or "0"
        lines.append(f"{parameter.symbol.type.element} {name}[{max(1,len(contents))}] = {{{values}}};")
    arguments = [
        (
            p.symbol.name
            if isinstance(p.symbol.type, ir.ArrayType)
            else target.long_literal(dict(inputs.scalars)[p.symbol.name])
        )
        for p in function.parameters
    ]
    lines.extend([f'(void){function.name}({", ".join(arguments)});', "return 0;", "}"])
    return "\n".join(lines) + "\n"


def replay(
    program: ir.Program,
    report: smt.CheckReport,
    identifier: str,
    *,
    compiler: str,
    runner: Callable[[tuple[str, ...], Path], difftest.ProcessResult] = difftest.invoke,
) -> ReplayReport:
    """Compile only checked C; SIGILL is evidence only after exact-site reconstruction."""
    candidate = reconstruct(program, report, identifier)
    if candidate.input is None:
        return ReplayReport(identifier, None, False, candidate.reason)
    if read_target(compiler) != report.target:
        return ReplayReport(identifier, candidate.input, False, "compiler target differs from solver target")
    output = emit(program, report.target, mode="checked")
    if len(output.decisions) != 1 or output.decisions[0].obligation_ids != (identifier,) or output.decisions[0].removed:
        return ReplayReport(identifier, candidate.input, False, "replay requires exactly the intended retained guard")
    executable = resolve_compiler(compiler)
    with TemporaryDirectory(prefix="pfc-replay-") as temporary:
        directory = Path(temporary)
        source, driver, binary = (directory / name for name in ("kernel.c", "main.c", "replay"))
        source.write_text(output.c, encoding="utf-8")
        driver.write_text(_driver(program.functions[0], candidate.input, report.target), encoding="utf-8")
        compiled = runner((executable, *C_FLAGS, str(source), str(driver), "-o", str(binary)), directory)
        if compiled.status != "ok":
            return ReplayReport(identifier, candidate.input, False, "checked replay did not compile", compiled)
        execution = runner((str(binary),), directory)
    passed = execution.status == "error" and execution.returncode == -signal.SIGILL
    return ReplayReport(
        identifier,
        candidate.input,
        passed,
        "exact-site SIGILL observed" if passed else "expected SIGILL absent",
        compiled,
        execution,
    )
