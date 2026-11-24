"""Structured plain/checked/verified C with explicit proof and fact decisions.

Emitter candidate v5 (ADR-0049 of the revision series): besides the bounds and division guards decided per site
(ADR-0023/0026), every `+ - *` and unary `-` site is an overflow decision, trapped with `__builtin_*_overflow`
unless its overflow obligation is proved with its dependency closure (checked traps always); and the verified
mode checks at function entry every top-level `REQUIRES` conjunct of the pure-scalar class whose C evaluation
provably equals its mathematical meaning, trapping when a caller violates it. Conjuncts outside that class are
recorded as skipped, never approximated. Overflow sites live in `Emission.overflow`, not in `decisions`, so the
admission of invariants (all guards removed) and every earlier consumer of `decisions` keep their meaning.
"""

import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Literal

from pfc import ir, smt, vc
from pfc.authorization import authorize, expressions, statements
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.facts import ALL_FACTS, DEFAULT_FACTS, Fact, FactName, restricted_parameters
from pfc.parse import SpecKind
from pfc.target import Target

Mode = Literal["plain", "checked", "verified"]
EMITTER = (
    "emitter-v5: pure-scalar REQUIRES entry checks in verified; overflow traps at + - * and unary - unless proved; "
    "division guard covers 0 and MIN/-1"
)
HEADER_POLICY = (
    "/* Overflow traps guard + - * and unary - unless their obligation is proved; division guards cover 0 and "
    "MIN/-1; verified checks pure-scalar REQUIRES at entry. Proof citations authorize only their individual guards. */"
)
# The three trap shapes, for independent counting by harness/audit code (never for authorization).
GUARD_LINE = re.compile(r"^\s*if \(!\((.*)\)\) __builtin_trap\(\);$", re.M)
OVERFLOW_LINE = re.compile(r"^\s*if \(__builtin_(add|sub|mul)_overflow\((.*)\)\) __builtin_trap\(\);$", re.M)
ENTRY_LINE = re.compile(r"^\s*if \(!pfc_entry_\d+\) __builtin_trap\(\);$", re.M)
_BUILTIN = {"+": "add", "-": "sub", "*": "mul"}
_GOAL_OPERATOR = {"+": "+", "-": "-", "*": "*", "neg": "neg"}
_CTYPE = {ir.ScalarType.LONG: "long", ir.ScalarType.INT: "int", ir.ScalarType.BOOL: "_Bool"}


@dataclass(frozen=True)
class Decision:
    site: int
    kind: Literal["bounds", "division", "overflow"]
    location: SourceLocation
    obligation_ids: tuple[str, ...]  # Every path obligation of one syntactic site; empty when ambiguous.
    removed: bool
    reason: str
    condition: str = ""  # The exact C predicate the guard tests, or the overflow builtin call (audit record).

    @property
    def site_id(self) -> str:
        return f"{self.kind}:{self.location.file}:{self.location.line}:{self.location.column}"


@dataclass(frozen=True)
class RequiresCheck:
    """One top-level REQUIRES conjunct at function entry: rendered to C and trapped, or skipped with a reason."""

    function: str
    contract: SourceLocation
    requires_index: int
    conjunct_index: int
    text: str
    rendered: str | None
    reason: str | None
    name: str | None


class _Unrenderable(Exception):
    pass


@dataclass(frozen=True)
class Emission:
    mode: Mode
    context_id: str
    target: Target
    c: str
    decisions: tuple[Decision, ...]
    arithmetic_dependencies: tuple[str, ...]
    proof: smt.CheckReport | None
    facts: tuple[FactName, ...] = ()
    emitted_facts: tuple[Fact, ...] = ()
    vc_model: str = vc.MODEL
    overflow: tuple[Decision, ...] = ()  # Arithmetic sites: kept apart from `decisions` so admission is unchanged.
    requires_checks: tuple[RequiresCheck, ...] = ()
    emitter: str = EMITTER


