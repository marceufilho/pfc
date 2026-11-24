"""Syntax frontend: private pycparser tree -> explicit contracts and source sites.

Syntax policy lives in subset.py; typed acceptance reuses lower.py. A parse report
alone is neither typed validation nor a verification result.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, NoReturn, cast

from pycparser import c_ast, c_parser

from pfc.config import DEFAULT_CPP, MAX_AST_DEPTH, MAX_AST_NODES, MAX_PREPROCESSED_BYTES
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.preprocess import preprocess
from pfc.subset import _location, _scalar_type, check_node, validate_calls


class SpecKind(StrEnum):
    REQUIRES = "REQUIRES"
    ENSURES = "ENSURES"
    INVARIANT = "INVARIANT"
    ASSERT = "ASSERT"
    SEPARATED = "SEPARATED"


MARKERS = {f"__spec_{kind.value.lower()}": kind for kind in SpecKind}
ENTRY_SPECS = {SpecKind.REQUIRES, SpecKind.ENSURES}


@dataclass(frozen=True)
class Spec(c_ast.Node):
    """An annotation node, retaining its original expression AST and coordinate."""

    kind: SpecKind
    arguments: tuple[c_ast.Node, ...]
    coord: c_parser.Coord | None
    attr_names: ClassVar[tuple[str, ...]] = ("kind",)

    def children(self) -> tuple[tuple[str, c_ast.Node], ...]:
        return tuple((f"arguments[{index}]", node) for index, node in enumerate(self.arguments))

    def __iter__(self) -> Iterator[c_ast.Node]:
        return iter(self.arguments)


@dataclass(frozen=True)
class ContractInfo:
    kind: SpecKind
    function: str
    location: SourceLocation


@dataclass(frozen=True)
class Site:
    kind: str
    context: str
    function: str
    location: SourceLocation


@dataclass(frozen=True)
class ParseReport:
    source: str
    functions: tuple[str, ...]
    contracts: tuple[ContractInfo, ...]
    sites: tuple[Site, ...]
    verification: str = "not-run"


@dataclass(frozen=True)
class ParsedUnit:
    ast: c_ast.FileAST
    report: ParseReport


def _children(node: c_ast.Node) -> tuple[tuple[str, c_ast.Node], ...]:
    # pycparser's generated children() has a stable API but lacks an annotation.
    children = cast(Callable[[], tuple[tuple[str, c_ast.Node], ...]], node.children)
    return children()


def _valid_marker_declaration(node: c_ast.Decl) -> bool:
    if node.init is not None or not isinstance(node.type, c_ast.FuncDecl):
        return False
    declaration = node.type
    parameters = declaration.args.params if declaration.args else []
    if not all(isinstance(parameter, (c_ast.Decl, c_ast.Typename)) for parameter in parameters):
        return False
    if node.name != "__spec_separated":
        return (
            _scalar_type(declaration.type) == "void"
            and len(parameters) == 1
            and _scalar_type(parameters[0].type) == "_Bool"
        )
    if _scalar_type(declaration.type) != "_Bool" or len(parameters) != 4:
        return False
    for index, parameter in enumerate(parameters):
        if index % 2:
            if _scalar_type(parameter.type) != "long":
                return False
        elif not isinstance(parameter.type, c_ast.PtrDecl) or _scalar_type(parameter.type.type) != "void":
            return False
    return True


class _Normalizer:
    """Own a newly parsed tree until conversion and validation finish."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.function = ""
        self.function_body: c_ast.Compound | None = None
        self.loop_heads: set[int] = set()
        self.contracts: list[ContractInfo] = []
        self.sites: list[Site] = []
        self.node_count = 0

    def _fail(self, rule: str, message: str, node: c_ast.Node) -> NoReturn:
        raise FrontendError(rule, message, _location(node, self.filename))

    def _check_position(self, node: c_ast.FuncCall, kind: SpecKind, parent: c_ast.Node | None, edge: str) -> None:
        statement = (
            isinstance(parent, c_ast.Compound)
            and edge.startswith("block_items[")
            or isinstance(parent, c_ast.If)
            and edge in {"iftrue", "iffalse"}
            or isinstance(parent, (c_ast.For, c_ast.While))
            and edge == "stmt"
        )
        if not statement:
            self._fail("contract-position", "annotation must be a statement (SEPARATED may occur in contracts)", node)
        if kind in ENTRY_SPECS:
            if self.function_body is None or parent is not self.function_body:
                self._fail("contract-position", "entry contracts belong at the start of a function body", node)
            for previous in self.function_body.block_items or []:
                if previous is node:
                    break
                if not isinstance(previous, Spec) or previous.kind not in ENTRY_SPECS:
                    self._fail(
                        "contract-position", "entry contracts must precede executable statements and locals", node
                    )
        if kind is SpecKind.INVARIANT and id(node) not in self.loop_heads:
            self._fail("contract-position", "INVARIANT must be first in a braced loop body", node)

    def _spec(self, node: c_ast.FuncCall, parent: c_ast.Node | None, edge: str, contract: bool, depth: int) -> Spec:
        if not isinstance(node.name, c_ast.ID) or node.name.name not in MARKERS:
            rule = (
                "contract-marker"
                if isinstance(node.name, c_ast.ID) and node.name.name.startswith("__spec_")
                else "subset-call"
            )
            self._fail(rule, "unknown annotation marker", node)
        kind = MARKERS[node.name.name]
        if contract:
            if kind is not SpecKind.SEPARATED:
                self._fail("contract-position", "only SEPARATED may be nested inside a contract", node)
        else:
            self._check_position(node, kind, parent, edge)
        arguments = tuple(node.args.exprs) if node.args else ()
        if len(arguments) != (4 if kind is SpecKind.SEPARATED else 1):
            self._fail("contract-arity", "incorrect number of annotation arguments", node)
        self.contracts.append(ContractInfo(kind, self.function, _location(node, self.filename)))
        converted = tuple(self._visit(argument, node, "argument", True, depth + 1) for argument in arguments)
        return Spec(kind, converted, node.coord)

    def _visit(
        self, node: c_ast.Node, parent: c_ast.Node | None = None, edge: str = "", contract: bool = False, depth: int = 0
    ) -> c_ast.Node:
        self.node_count += 1
        if depth > MAX_AST_DEPTH or self.node_count > MAX_AST_NODES:
            self._fail("parse-complexity", "AST exceeds the frontend complexity limit", node)
        if isinstance(node, c_ast.FuncCall):
            if isinstance(node.name, c_ast.ID) and node.name.name.startswith("__spec_"):
                return self._spec(node, parent, edge, contract, depth)
            if contract:
                self._fail("contract-effect", "ordinary calls are forbidden inside contracts", node)
            if not isinstance(node.name, c_ast.ID):
                self._fail("subset-indirect-call", "only direct calls to defined functions are supported", node)
            if node.args:
                node.args.exprs = [self._visit(arg, node, "argument", False, depth + 1) for arg in node.args.exprs]
            return node
        check_node(node, self.filename, contract)
        if isinstance(node, (c_ast.While, c_ast.For)) and isinstance(node.stmt, c_ast.Compound):
            if node.stmt.block_items:
                self.loop_heads.add(id(node.stmt.block_items[0]))
        context = "contract" if contract else "program"
        if isinstance(node, c_ast.ArrayRef):
            self.sites.append(Site("index", context, self.function, _location(node, self.filename)))
        if isinstance(node, c_ast.BinaryOp) and node.op == "/":
            self.sites.append(Site("division", context, self.function, _location(node, self.filename)))
        for name, child in _children(node):
            converted = self._visit(child, node, name, contract, depth + 1)
            field, separator, index = name.partition("[")
            if separator:
                cast(list[c_ast.Node], getattr(node, field))[int(index[:-1])] = converted
            else:
                setattr(node, field, converted)
        return node

    def normalize(self, tree: c_ast.FileAST) -> ParsedUnit:
        functions: list[str] = []
        for node in tree.ext:
            if isinstance(node, c_ast.Decl) and (node.name or "").startswith("__spec_"):
                if node.name not in MARKERS or not _valid_marker_declaration(node):
                    self._fail("contract-marker", "marker declaration does not match the transport header", node)
                continue
            if isinstance(node, c_ast.FuncDef):
                if node.decl.name.startswith("__spec_"):
                    self._fail("contract-marker", "contract marker definitions are forbidden", node)
                self.function = node.decl.name
                self.function_body = node.body
                functions.append(self.function)
            elif isinstance(node, c_ast.Decl) and isinstance(node.type, c_ast.FuncDecl):
                self.function = ""
                self.function_body = None
            else:
                self._fail(
                    "subset-global", "only function declarations and definitions are supported at file scope", node
                )
            self._visit(node)
        if not functions:
            raise FrontendError(
                "empty-program", "source contains no function definition", SourceLocation(self.filename)
            )
        validate_calls(tree, self.filename)
        report = ParseReport(self.filename, tuple(functions), tuple(self.contracts), tuple(self.sites))
        return ParsedUnit(tree, report)


