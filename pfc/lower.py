"""Canonical typed subset validation and immutable IR construction.

Calls and memory predicates preserve syntax/types without granting proof authority.
Verification capabilities are checked separately by vc.py.
"""

from typing import NoReturn

from pycparser import c_ast

from pfc import ir
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.parse import MARKERS, ParsedUnit, Spec, SpecKind
from pfc.subset import _location, _scalar_type

# Portable C11 minima: avoid assuming a target ABI before the encoding stage.
PORTABLE_INT_MAX = 32767
PORTABLE_LONG_MAX = 2147483647
_RESERVED_NAMES = {kind.value for kind in SpecKind} | {"RESULT", "PFC_SPEC_H", "SPEC_KEEP", "VERIFIED"}


def _convert(expression: ir.Expr, target: ir.ScalarType) -> ir.Expr:
    if expression.type == target:
        return expression
    return ir.Convert(target, expression, location=expression.location)


def _returns(statement: ir.Statement) -> bool:
    """Conservative structural return check; loops are not termination proofs."""
    if isinstance(statement, ir.Return):
        return True
    if isinstance(statement, ir.Block):
        return any(_returns(child) for child in statement.statements)
    return (
        isinstance(statement, ir.If)
        and statement.else_body is not None
        and _returns(statement.then_body)
        and _returns(statement.else_body)
    )