class _Emitter:
    def __init__(
        self,
        program: ir.Program,
        conditions: vc.VCReport,
        target: Target,
        mode: Mode,
        proof: smt.CheckReport | None,
        function: ir.Function,
        facts: frozenset[FactName],
    ) -> None:
        self.program = program
        self.function = function
        self.facts = facts
        self.emitted_facts: list[Fact] = []
        self.target = target
        self.mode = mode
        self.conditions = conditions
        self.authorization = authorize(program, self.conditions, target, proof) if mode == "verified" else None
        self.decisions: list[Decision] = []
        self.overflow: list[Decision] = []
        self.requires_checks: list[RequiresCheck] = []
        self.lines: list[str] = []
        self.depth = 1
        self.serial = 0
        self.entry_serial = 0
        symbols = [parameter.symbol for parameter in self.function.parameters]
        symbols.extend(
            statement.symbol for statement in statements(self.function.body) if isinstance(statement, ir.Local)
        )
        self.names = {symbol.name for symbol in symbols} | {function.name for function in program.functions}
        if any(name.startswith("__") for name in self.names):
            raise FrontendError("emit-name", "implementation-reserved __ names are unsupported", self.function.location)
        self.lengths = {
            parameter.symbol.id: parameter.length.name
            for parameter in self.function.parameters
            if parameter.length is not None
        }
        self.restricted = (
            restricted_parameters(self.function, conditions)
            if self.authorization and self.authorization.allowed and "restrict" in facts
            else {}
        )
        self.sites = _site_counts(self.function)

    def line(self, text: str) -> None:
        self.lines.append("    " * self.depth + text)

    def fresh(self, prefix: str = "pfc_tmp_") -> str:
        """A generated name that collides with no user binding (user names may themselves look generated)."""
        while True:
            if prefix == "pfc_tmp_":
                self.serial += 1
                name = f"{prefix}{self.serial}"
            else:
                self.entry_serial += 1
                name = f"{prefix}{self.entry_serial}"
            if name not in self.names:
                break
        self.names.add(name)
        return name

    def temporary(self, type_: ir.CType, value: str) -> str:
        name = self.fresh()
        self.line(f"{type_} {name} = {value};")
        return name

    def arithmetic(self, expression: ir.Expr, builtin: str, left: str, right: str, plain: str) -> str:
        """Overflow decision for one `+ - *`/unary `-` site: trap with the checked builtin unless proved (v5).

        The verdict never uses `authorization.allowed`: a unit may be allowed with an unproved accumulator, and
        that accumulator keeps its trap. Overflow obligations stay `reported`; they decide only their own trap.
        """
        type_ = expression.type
        if self.mode == "plain":
            return self.temporary(type_, plain)
        operator = expression.operator if isinstance(expression, ir.Binary) else "neg"
        # A parenthesized operand shares its coordinate with the enclosing operator (`(hi - lo) / 2`), so the
        # obligation is matched by location *and* by the operator of its goal term, and uniqueness is per operator.
        candidates = tuple(
            item.id
            for item in self.conditions.obligations
            if item.kind == "overflow"
            and item.location == expression.location
            and _overflow_operator(item.goal) == operator
        )
        unique = bool(candidates) and self.sites[(f"overflow:{operator}", expression.location)] == 1
        justified, reason = self._overflow_verdict(candidates if unique else ())
        removed = justified and "delete" in self.facts
        if justified and not removed:
            reason = "delete fact disabled"
        identifiers = candidates if unique else ()
        name = self.fresh()
        check = f"__builtin_{builtin}_overflow({left}, {right}, &{name})"
        self.overflow.append(
            Decision(len(self.overflow) + 1, "overflow", expression.location, identifiers, removed, reason, check)
        )
        if removed:
            for identifier in identifiers:
                self.line(f"// proved: obligation {identifier}")
            self.line(f"{type_} {name} = {plain};")
        else:
            self.line(f"{type_} {name};")
            self.line(f"if ({check}) __builtin_trap();")
        return name

    def _overflow_verdict(self, identifiers: tuple[str, ...]) -> tuple[bool, str]:
        if not identifiers:
            return False, "missing or ambiguous operation identity"
        authorization = self.authorization
        if authorization is None:
            return False, "checked mode retains every check"
        if not authorization.proved and not authorization.arithmetic_proved:
            return False, authorization.reason  # Evidence identity mismatch or missing report.
        if all(identifier in authorization.proved for identifier in identifiers):
            return True, "overflow obligation proved with dependencies"
        return False, "overflow obligation not proved"

    def guard(self, expression: ir.Expr, kind: Literal["bounds", "division"], condition: str) -> None:
        """Decide one syntactic site; every structured path through it must be proved.

        Path splitting yields one obligation per path at the same location. They all
        belong to this site only when no other operation of the same kind shares the
        location; otherwise the identity is ambiguous and the guard stays (ADR-0023).
        """
        if self.mode == "plain":
            return
        candidates = tuple(
            item.id
            for item in self.conditions.obligations
            if item.kind == kind and item.location == expression.location
        )
        unique = bool(candidates) and self.sites[(kind, expression.location)] == 1
        justified, reason = self._site_verdict(candidates if unique else ())
        removed = justified and "delete" in self.facts
        if justified and not removed:
            reason = "delete fact disabled"
        identifiers = candidates if unique else ()
        self.decisions.append(
            Decision(len(self.decisions) + 1, kind, expression.location, identifiers, removed, reason, condition)
        )
        if removed:
            for identifier in identifiers:
                self.line(f"// proved: obligation {identifier}")
        else:
            self.line(f"if (!({condition})) __builtin_trap();")
        if justified and "assume" in self.facts:
            self.line(f"// justified assume: obligation {', '.join(identifiers)}")
            self.line(f"__builtin_assume({condition});")
            self.emitted_facts.append(Fact("assume", expression.location, identifiers))

    def _site_verdict(self, identifiers: tuple[str, ...]) -> tuple[bool, str]:
        if not identifiers:
            return False, "missing or ambiguous operation identity"
        authorization = self.authorization
        if authorization is None:
            return False, "checked mode retains every check"
        if authorization.allowed:
            return True, authorization.reason
        if not authorization.proved and not authorization.arithmetic_proved:
            return False, authorization.reason  # Evidence identity mismatch or missing report.
        if not all(identifier in authorization.proved for identifier in identifiers):
            return False, "site proof or invariant dependency not proved"
        if not authorization.arithmetic_proved:
            return False, "arithmetic supporting control or safety not proved"
        return True, "site proofs, invariant admission and relevant arithmetic proved"

    def expression(self, expression: ir.Expr) -> str:
        if isinstance(expression, (ir.Literal, ir.Variable)):
            return ir._expression(expression)
        if isinstance(expression, ir.Convert):
            value = self.expression(expression.operand)
            return self.temporary(expression.type, value)
        if isinstance(expression, ir.Unary):
            value = self.expression(expression.operand)
            if expression.operator == "-" and not isinstance(expression.operand, ir.Literal):
                zero = "0L" if expression.type is ir.ScalarType.LONG else "0"
                return self.arithmetic(expression, "sub", zero, value, f"(-{value})")
            # A negated literal is a constant inside its type's range: no overflow site, no trap.
            return self.temporary(expression.type, f"({expression.operator}{value})")
        if isinstance(expression, ir.Index):
            return self.temporary(expression.type, self.array_lvalue(expression))
        if isinstance(expression, ir.Call):
            arguments = ", ".join(self.expression(argument) for argument in expression.arguments)
            return self.temporary(expression.type, f"{expression.function}({arguments})")
        if isinstance(expression, ir.Binary):
            left = self.expression(expression.left)
            if expression.operator in {"&&", "||"}:
                value = self.temporary(expression.type, f"({left} != 0)")
                self.line(f"if ({value if expression.operator == '&&' else '!' + value}) {{")
                self.depth += 1
                right = self.expression(expression.right)
                self.line(f"{value} = ({right} != 0);")
                self.depth -= 1
                self.line("}")
                return value
            right = self.expression(expression.right)
            if expression.operator == "/":
                bits = self.target.int_bits if expression.type is ir.ScalarType.INT else self.target.long_bits
                suffix = "" if expression.type is ir.ScalarType.INT else "L"
                minimum = f"(-{2 ** (bits - 1) - 1}{suffix} - 1{suffix})"
                self.guard(expression, "division", f"{right} != 0 && !({left} == {minimum} && {right} == -1)")
            plain = f"({left} {expression.operator} {right})"
            if expression.operator in _BUILTIN:
                return self.arithmetic(expression, _BUILTIN[expression.operator], left, right, plain)
            return self.temporary(expression.type, plain)
        raise FrontendError("emit-expression", "unsupported expression", expression.location)

    def array_lvalue(self, expression: ir.Index) -> str:
        index = self.expression(expression.index)
        length = self.lengths[expression.array.id]
        self.guard(expression, "bounds", f"0 <= {index} && {index} < {length}")
        return f"{expression.array.name}[{index}]"

    def block(self, block: ir.Block) -> None:
        for statement in block.statements:
            self.statement(statement)

    def statement(self, statement: ir.Statement) -> None:
        if isinstance(statement, ir.Block):
            self.line("{")
            self.depth += 1
            self.block(statement)
            self.depth -= 1
            self.line("}")
        elif isinstance(statement, ir.If):
            condition = self.expression(statement.condition)
            self.line(f"if ({condition}) {{")
            self.depth += 1
            self.block(statement.then_body)
            self.depth -= 1
            if statement.else_body:
                self.line("} else {")
                self.depth += 1
                self.block(statement.else_body)
                self.depth -= 1
            self.line("}")
        elif isinstance(statement, (ir.While, ir.For)):
            scoped = isinstance(statement, ir.For)
            if isinstance(statement, ir.For):
                self.line("{")
                self.depth += 1
                if statement.initializer:
                    self.statement(statement.initializer)
            self.line("while (1) {")
            self.depth += 1
            if statement.condition:
                condition = self.expression(statement.condition)
                self.line(f"if (!({condition})) break;")
            self.block(statement.body)
            if isinstance(statement, ir.For) and statement.update:
                self.statement(statement.update)
            self.depth -= 1
            self.line("}")
            if scoped:
                self.depth -= 1
                self.line("}")
        elif isinstance(statement, ir.Local):
            value = self.expression(statement.initializer)
            self.line(f"{ir._declaration(statement.symbol)} = {value};")
            self.line(f"(void){statement.symbol.name};")
        elif isinstance(statement, ir.Assign):
            value = self.expression(statement.value)
            target = (
                self.array_lvalue(statement.target)
                if isinstance(statement.target, ir.Index)
                else ir._expression(statement.target)
            )
            self.line(f"{target} = {value};")
        elif isinstance(statement, ir.Return):
            value = self.expression(statement.value)
            self.line(f"return {value};")
        elif isinstance(statement, ir.Contract):
            # Like spec.h: proof annotations are erased, not C runtime predicates.
            self.line("/* ASSERT is a verification obligation; runtime guards cover bounds/division. */")
        else:
            raise FrontendError("emit-subset", "unsupported statement", statement.location)

    def entry_checks(self) -> None:
        """Trap at entry on every pure-scalar REQUIRES conjunct, sequentially (C `&&` short-circuit order).

        Rendered conjuncts are those whose C value provably equals the annotation's mathematical value: scalar
        variables and literals, casts, `!`, comparisons, `&&`/`||`, unary minus of a literal, and `l / r` only
        when `r` is a positive literal or a variable an earlier *rendered* conjunct bounds by `>= 1` with no
        earlier conjunct skipped (so the C path condition equals the proof hypotheses). Anything else is
        skipped and recorded: `+ - *` (unbounded in C), memory reads, SEPARATED, calls, RESULT.
        """
        positive: set[str] = set()
        skipped_before = False
        requires_index = 0
        for contract in self.function.contracts:
            if contract.kind is not SpecKind.REQUIRES:
                continue
            for conjunct_index, part in enumerate(_conjuncts(contract.condition)):
                text = ir._expression(part)
                rendered: str | None
                reason: str | None = None
                try:
                    rendered = _render_requires(part, positive, skipped_before)
                except _Unrenderable as problem:
                    rendered, reason = None, str(problem)
                name = None
                if rendered is None:
                    skipped_before = True
                    self.line(
                        f"// entry check skipped: REQUIRES {contract.location} conjunct {conjunct_index + 1} ({reason})"
                    )
                else:
                    name = self.fresh("pfc_entry_")
                    where = f"REQUIRES {contract.location} conjunct {conjunct_index + 1}"
                    self.line(f"// entry check {self.entry_serial}: {where}")
                    self.line(f"_Bool {name} = {rendered};")
                    self.line(f"if (!{name}) __builtin_trap();")
                    bounded = _positive_divisor(part)
                    if bounded is not None:
                        positive.add(bounded)
                self.requires_checks.append(
                    RequiresCheck(
                        self.function.name,
                        contract.location,
                        requires_index,
                        conjunct_index,
                        text,
                        rendered,
                        reason,
                        name,
                    )
                )
            requires_index += 1

    def declaration(self, parameter: ir.Parameter) -> str:
        declaration = ir._declaration(parameter.symbol)
        if parameter.symbol.id in self.restricted:
            return declaration.replace("[]", "[restrict]")
        return declaration

    def run(self, *, preamble: bool = True) -> str:
        # Builtin macros avoid introducing header macros that can capture user bindings.
        self.lines = [
            f"/* pfc {self.mode}; requires the declared entry/memory contract and defined C arithmetic. */",
            HEADER_POLICY,
            "#ifndef __clang__",
            '#error "pfc output requires clang"',
            "#endif",
            f"_Static_assert(__CHAR_BIT__ == 8 && sizeof(int) * 8 == {self.target.int_bits}"
            f' && sizeof(long) * 8 == {self.target.long_bits}, "pfc target widths mismatch");',
            f"_Static_assert(__INT_MAX__ == {2 ** (self.target.int_bits - 1) - 1}"
            f' && __LONG_MAX__ == {2 ** (self.target.long_bits - 1) - 1}L, "pfc target ranges mismatch");',
            "",
        ]
        if not preamble:
            self.lines = []
        elif len(self.program.functions) > 1:
            for function in self.program.functions:
                parameters = ", ".join(ir._declaration(p.symbol) for p in function.parameters) or "void"
                self.lines.append(f"{function.return_type} {function.name}({parameters});")
            self.lines.append("")
        for fact in self.restricted.values():
            self.lines.append(f"/* justified restrict: context {self.conditions.context_id}; full entry regions */")
            self.emitted_facts.append(fact)
        parameters = ", ".join(self.declaration(parameter) for parameter in self.function.parameters) or "void"
        self.lines.extend([f"{self.function.return_type} {self.function.name}({parameters})", "{"])
        for parameter in self.function.parameters:
            self.line(f"(void){parameter.symbol.name};")
        if self.mode == "verified":
            self.entry_checks()
        modified = {
            statement.target.symbol.id
            for statement in statements(self.function.body)
            if isinstance(statement, ir.Assign) and isinstance(statement.target, ir.Variable)
        }
        for parameter in self.function.parameters:
            if parameter.length is not None and parameter.length.id in modified and self.mode != "plain":
                self.lengths[parameter.symbol.id] = self.temporary(ir.ScalarType.LONG, parameter.length.name)
        self.block(self.function.body)
        self.lines.extend(["}", ""])
        return "\n".join(self.lines)


