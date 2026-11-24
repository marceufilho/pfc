"""Semantics-preserving variants and verdict equality (roadmap E7, design E1.5, EV-R12).

Three transformations on the typed IR, each with explicit side conditions:

- `rename`: α-renaming of every parameter and local of a function to fresh names (`<array>` and
  `<array>_len` stay paired). Side conditions: fresh names collide with nothing in the program and are
  not reserved. Locations are untouched, so verdicts and decisions compare by (kind, location); the
  emitted C is compared after applying the inverse renaming.
- `reorder`: swapping two *adjacent* local declarations in the same block when the second initializer
  does not mention the first symbol and neither initializer contains a call. Evaluation order, scope and
  effects are preserved because the subset's call-free expressions have no side effects.
- `swap`: exchanging the operands of one `+`, `*`, `==` or `!=`. `&&` and `||` are excluded (short
  circuit changes definedness), as are every non-commutative operator; operands are side-effect free.

Finite comparisons are empirical validation, not a proof of equivalence. For every variant the required
verdicts, the per-site decisions and the restrict facts must be identical to the original's; a difference
is verdict brittleness (Chen and Furia, 2018). Observable behavior is compared with the reference
interpreter on fuzzed inputs; a divergence there is a transformation bug, reported separately. Renamed C
is not expected byte-identical; it is compared only under the inverse map, and no normalization is claimed
for the other transformations. `obfuscate` composes renaming with every commutative swap for the ported
public stratum of E10.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from pathlib import Path
from random import Random
from typing import Literal, cast

from pfc import ir, smt, workflow
from pfc.config import DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.diagnostics import SourceLocation
from pfc.emit import Emission, emit
from pfc.lower import lower
from pfc.parse import SpecKind, parse_source
from pfc.target import Target
from pfc_eval import campaign, conjuncts, evidence, fuzz, interpret

COMMUTATIVE = ("+", "*", "==", "!=")
RESERVED = {kind.value for kind in SpecKind} | {"RESULT", "PFC_SPEC_H", "SPEC_KEEP", "VERIFIED", "main"}
Kind = Literal["rename", "reorder", "swap", "obfuscate"]

SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("variant_id", "string"),
        evidence.Column("transformation", "string", allowed=("rename", "reorder", "swap", "obfuscate")),
        evidence.Column("detail", "string"),
        evidence.Column("verdicts_identical", "boolean"),
        evidence.Column("decisions_identical", "boolean"),
        evidence.Column("facts_identical", "boolean"),
        evidence.Column("c_compared", "boolean"),
        evidence.Column("c_identical", "boolean", nullable=True),
        evidence.Column("behavior_cases", "integer"),
        evidence.Column("behavior_rejected", "integer"),
        evidence.Column("divergent", "boolean"),
        evidence.Column("brittle", "boolean"),
    )
)


@dataclass(frozen=True)
class Variant:
    identifier: str
    kind: Kind
    detail: str
    program: ir.Program
    mapping: dict[str, str]  # original name -> variant name (empty unless renamed)
    swapped: tuple[SourceLocation, ...] = ()


@dataclass(frozen=True)
class Site:
    operator: str
    location: SourceLocation
    index: int


@dataclass(frozen=True)
class Reorder:
    first: ir.Local
    second: ir.Local
    index: int


@dataclass(frozen=True)
class Behavior:
    cases: int
    rejected: int
    divergent: int


@dataclass(frozen=True)
class VariantResult:
    variant: Variant
    verdicts_identical: bool
    decisions_identical: bool
    facts_identical: bool
    c_identical: bool | None
    behavior: Behavior
    brittle: bool
    verified_c: str


@dataclass(frozen=True)
class KernelRobustness:
    kernel: str
    original_c: str
    variants: tuple[VariantResult, ...]
    brittle: int
    divergent: int


# -- generic IR traversal --------------------------------------------------------------------------------------


def _transform(node: object, visit: Callable[[object], object | None]) -> object:
    """Pre-order rebuild: `visit` may return a replacement for a node before its fields are rebuilt."""
    replacement = visit(node)
    if replacement is not None:
        node = replacement
    if is_dataclass(node) and not isinstance(node, type):
        values = {f.name: _transform(getattr(node, f.name), visit) for f in fields(node)}
        return type(node)(**values)
    if isinstance(node, tuple):
        return tuple(_transform(item, visit) for item in node)
    return node


def _symbols(function: ir.Function) -> list[ir.Symbol]:
    found: list[ir.Symbol] = [p.symbol for p in function.parameters]

    def visit(node: object) -> None:
        if isinstance(node, ir.Local) and node.symbol not in found:
            found.append(node.symbol)
        return None

    _transform(function.body, visit)
    return found


def _names(program: ir.Program) -> set[str]:
    names = {f.name for f in program.functions}
    for function in program.functions:
        names.update(s.name for s in _symbols(function))
    return names


# -- rename ----------------------------------------------------------------------------------------------------


def rename(program: ir.Program, *, seed: int) -> Variant:
    """Fresh names for every parameter and local; `<array>_len` pairs stay paired; locations untouched."""
    rng = Random(seed)
    taken = _names(program) | RESERVED
    counter = 0
    mapping: dict[str, str] = {}
    new_symbols: dict[str, ir.Symbol] = {}

    def fresh() -> str:
        nonlocal counter
        while True:
            name = f"v{counter}"
            counter += 1
            if name not in taken and f"{name}_len" not in taken:
                taken.add(name)
                return name

    for function in program.functions:
        symbols = _symbols(function)
        order = list(range(len(symbols)))
        rng.shuffle(order)
        lengths = {p.length.id: p.symbol.id for p in function.parameters if p.length is not None}
        for index in order:
            symbol = symbols[index]
            if symbol.id in new_symbols or symbol.id in lengths:
                continue
            name = fresh()
            new_symbols[symbol.id] = replace(symbol, name=name)
            mapping[symbol.name] = name
        for length_id, array_id in lengths.items():
            length = next(p.length for p in function.parameters if p.length is not None and p.length.id == length_id)
            name = f"{new_symbols[array_id].name}_len"
            taken.add(name)
            new_symbols[length_id] = replace(length, name=name)
            mapping[length.name] = name

    def visit(node: object) -> object | None:
        if isinstance(node, ir.Symbol):
            return new_symbols.get(node.id)
        return None

    renamed = cast(ir.Program, _transform(program, visit))
    return Variant("", "rename", f"renamed {len(mapping)} names (seed {seed})", renamed, mapping)


def unrename(text: str, mapping: Mapping[str, str]) -> str:
    """Apply the inverse renaming to C text, longest new names first, on word boundaries."""
    for original, new in sorted(mapping.items(), key=lambda item: -len(item[1])):
        text = re.sub(rf"\b{re.escape(new)}\b", original, text)
    return text


def map_inputs(
    scalars: Mapping[str, int], arrays: Mapping[str, Sequence[int]], mapping: Mapping[str, str]
) -> interpret.Inputs:
    """Rename input keys; the same buffer object under two names stays one object (aliasing is preserved)."""
    return interpret.Inputs(
        {mapping.get(k, k): v for k, v in scalars.items()}, {mapping.get(k, k): v for k, v in arrays.items()}
    )


# -- reorder ---------------------------------------------------------------------------------------------------


def _mentions(expression: ir.Expr, symbol: ir.Symbol) -> bool:
    found = False

    def visit(node: object) -> None:
        nonlocal found
        if isinstance(node, ir.Symbol) and node.id == symbol.id:
            found = True
        return None

    _transform(expression, visit)
    return found


def _has_call(expression: ir.Expr) -> bool:
    found = False

    def visit(node: object) -> None:
        nonlocal found
        if isinstance(node, ir.Call):
            found = True
        return None

    _transform(expression, visit)
    return found


def _blocks(block: ir.Block) -> list[ir.Block]:
    found = [block]
    for statement in block.statements:
        if isinstance(statement, ir.Block):
            found.extend(_blocks(statement))
        elif isinstance(statement, ir.If):
            found.extend(_blocks(statement.then_body))
            if statement.else_body is not None:
                found.extend(_blocks(statement.else_body))
        elif isinstance(statement, (ir.While, ir.For)):
            found.extend(_blocks(statement.body))
    return found


def reorderable(function: ir.Function) -> tuple[Reorder, ...]:
    """Adjacent, independent, call-free local declarations in the same block, in preorder."""
    found: list[Reorder] = []
    for block in _blocks(function.body):
        statements = block.statements
        for index in range(len(statements) - 1):
            first, second = statements[index], statements[index + 1]
            if not (isinstance(first, ir.Local) and isinstance(second, ir.Local)):
                continue
            if (
                _mentions(second.initializer, first.symbol)
                or _has_call(first.initializer)
                or _has_call(second.initializer)
            ):
                continue
            found.append(Reorder(first, second, len(found)))
    return tuple(found)


def reorder(program: ir.Program, index: int, *, function: str | None = None) -> Variant:
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    pair = reorderable(entry)[index]

    def visit(node: object) -> object | None:
        if isinstance(node, ir.Block):
            statements = list(node.statements)
            for k in range(len(statements) - 1):
                if statements[k] is pair.first and statements[k + 1] is pair.second:
                    statements[k], statements[k + 1] = statements[k + 1], statements[k]
                    return replace(node, statements=tuple(statements))
        return None

    rebuilt = cast(ir.Function, _transform(entry, visit))
    program = replace(program, functions=tuple(rebuilt if f.name == name else f for f in program.functions))
    detail = (
        f"swapped declarations of {pair.first.symbol.name} (line {pair.first.location.line}) and "
        f"{pair.second.symbol.name} (line {pair.second.location.line})"
    )
    return Variant("", "reorder", detail, program, {})


# -- commutative swap ------------------------------------------------------------------------------------------


def commutative_sites(function: ir.Function) -> tuple[Site, ...]:
    """Every `+`, `*`, `==`, `!=` node of the function, contracts included, in preorder."""
    found: list[Site] = []

    def visit(node: object) -> None:
        if isinstance(node, ir.Binary) and node.operator in COMMUTATIVE:
            found.append(Site(node.operator, node.location, len(found)))
        return None

    _transform(function, visit)
    return tuple(found)


def _swap_sites(entry: ir.Function, indices: set[int]) -> ir.Function:
    counter = [0]

    def visit(node: object) -> object | None:
        if isinstance(node, ir.Binary) and node.operator in COMMUTATIVE:
            index = counter[0]
            counter[0] += 1
            if index in indices:
                return replace(node, left=node.right, right=node.left)
        return None

    return cast(ir.Function, _transform(entry, visit))


def swap(program: ir.Program, index: int, *, function: str | None = None) -> Variant:
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    site = commutative_sites(entry)[index]
    rebuilt = _swap_sites(entry, {index})
    program = replace(program, functions=tuple(rebuilt if f.name == name else f for f in program.functions))
    detail = f"swapped operands of {site.operator} at line {site.location.line} column {site.location.column}"
    return Variant("", "swap", detail, program, {}, (site.location,))


def obfuscate(program: ir.Program, *, seed: int, function: str | None = None) -> Variant:
    """Renaming plus every commutative swap, for the ported public stratum (design §5)."""
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    sites = commutative_sites(entry)
    rebuilt = _swap_sites(entry, set(range(len(sites))))
    swapped = replace(program, functions=tuple(rebuilt if f.name == name else f for f in program.functions))
    renamed = rename(swapped, seed=seed)
    detail = f"{renamed.detail}; swapped {len(sites)} commutative operand pairs"
    return Variant("", "obfuscate", detail, renamed.program, renamed.mapping, tuple(s.location for s in sites))


# -- variant schedule --------------------------------------------------------------------------------------------


def variants(program: ir.Program, *, seed: int, count: int = 10, function: str | None = None) -> tuple[Variant, ...]:
    """Seeded schedule cycling rename, reorder and swap; each reorder pair and swap site is used at most once."""
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    rng = Random(seed)
    reorders = list(range(len(reorderable(entry))))
    swaps = list(range(len(commutative_sites(entry))))
    rng.shuffle(reorders)
    rng.shuffle(swaps)
    found: list[Variant] = []
    kinds = ["rename", "reorder", "swap"]
    position = 0
    while len(found) < count:
        kind = kinds[position % 3]
        position += 1
        if kind == "rename":
            variant = rename(program, seed=rng.randrange(2**31))
        elif kind == "reorder":
            if not reorders:
                continue
            variant = reorder(program, reorders.pop(), function=name)
        else:
            if not swaps:
                continue
            variant = swap(program, swaps.pop(), function=name)
        found.append(replace(variant, identifier=f"v{len(found):02d}"))
    return tuple(found)


def c_comparable(variant: Variant) -> bool:
    """Only renaming has a specified normalization (the inverse map); other kinds are not compared on C."""
    return variant.kind == "rename"


# -- comparisons -----------------------------------------------------------------------------------------------------


def compare_behavior(
    original: ir.Program, variant: Variant, target: Target, *, seed: int, cases: int = 100, function: str | None = None
) -> Behavior:
    """Interpreter differential between original and variant on the same (mapped) fuzz inputs."""
    name = function if function is not None else original.functions[0].name
    entry = next(f for f in original.functions if f.name == name)
    rng = Random(seed)
    divergent = rejected = 0
    inverse = {new: old for old, new in variant.mapping.items()}
    for _ in range(cases):
        inputs = fuzz.draw_inputs(entry, rng)
        before = interpret.run(original, inputs, target=target, function=name)
        after = interpret.run(
            variant.program, map_inputs(inputs.scalars, inputs.arrays, variant.mapping), target=target, function=name
        )
        if isinstance(before, interpret.Rejection) or isinstance(after, interpret.Rejection):
            rejected += 1
            same = (
                isinstance(before, interpret.Rejection)
                and isinstance(after, interpret.Rejection)
                and before.kind == after.kind
            )
        else:
            arrays = {inverse.get(k, k): v for k, v in after.arrays.items()}
            same = before.result == after.result and before.arrays == arrays
        if not same:
            divergent += 1
    return Behavior(cases, rejected, divergent)


def run_kernel(
    program: ir.Program,
    target: Target,
    *,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    seed: int = 0,
    count: int = 10,
    cases: int = 100,
) -> KernelRobustness:
    name = program.functions[0].name
    original_proof = smt.check(program, target, timeout_ms=timeout_ms)
    original = emit(program, target, mode="verified", proof=original_proof, facts=conjuncts.COMPARISON_FACTS)
    base_verdicts = conjuncts.verdicts(original_proof)
    base_decisions = conjuncts.decisions(original)
    base_facts = conjuncts.restrict_sites(original)
    base_c = conjuncts.normalize_c(original.c)
    results = []
    for variant in variants(program, seed=seed, count=count, function=name):
        proof = smt.check(variant.program, target, timeout_ms=timeout_ms)
        emission: Emission = emit(
            variant.program, target, mode="verified", proof=proof, facts=conjuncts.COMPARISON_FACTS
        )
        verdicts_identical = conjuncts.verdicts(proof) == base_verdicts
        decisions_identical = conjuncts.decisions(emission) == base_decisions
        facts_identical = conjuncts.restrict_sites(emission) == base_facts
        c_identical = (
            conjuncts.normalize_c(unrename(emission.c, variant.mapping)) == base_c if c_comparable(variant) else None
        )
        behavior = compare_behavior(program, variant, target, seed=seed, cases=cases, function=name)
        brittle = not (verdicts_identical and decisions_identical and facts_identical and c_identical is not False)
        results.append(
            VariantResult(
                variant,
                verdicts_identical,
                decisions_identical,
                facts_identical,
                c_identical,
                behavior,
                brittle,
                emission.c,
            )
        )
    return KernelRobustness(
        name,
        original.c,
        tuple(results),
        sum(r.brittle for r in results),
        sum(r.behavior.divergent > 0 for r in results),
    )


# -- evidence ----------------------------------------------------------------------------------------------------


def rows(result: KernelRobustness, kernel: str) -> list[dict[str, object]]:
    return [
        {
            "kernel": kernel,
            "variant_id": r.variant.identifier,
            "transformation": r.variant.kind,
            "detail": r.variant.detail,
            "verdicts_identical": r.verdicts_identical,
            "decisions_identical": r.decisions_identical,
            "facts_identical": r.facts_identical,
            "c_compared": r.c_identical is not None,
            "c_identical": r.c_identical,
            "behavior_cases": r.behavior.cases,
            "behavior_rejected": r.behavior.rejected,
            "divergent": r.behavior.divergent > 0,
            "brittle": r.brittle,
        }
        for r in result.variants
    ]


def summarize(results: Mapping[str, KernelRobustness]) -> dict[str, object]:
    kernels: dict[str, object] = {}
    for name, result in results.items():
        kinds = {kind: sum(r.variant.kind == kind for r in result.variants) for kind in ("rename", "reorder", "swap")}
        kernels[name] = {
            "variants": len(result.variants),
            "by_kind": kinds,
            "brittle": result.brittle,
            "divergent": result.divergent,
            "renamed_c_identical": sum(r.c_identical is True for r in result.variants),
            "renamed_c_compared": sum(r.c_identical is not None for r in result.variants),
        }
    return {
        "experiment": "robustness",
        "kernels": kernels,
        "passed": all(r.brittle == 0 and r.divergent == 0 for r in results.values()),
    }


def render_table(summary: Mapping[str, object]) -> str:
    lines = [
        "| kernel | variants (rename/reorder/swap) | brittle | divergent | renamed C identical |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    for name, e in kernels.items():
        kinds = e["by_kind"]
        lines.append(
            f"| {name} | {e['variants']} ({kinds['rename']}/{kinds['reorder']}/{kinds['swap']}) | {e['brittle']} | "
            f"{e['divergent']} | {e['renamed_c_identical']}/{e['renamed_c_compared']} |"
        )
    lines.append("")
    lines.append(
        "Brittle: required verdicts, per-site decisions or restrict facts differ from the original's under the "
        "location mapping (renamed C is compared only after the inverse renaming). Divergent: the interpreter "
        "observed different behavior on the same fuzzed inputs, which would be a transformation bug. Finite "
        "comparisons are empirical validation, not a proof of equivalence."
    )
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "variant"]


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = 0,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    count: int = 10,
    cases: int = 100,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    sources = workflow.discover(tuple(directories))
    programs = [(source.parent.name, lower(parse_source(source, cpp=cpp))) for source in sources]
    if dry_run:
        for name, program in programs:
            entry = program.functions[0]
            print(
                f"{name}: {count} variants; {len(reorderable(entry))} reorderable pair(s), "
                f"{len(commutative_sites(entry))} commutative site(s)"
            )
        print(f"Preview: seed {seed}, {cases} behavior cases per variant, solver timeout {timeout_ms} ms.")
        print("No files were written.")
        return True
    run = campaign.start(
        "robustness",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "timeout_ms": timeout_ms,
            "variants_per_kernel": count,
            "behavior_cases": cases,
            "behavior_domain": fuzz.domain(),
            "commutative_operators": list(COMMUTATIVE),
            "facts": sorted(conjuncts.COMPARISON_FACTS),
            "kernels": [name for name, _ in programs],
        },
        schema=SCHEMA,
    )
    results: dict[str, KernelRobustness] = {}
    for name, program in programs:
        if progress:
            progress(f"robustness: {name}")
        result = run_kernel(program, run.target, timeout_ms=timeout_ms, seed=seed, count=count, cases=cases)
        results[name] = result
        run.evidence.write_text(f"sources/{name}-original-verified.c", result.original_c)
        for row, item in zip(rows(result, name), result.variants, strict=True):
            run.evidence.record("variant", name, item.variant.identifier, row)
            run.evidence.write_text(f"sources/{name}-{item.variant.identifier}.c", ir.print_c(item.variant.program))
            run.evidence.write_text(f"sources/{name}-{item.variant.identifier}-verified.c", item.verified_c)
            if item.variant.mapping:
                run.evidence.record("mapping", name, item.variant.identifier, dict(item.variant.mapping))
    summary = summarize(results)
    campaign.finish(run, summary, render_table(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