class _Lowerer:
    def __init__(
        self,
        filename: str,
        name: str,
        signatures: dict[str, tuple[ir.ScalarType, tuple[ir.Parameter, ...]]] | None = None,
    ) -> None:
        self.signatures = signatures or {}
        self.filename = filename
        self.name = name
        self.symbols: dict[str, ir.Symbol] = {}
        self.serial = 0
        self.return_type = ir.ScalarType.LONG
        self.in_postcondition = False

    def location(self, node: c_ast.Node) -> SourceLocation:
        return _location(node, self.filename)

    def fail(self, rule: str, message: str, node: c_ast.Node) -> NoReturn:
        raise FrontendError(rule, message, self.location(node))

    def name_available(self, name: str | None, node: c_ast.Node) -> str:
        if not name or name in self.symbols or name in _RESERVED_NAMES or name.startswith("__spec_"):
            self.fail("ir-name", "expected a fresh named binding; shadowing and compiler names are unsupported", node)
        return name

    def symbol(self, declaration: c_ast.Decl, *, parameter: bool = False) -> ir.Symbol:
        name = self.name_available(declaration.name, declaration)
        node = declaration.type
        if isinstance(node, c_ast.ArrayDecl):
            if not parameter or node.dim is not None or node.dim_quals:
                self.fail("ir-array-shape", "array parameters require one unsized [] dimension", declaration)
            scalar = self.scalar_type(node.type)
            type_: ir.CType = ir.ArrayType(scalar)
            qualifiers = node.type.quals if isinstance(node.type, c_ast.TypeDecl) else []
        else:
            type_ = self.scalar_type(node)
            qualifiers = node.quals if isinstance(node, c_ast.TypeDecl) else []
        if declaration.align or declaration.bitsize or any(item != "const" for item in qualifiers):
            self.fail("ir-declaration", "alignment, bitfields and non-const qualifiers are unsupported", declaration)
        symbol = ir.Symbol(
            f"{self.name}:{self.serial}", name, type_, "const" in qualifiers, location=self.location(declaration)
        )
        self.serial += 1
        self.symbols[name] = symbol
        return symbol

    def scalar_type(self, node: c_ast.Node) -> ir.ScalarType:
        name = _scalar_type(node)
        if name not in {"long", "_Bool"}:
            self.fail("ir-type", "expected a scalar long or bool declaration", node)
        return ir.ScalarType(name)

    def signature(self, declaration: c_ast.Decl) -> tuple[ir.ScalarType, tuple[ir.Parameter, ...]]:
        if declaration.name in _RESERVED_NAMES:
            self.fail("ir-name", "function name conflicts with annotation transport", declaration)
        node = declaration.type
        if not isinstance(node, c_ast.FuncDecl) or node.args is None:
            self.fail("ir-prototype", "use a parameter prototype, including (void) for no parameters", declaration)
        self.return_type = self.scalar_type(node.type)
        if isinstance(node.type, c_ast.TypeDecl) and node.type.quals:
            self.fail("ir-type", "qualified function return types are unsupported", declaration)
        params = node.args.params
        if len(params) == 1 and isinstance(params[0], c_ast.Typename) and _scalar_type(params[0].type) == "void":
            return self.return_type, ()
        symbols = []
        for parameter in params:
            if not isinstance(parameter, c_ast.Decl) or parameter.init is not None:
                self.fail("ir-prototype", "parameters must be named declarations without initializers", parameter)
            symbols.append(self.symbol(parameter, parameter=True))
        parameters = []
        for symbol in symbols:
            length = None
            if isinstance(symbol.type, ir.ArrayType):
                length = self.symbols.get(f"{symbol.name}_len")
                if length is None or length.type is not ir.ScalarType.LONG:
                    self.fail("ir-array-length", "each array needs a long parameter named <array>_len", declaration)
            parameters.append(ir.Parameter(symbol, length))
        return self.return_type, tuple(parameters)

    def scalar(self, node: c_ast.Node, *, allow_call: bool = False) -> ir.Expr:
        expression = self.expression(node, allow_call=allow_call)
        if not isinstance(expression.type, ir.ScalarType):
            self.fail("ir-type", "array values are only supported as indexed operands", node)
        return expression

    def expression(self, node: c_ast.Node, *, allow_call: bool = False) -> ir.Expr:
        location = self.location(node)
        if isinstance(node, c_ast.ID):
            if node.name == "RESULT" and self.in_postcondition:
                return ir.Result(self.return_type, location=location)
            symbol = self.symbols.get(node.name)
            if symbol is None:
                self.fail("ir-name", "identifier has no visible initialized binding", node)
            return ir.Variable(symbol.type, symbol, location=location)
        if isinstance(node, c_ast.Constant):
            spelling = node.value
            digits = spelling.rstrip("lL")
            base = 16 if digits.lower().startswith("0x") else 8 if digits.startswith("0") else 10
            significant = (digits[2:] if base == 16 else digits).lstrip("0") or "0"
            # Even octal needs at most 11 digits for the portable long range.
            # Bound conversion before Python's arbitrary-precision int parser.
            if len(significant) > 11:
                self.fail("ir-literal-range", "literal exceeds the portable signed range", node)
            value = int(significant, base)
            type_ = ir.ScalarType.LONG if spelling != digits else ir.ScalarType.INT
            limit = PORTABLE_LONG_MAX if type_ is ir.ScalarType.LONG else PORTABLE_INT_MAX
            if value > limit:
                self.fail(
                    "ir-literal-range",
                    "literal exceeds the portable signed range; target-specific literals are deferred",
                    node,
                )
            return ir.Literal(type_, spelling, value, location=location)
        if isinstance(node, c_ast.ArrayRef):
            array = self.expression(node.name)
            if not isinstance(array, ir.Variable) or not isinstance(array.type, ir.ArrayType):
                self.fail("ir-type", "indexing requires an array parameter", node)
            return ir.Index(array.type.element, array.symbol, self.scalar(node.subscript), location=location)
        if isinstance(node, c_ast.UnaryOp):
            operand = self.scalar(node.expr)
            target = ir.ScalarType.LONG if operand.type is ir.ScalarType.LONG else ir.ScalarType.INT
            if node.op == "!":
                return ir.Unary(ir.ScalarType.INT, node.op, operand, location=location)
            return ir.Unary(target, node.op, _convert(operand, target), location=location)
        if isinstance(node, c_ast.BinaryOp):
            left, right = self.expression(node.left), self.expression(node.right)
            if isinstance(left.type, ir.ArrayType) or isinstance(right.type, ir.ArrayType):
                rule = "subset-pointer-arith" if node.op in {"+", "-"} else "ir-type"
                self.fail(rule, "array addresses cannot be scalar arithmetic or comparison operands", node)
            common = ir.ScalarType.LONG if ir.ScalarType.LONG in (left.type, right.type) else ir.ScalarType.INT
            if node.op not in {"&&", "||"}:
                left, right = _convert(left, common), _convert(right, common)
            result_type = common if node.op in {"+", "-", "*", "/"} else ir.ScalarType.INT
            return ir.Binary(result_type, node.op, left, right, location=location)
        if isinstance(node, c_ast.FuncCall):
            return self.call(node, allow_call=allow_call)
        if isinstance(node, Spec):
            return self.separated(node)
        self.fail("ir-expression", "expression cannot be represented without unsupported effects", node)

    def call(self, node: c_ast.FuncCall, *, allow_call: bool) -> ir.Call:
        if not allow_call:
            self.fail(
                "subset-call-position", "calls require a whole scalar initializer, assignment RHS or return", node
            )
        assert isinstance(node.name, c_ast.ID)
        if node.name.name in self.symbols:
            self.fail("subset-indirect-call", "call target is hidden by an object binding", node)
        signature = self.signatures.get(node.name.name)
        if signature is None:
            self.fail("subset-call", "callee has no supported definition", node)
        result_type, parameters = signature
        arguments = node.args.exprs if node.args else []
        if len(arguments) != len(parameters):
            self.fail("subset-call-arity", "argument count differs from the callee prototype", node)
        converted: list[ir.Expr] = []
        for argument, parameter in zip(arguments, parameters, strict=True):
            argument_value = self.expression(argument)
            expected = parameter.symbol
            if isinstance(expected.type, ir.ArrayType):
                if (
                    not isinstance(argument_value, ir.Variable)
                    or argument_value.type != expected.type
                    or argument_value.symbol.const
                    and not expected.const
                ):
                    self.fail(
                        "subset-call-type", "array argument type or const qualification is incompatible", argument
                    )
                converted.append(argument_value)
            else:
                if not isinstance(argument_value.type, ir.ScalarType):
                    self.fail("subset-call-type", "scalar argument required", argument)
                converted.append(_convert(argument_value, expected.type))
        return ir.Call(result_type, node.name.name, tuple(converted), location=self.location(node))

    def separated(self, node: Spec) -> ir.Separated:
        if node.kind is not SpecKind.SEPARATED:
            self.fail("ir-contract", "only SEPARATED can be a contract predicate", node)
        arguments = tuple(self.expression(argument) for argument in node.arguments)
        for index, argument in enumerate(arguments):
            if index % 2 == 0:
                if not isinstance(argument, ir.Variable) or not isinstance(argument.type, ir.ArrayType):
                    self.fail("subset-separated", "SEPARATED regions must start at array parameters", node)
            elif not isinstance(argument.type, ir.ScalarType):
                self.fail("subset-separated", "SEPARATED lengths must be scalar integers", node)
        converted_lengths = tuple(
            _convert(argument, ir.ScalarType.LONG) if index % 2 else argument
            for index, argument in enumerate(arguments)
        )
        return ir.Separated(ir.ScalarType.BOOL, converted_lengths, location=self.location(node))

    def contract(self, node: Spec) -> ir.Contract:
        if node.kind is SpecKind.SEPARATED:
            self.fail("ir-contract", "SEPARATED semantics are deferred to the memory model", node)
        self.in_postcondition = node.kind is SpecKind.ENSURES
        try:
            condition = _convert(self.scalar(node.arguments[0]), ir.ScalarType.BOOL)
        finally:
            self.in_postcondition = False
        return ir.Contract(node.kind, condition, location=self.location(node))

    def local(self, node: c_ast.Decl) -> ir.Local:
        if not isinstance(node.type, c_ast.TypeDecl) or node.init is None:
            self.fail("ir-local", "locals must be initialized scalar declarations", node)
        self.name_available(node.name, node)
        # Bind only after checking the initializer: no self-initialization.
        value = self.scalar(node.init, allow_call=True)
        symbol = self.symbol(node)
        assert isinstance(symbol.type, ir.ScalarType)
        return ir.Local(symbol, _convert(value, symbol.type), location=self.location(node))

    def assignment(self, node: c_ast.Node) -> ir.Assign:
        if not isinstance(node, c_ast.Assignment):
            self.fail("ir-assignment", "expected a simple assignment", node)
        target = self.expression(node.lvalue)
        if not isinstance(target, (ir.Variable, ir.Index)) or not isinstance(target.type, ir.ScalarType):
            self.fail("ir-lvalue", "assignment requires a scalar variable or array element", node.lvalue)
        symbol = target.symbol if isinstance(target, ir.Variable) else target.array
        if symbol.const:
            self.fail("ir-const", "assignment modifies a const binding or element", node.lvalue)
        return ir.Assign(
            target,
            _convert(self.scalar(node.rvalue, allow_call=isinstance(target, ir.Variable)), target.type),
            location=self.location(node),
        )

    def body(self, node: c_ast.Node, *, loop: bool = False) -> tuple[ir.Block, ir.Contract | None]:
        outer = self.symbols.copy()
        nodes = list(node.block_items or []) if isinstance(node, c_ast.Compound) else [node]
        invariant = None
        if loop and nodes and isinstance(nodes[0], Spec) and nodes[0].kind is SpecKind.INVARIANT:
            invariant = self.contract(nodes[0])
            nodes.pop(0)
        statements = tuple(self.statement(child) for child in nodes)
        self.symbols = outer
        return ir.Block(statements, location=self.location(node)), invariant

    def statement(self, node: c_ast.Node) -> ir.Statement:
        location = self.location(node)
        if isinstance(node, c_ast.Decl):
            return self.local(node)
        if isinstance(node, c_ast.Assignment):
            return self.assignment(node)
        if isinstance(node, c_ast.EmptyStatement):
            return ir.Block((), location=location)
        if isinstance(node, c_ast.Compound):
            return self.body(node)[0]
        if isinstance(node, Spec):
            if node.kind is not SpecKind.ASSERT:
                self.fail("ir-contract", "contract is not supported at this statement position", node)
            return self.contract(node)
        if isinstance(node, c_ast.Return):
            if node.expr is None:
                self.fail("ir-return", "non-void functions require a return value", node)
            return ir.Return(_convert(self.scalar(node.expr, allow_call=True), self.return_type), location=location)
        if isinstance(node, c_ast.If):
            condition = self.scalar(node.cond)
            then_body = self.body(node.iftrue)[0]
            else_body = self.body(node.iffalse)[0] if node.iffalse is not None else None
            return ir.If(condition, then_body, else_body, location=location)
        if isinstance(node, c_ast.While):
            condition = self.scalar(node.cond)
            body, invariant = self.body(node.stmt, loop=True)
            return ir.While(condition, invariant, body, location=location)
        if isinstance(node, c_ast.For):
            outer = self.symbols.copy()
            initial: ir.Local | ir.Assign | None = None
            if isinstance(node.init, c_ast.DeclList):
                if len(node.init.decls) != 1:
                    self.fail("ir-for", "for initialization supports one declaration", node.init)
                initial = self.local(node.init.decls[0])
            elif node.init is not None:
                initial = self.assignment(node.init)
            for_condition = self.scalar(node.cond) if node.cond is not None else None
            update = self.assignment(node.next) if node.next is not None else None
            body, invariant = self.body(node.stmt, loop=True)
            self.symbols = outer
            return ir.For(initial, for_condition, update, invariant, body, location=location)
        self.fail("ir-statement", "statement is outside the structured IR subset", node)

    def function(self, node: c_ast.FuncDef) -> ir.Function:
        return_type, parameters = self.signature(node.decl)
        nodes = list(node.body.block_items or [])
        contracts = []
        while nodes and isinstance(nodes[0], Spec) and nodes[0].kind in {SpecKind.REQUIRES, SpecKind.ENSURES}:
            contracts.append(self.contract(nodes.pop(0)))
        body = ir.Block(tuple(self.statement(child) for child in nodes), location=self.location(node.body))
        if not _returns(body):
            self.fail(
                "ir-return", "all paths must structurally end in a value return; loop termination is not inferred", node
            )
        return ir.Function(
            node.decl.name, return_type, parameters, tuple(contracts), body, location=self.location(node)
        )