def _site_counts(function: ir.Function) -> Counter[tuple[str, SourceLocation]]:
    """Count executable bounds/division sites per location, as the emitter will visit them."""
    counts: Counter[tuple[str, SourceLocation]] = Counter()
    roots: list[ir.Expr] = []
    for statement in statements(function.body):
        if isinstance(statement, (ir.If, ir.While, ir.For)):
            if statement.condition:
                roots.append(statement.condition)
        elif isinstance(statement, ir.Local):
            roots.append(statement.initializer)
        elif isinstance(statement, ir.Assign):
            roots.append(statement.value)
            if isinstance(statement.target, ir.Index):
                roots.append(statement.target)
        elif isinstance(statement, ir.Return):
            roots.append(statement.value)
    for node in (node for root in roots for node in expressions(root)):
        if isinstance(node, ir.Index):
            counts[("bounds", node.location)] += 1
        elif isinstance(node, ir.Binary) and node.operator == "/":
            counts[("division", node.location)] += 1
        elif isinstance(node, ir.Binary) and node.operator in _BUILTIN:
            counts[(f"overflow:{node.operator}", node.location)] += 1
        elif isinstance(node, ir.Unary) and node.operator == "-" and not isinstance(node.operand, ir.Literal):
            counts[("overflow:neg", node.location)] += 1
    return counts