def parse_preprocessed(text: str, filename: str = "<input>") -> ParsedUnit:
    """Parse already preprocessed text without I/O; never mutate a caller's AST."""
    location = SourceLocation(filename)
    if not text.strip():
        raise FrontendError("source-empty", "source is empty", location)
    if len(text.encode("utf-8")) > MAX_PREPROCESSED_BYTES:
        raise FrontendError("preprocess-size", "preprocessed source exceeds the limit", location)
    try:
        tree = c_parser.CParser().parse(text, filename=filename)
        return _Normalizer(filename).normalize(tree)
    except c_parser.ParseError as error:
        match = re.match(r"(.*?):(\d+)(?::(\d+))?:", str(error))
        if match:
            file, line, column = match.groups()
            location = SourceLocation(file, int(line), int(column) if column else None)
        raise FrontendError("parse-syntax", "invalid C syntax", location) from None
    except RecursionError:
        raise FrontendError("parse-complexity", "source nesting exceeds the parser limit", location) from None


def parse_source(source: Path, *, cpp: str = DEFAULT_CPP) -> ParsedUnit:
    """Orchestrate preprocessing and syntax normalization for one translation unit."""
    return parse_preprocessed(preprocess(source, cpp=cpp), str(source.resolve()))


def render_report(report: ParseReport) -> str:
    """Render counts and locations without printing source expressions."""
    counts = {kind: sum(item.kind == kind for item in report.contracts) for kind in SpecKind}
    indexes = sum(site.kind == "index" for site in report.sites)
    divisions = sum(site.kind == "division" for site in report.sites)
    rows = [
        f"Parsed {report.source} (syntax only; no verification)",
        ", ".join(f"{count} {kind}" for kind, count in counts.items()),
        f"{indexes} index site{'s' if indexes != 1 else ''}, {divisions} division{'s' if divisions != 1 else ''}",
    ]
    rows.extend(f"{item.kind} {item.function} {item.location}" for item in report.contracts)
    rows.extend(f"{site.kind} {site.context} {site.function} {site.location}" for site in report.sites)
    return "\n".join(rows) + "\n"