def _signature_key(return_type: ir.ScalarType, parameters: tuple[ir.Parameter, ...]) -> tuple[object, ...]:
    symbols = [parameter.symbol for parameter in parameters]
    return (
        return_type,
        tuple(
            (
                parameter.symbol.type,
                parameter.symbol.const if isinstance(parameter.symbol.type, ir.ArrayType) else False,
                symbols.index(parameter.length) if parameter.length is not None else None,
            )
            for parameter in parameters
        ),
    )


def lower(parsed: ParsedUnit) -> ir.Program:
    """Lower a parser-produced unit without modifying it; reject semantic gaps.

    Compatible prototypes are checked then coalesced. Calls preserve their typed
    signature; modular verification and memory predicates remain later work.
    """
    signatures = {}
    for node in parsed.ast.ext:
        if isinstance(node, c_ast.FuncDef):
            signatures[node.decl.name] = _Lowerer(parsed.report.source, node.decl.name).signature(node.decl)
    definitions: dict[str, ir.Function] = {}
    prototypes: list[tuple[c_ast.Decl, tuple[object, ...]]] = []
    for node in parsed.ast.ext:
        if isinstance(node, c_ast.Decl) and node.name in MARKERS:
            continue
        declaration = node.decl if isinstance(node, c_ast.FuncDef) else node
        if not isinstance(declaration, c_ast.Decl):
            raise FrontendError("ir-function", "expected a function declaration", _location(node, parsed.report.source))
        builder = _Lowerer(parsed.report.source, declaration.name, signatures)
        if isinstance(node, c_ast.FuncDef):
            if declaration.name in definitions:
                builder.fail("ir-function", "duplicate function definition", declaration)
            definitions[declaration.name] = builder.function(node)
        else:
            prototypes.append((declaration, _signature_key(*builder.signature(declaration))))
    for declaration, signature in prototypes:
        definition = definitions.get(declaration.name)
        if definition is None or signature != _signature_key(definition.return_type, definition.parameters):
            raise FrontendError(
                "ir-prototype", "prototype has no compatible definition", _location(declaration, parsed.report.source)
            )
    return ir.Program(tuple(definitions.values()))