def _overflow_operator(goal: vc.Term) -> str | None:
    """Operator of the value an overflow goal bounds: `MIN <= v && v <= MAX` yields the root operator of `v`."""
    if not isinstance(goal, vc.Operation) or goal.operator != "&&" or len(goal.arguments) != 2:
        return None
    lower_bound = goal.arguments[0]
    if not isinstance(lower_bound, vc.Operation) or lower_bound.operator != "<=" or len(lower_bound.arguments) != 2:
        return None
    value = lower_bound.arguments[1]
    return value.operator if isinstance(value, vc.Operation) else None


def _conjuncts(expression: ir.Expr) -> tuple[ir.Expr, ...]:
    if isinstance(expression, ir.Convert):
        return _conjuncts(expression.operand)
    if isinstance(expression, ir.Binary) and expression.operator == "&&":
        return _conjuncts(expression.left) + _conjuncts(expression.right)
    return (expression,)


def _strip(expression: ir.Expr) -> ir.Expr:
    while isinstance(expression, ir.Convert):
        expression = expression.operand
    return expression


def _simple(expression: ir.Expr) -> bool:
    return isinstance(_strip(expression), (ir.Literal, ir.Variable))


def _positive_divisor(part: ir.Expr) -> str | None:
    """The variable a rendered conjunct bounds below by 1: `k < r`, `k <= r`, `r > k`, `r >= k` with a literal k."""
    inner = _strip(part)
    if not isinstance(inner, ir.Binary) or inner.operator not in {"<", "<=", ">", ">="}:
        return None
    left, right = _strip(inner.left), _strip(inner.right)
    if inner.operator in {"<", "<="}:
        literal, variable, strict = left, right, inner.operator == "<"
    else:
        literal, variable, strict = right, left, inner.operator == ">"
    if not isinstance(literal, ir.Literal) or not isinstance(variable, ir.Variable):
        return None
    if isinstance(variable.symbol.type, ir.ArrayType):
        return None
    if (strict and literal.value >= 0) or (not strict and literal.value >= 1):
        return variable.symbol.name
    return None


def _render_requires(expression: ir.Expr, positive: set[str], skipped_before: bool) -> str:
    """C text of a pure-scalar annotation whose runtime value equals its mathematical value (see entry_checks)."""
    if isinstance(expression, ir.Literal):
        return expression.spelling
    if isinstance(expression, ir.Variable):
        if isinstance(expression.symbol.type, ir.ArrayType):
            raise _Unrenderable("array value has no scalar C evaluation")
        return expression.symbol.name
    if isinstance(expression, ir.Convert):
        inner = _render_requires(expression.operand, positive, skipped_before)
        ctype = _CTYPE.get(expression.type) if isinstance(expression.type, ir.ScalarType) else None
        if ctype is None:
            raise _Unrenderable("conversion to a non-scalar type")
        return f"({ctype}){inner}" if _simple(expression.operand) else f"({ctype})({inner})"
    if isinstance(expression, ir.Unary):
        if expression.operator == "!":
            return f"(!{_render_requires(expression.operand, positive, skipped_before)})"
        if expression.operator == "+":
            return _render_requires(expression.operand, positive, skipped_before)
        if expression.operator == "-" and isinstance(_strip(expression.operand), ir.Literal):
            return f"(-{_render_requires(expression.operand, positive, skipped_before)})"
        raise _Unrenderable("arithmetic result may leave the C long range; annotation arithmetic is mathematical")
    if isinstance(expression, ir.Binary):
        operator = expression.operator
        if operator in {"==", "!=", "<", "<=", ">", ">=", "&&", "||"}:
            left = _render_requires(expression.left, positive, skipped_before)
            right = _render_requires(expression.right, positive, skipped_before)
            return f"({left} {operator} {right})"
        if operator == "/":
            if skipped_before:
                raise _Unrenderable("divisor bound not on the C path: an earlier conjunct was skipped")
            if not (_simple(expression.left) and _simple(expression.right)):
                raise _Unrenderable("division operands must be variables or literals")
            divisor = _strip(expression.right)
            bounded = (isinstance(divisor, ir.Literal) and divisor.value >= 1) or (
                isinstance(divisor, ir.Variable) and divisor.symbol.name in positive
            )
            if not bounded:
                raise _Unrenderable("divisor not bounded below by 1 by an earlier rendered conjunct")
            left = _render_requires(expression.left, positive, skipped_before)
            right = _render_requires(expression.right, positive, skipped_before)
            return f"({left} / {right})"
        raise _Unrenderable("arithmetic result may leave the C long range; annotation arithmetic is mathematical")
    if isinstance(expression, ir.Index):
        raise _Unrenderable("reads memory; the entry check evaluates scalars only")
    if isinstance(expression, ir.Separated):
        raise _Unrenderable("separation predicate has no C evaluation")
    if isinstance(expression, ir.Call):
        raise _Unrenderable("call inside a contract")
    raise _Unrenderable(f"unsupported annotation node {type(expression).__name__}")


def emit(
    program: ir.Program,
    target: Target,
    *,
    mode: Mode,
    proof: smt.CheckReport | None = None,
    facts: frozenset[FactName] = DEFAULT_FACTS,
) -> Emission:
    """Emit all modes from one lowered IR; proof annotations are erased as in spec.h.

    Missing/stale/failed evidence retains safety guards. Restrict and assume require
    exact in-process proofs and explicit fact selection. No solver or filesystem I/O.
    """
    location = program.functions[0].location if program.functions else SourceLocation("<IR>")
    if mode not in {"plain", "checked", "verified"}:
        raise FrontendError("emit-mode", "expected plain, checked or verified", location)
    if not isinstance(facts, frozenset) or not facts <= ALL_FACTS:
        raise FrontendError("emit-facts", "expected a subset of delete, restrict, assume", location)
    if target.long_bits > 64:
        raise FrontendError("emit-target", "emission currently supports integer widths up to 64 bits", location)
    if not program.functions:
        raise FrontendError("emit-subset", "program has no functions", location)
    conditions = (
        vc.VCReport(vc._digest((vc.MODEL, program)), ", ".join(f.name for f in program.functions), ())
        if mode == "plain"
        else vc.generate(program)
    )
    emitters = [_Emitter(program, conditions, target, mode, proof, function, facts) for function in program.functions]
    code = "\n".join(emitter.run(preamble=index == 0) for index, emitter in enumerate(emitters))
    decisions = tuple(item for emitter in emitters for item in emitter.decisions)
    overflow = tuple(item for emitter in emitters for item in emitter.overflow)
    return Emission(
        mode,
        conditions.context_id,
        target,
        code,
        tuple(replace(item, site=index + 1) for index, item in enumerate(decisions)),
        emitters[0].authorization.arithmetic_dependencies if emitters[0].authorization else (),
        proof,
        tuple(sorted(facts)),
        tuple(item for emitter in emitters for item in emitter.emitted_facts),
        overflow=tuple(replace(item, site=index + 1) for index, item in enumerate(overflow)),
        requires_checks=tuple(item for emitter in emitters for item in emitter.requires_checks),
    )
