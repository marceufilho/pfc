"""Per-conjunct REQUIRES ablation with verdict/C change definitions (roadmap E4, EV-R12, design E1.2).

Each top-level `&&` conjunct of each `REQUIRES` is dropped alone; the variant is re-verified and
re-emitted with all facts. "Changed" and "unchanged" are defined on per-site decisions, required
verdicts keyed by (kind, location) and the emitted C with proof citations stripped, never on obligation
IDs, which contain the contract text. The expected consequence of each drop is established
independently of the compiler by a small-domain witness search with the reference interpreter:

- `safety`: an input accepted by the weakened contract and rejected by the original reaches an
  out-of-bounds access, a zero divisor or an overflow at an executable site; the guard there must return;
- `callee`: such an input violates an internal callee precondition; every guard must return (ADR-0026);
- `annotation`/`undefined`: the input only falsifies a proof annotation or makes contract arithmetic
  undefined; a verdict change is expected, guard changes are reported but not scored;
- none: no discriminating input found in the search; verdicts and executable C must be identical;
- a pure `SEPARATED` conjunct: the pre-registered consequence is restrict-fact withdrawal (ADR-0017).

Absence of a witness after a finite search is not a proof of independence; the search size and the
number of discriminating inputs are recorded with each row.

Each row also lists the guard sites restored by the drop, keyed by (kind, location), so that conjunct
counts, distinct restored sites and conjunct–site pairs can be told apart. `reconcile_directories`
recomputes these sites for a sealed campaign whose rows predate the column: it re-emits every variant,
binds it to the sealed `sources/*.c` by normalized-C identity, and reports a site only for bound variants.
"""

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from random import Random
from typing import Literal

from pfc import ir, smt, workflow
from pfc.config import DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.diagnostics import SourceLocation
from pfc.emit import EMITTER, Emission, emit
from pfc.facts import FactName
from pfc.lower import lower
from pfc.parse import SpecKind, parse_source
from pfc.target import Target
from pfc_eval import campaign, evidence, expressions, fuzz, interpret

ROOT = Path(__file__).resolve().parents[1]
WITNESS_DRAWS = 3000
# Compared emissions carry deletions and restrict facts. The assume fact restates the proved guard condition
# together with the obligation identifiers, which digest the contract text, so it changes whenever a conjunct is
# dropped and would make every ablation "changed" by construction.
COMPARISON_FACTS: frozenset[FactName] = frozenset({"delete", "restrict"})
WitnessKind = Literal["safety", "callee", "annotation", "undefined"]
Expected = Literal["guard", "restrict", "annotation", "none"]
SiteKey = tuple[str, str]
_CITATION = re.compile(r"^[ \t]*// proved: obligation [^\n]*\n", re.MULTILINE)
_DIGEST = re.compile(r"\b[0-9a-f]{64}\b")
# Entry checks (emitter v5) restate the REQUIRES conjuncts, so they change whenever a conjunct is dropped; they are
# compared through `requires_checks`, not through the C text.
_ENTRY = re.compile(
    r"^[ \t]*(// entry check [^\n]*|_Bool pfc_entry_\d+ = [^\n]*|if \(!pfc_entry_\d+\) __builtin_trap\(\);)\n", re.M
)

SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("function", "string"),
        evidence.Column("requires_index", "integer"),
        evidence.Column("conjunct_index", "integer"),
        evidence.Column("conjunct_text", "string"),
        evidence.Column("separated", "boolean"),
        evidence.Column(
            "witness_kind", "string", nullable=True, allowed=("safety", "callee", "annotation", "undefined")
        ),
        evidence.Column("witness_line", "integer", nullable=True),
        evidence.Column("search_draws", "integer"),
        evidence.Column("discriminating_draws", "integer"),
        evidence.Column("expected", "string", allowed=("guard", "restrict", "annotation", "none")),
        evidence.Column("proof_lost", "boolean"),
        evidence.Column("verdicts_identical", "boolean"),
        evidence.Column("guard_restored", "boolean"),
        evidence.Column("witness_site_restored", "boolean", nullable=True),
        evidence.Column("guards_newly_removed", "integer"),
        evidence.Column("restrict_withdrawn", "boolean"),
        evidence.Column("c_identical", "boolean"),
        evidence.Column("as_expected", "boolean"),
        evidence.Column("restored_sites", "string"),
        evidence.Column("restored_site_count", "integer"),
    )
)
RECONCILE_SCHEMA = evidence.Schema(
    (
        evidence.Column("kernel", "string"),
        evidence.Column("function", "string"),
        evidence.Column("requires_index", "integer"),
        evidence.Column("conjunct_index", "integer"),
        evidence.Column("conjunct_text", "string"),
        evidence.Column("expected", "string", allowed=("guard", "restrict", "annotation", "none")),
        evidence.Column("sealed_guard_restored", "boolean"),
        evidence.Column("original_c_identical", "boolean"),
        evidence.Column("sealed_c_identical", "boolean"),
        evidence.Column("restored_sites", "string", nullable=True),
        evidence.Column("restored_site_count", "integer", nullable=True),
        evidence.Column("consistent", "boolean", nullable=True),
    )
)


@dataclass(frozen=True)
class Conjunct:
    requires_index: int
    index: int
    text: str
    location: SourceLocation
    separated: bool
    expression: ir.Expr


@dataclass(frozen=True)
class Witness:
    kind: WitnessKind
    location: SourceLocation | None
    inputs: interpret.Inputs
    message: str


@dataclass(frozen=True)
class Search:
    draws: int
    discriminating: int
    witness: Witness | None


@dataclass(frozen=True)
class Ablation:
    conjunct: Conjunct
    search: Search
    expected: Expected
    proof_lost: bool
    verdicts_identical: bool
    guard_restored: bool
    witness_site_restored: bool | None
    guards_newly_removed: int
    restrict_withdrawn: bool
    c_identical: bool
    as_expected: bool
    variant_c: str
    variant_required_passed: bool
    restored: tuple[SiteKey, ...] = ()


@dataclass(frozen=True)
class Binding:
    """A re-emitted variant bound (or not) to its sealed C; sites are reported only when bound."""

    identical: bool
    restored: tuple[SiteKey, ...] | None


@dataclass(frozen=True)
class KernelConjuncts:
    kernel: str
    function: str
    original_required_passed: bool
    original_c: str
    ablations: tuple[Ablation, ...]
    sensitivity: tuple[int, int]
    specificity: tuple[int, int]
    separated: tuple[int, int]
    annotation_dependent: int
    anomalies: int


# -- splitting and dropping ---------------------------------------------------------------------------------


def conjuncts(function: ir.Function) -> tuple[Conjunct, ...]:
    """Top-level conjuncts of every REQUIRES, in source order; SEPARATED predicates are flagged."""
    found = []
    requires_index = 0
    for contract in function.contracts:
        if contract.kind is not SpecKind.REQUIRES:
            continue
        for index, part in enumerate(expressions.flatten(contract.condition)):
            found.append(
                Conjunct(
                    requires_index,
                    index,
                    expressions.render(part),
                    contract.location,
                    isinstance(part, ir.Separated),
                    part,
                )
            )
        requires_index += 1
    return tuple(found)


def _as_bool(expression: ir.Expr) -> ir.Expr:
    """Mirror the lowering: a contract condition is converted to _Bool unless it already is one."""
    if expression.type is ir.ScalarType.BOOL:
        return expression
    return ir.Convert(ir.ScalarType.BOOL, expression, location=expression.location)


def _conjoin(parts: Sequence[ir.Expr], location: SourceLocation) -> ir.Expr:
    joined = parts[0]
    for part in parts[1:]:
        joined = ir.Binary(ir.ScalarType.INT, "&&", joined, part, location=location)
    return joined


def drop(program: ir.Program, conjunct: Conjunct, *, function: str | None = None) -> ir.Program:
    """Return a new program whose REQUIRES lacks exactly this conjunct; the original is untouched."""
    name = function if function is not None else program.functions[0].name
    target = next(f for f in program.functions if f.name == name)
    requires_seen = 0
    contracts: list[ir.Contract] = []
    for contract in target.contracts:
        if contract.kind is not SpecKind.REQUIRES:
            contracts.append(contract)
            continue
        if requires_seen != conjunct.requires_index:
            contracts.append(contract)
            requires_seen += 1
            continue
        requires_seen += 1
        parts = expressions.flatten(contract.condition)
        remaining = parts[: conjunct.index] + parts[conjunct.index + 1 :]
        if remaining:
            condition = _as_bool(_conjoin(remaining, contract.location))
            contracts.append(replace(contract, condition=condition))
    altered = replace(target, contracts=tuple(contracts))
    return replace(program, functions=tuple(altered if f.name == name else f for f in program.functions))


# -- change definitions --------------------------------------------------------------------------------------


def normalize_c(text: str) -> str:
    """Executable C for comparison: citations and comment digests embed the contract text, not behavior."""
    return _DIGEST.sub("<digest>", _ENTRY.sub("", _CITATION.sub("", text)))


def _site(location: SourceLocation) -> str:
    return f"{location.file}:{location.line}:{location.column}"


def decisions(emission: Emission) -> dict[SiteKey, bool]:
    return {(d.kind, _site(d.location)): d.removed for d in emission.decisions}


def compare_decisions(
    original: Mapping[SiteKey, bool], variant: Mapping[SiteKey, bool]
) -> tuple[tuple[SiteKey, ...], tuple[SiteKey, ...]]:
    """Sites whose guard came back (removed -> retained) and sites newly removed (retained -> removed)."""
    restored = tuple(k for k in sorted(original) if original[k] and k in variant and not variant[k])
    newly_removed = tuple(k for k in sorted(original) if not original[k] and k in variant and variant[k])
    return restored, newly_removed


def sites_text(sites: Iterable[SiteKey]) -> str:
    """Stable textual form of restored sites: `kind@file:line:column`, `;`-joined, in the given order."""
    return ";".join(f"{kind}@{site}" for kind, site in sites)


def bind_sites(
    sealed_c: str, fresh_c: str, original: Mapping[SiteKey, bool], variant: Mapping[SiteKey, bool]
) -> Binding:
    """Report restored sites only when the fresh emission is the sealed one up to citations and digests."""
    if normalize_c(sealed_c) != normalize_c(fresh_c):
        return Binding(False, None)
    restored, _newly_removed = compare_decisions(original, variant)
    return Binding(True, restored)


def verdicts(proof: smt.CheckReport) -> dict[tuple[str, str], str]:
    """Required verdicts keyed by (kind, location); 'proved' only with dependencies satisfied."""
    table: dict[tuple[str, str], str] = {}
    for result in proof.results:
        if result.obligation.category != "required":
            continue
        key = (result.obligation.kind, _site(result.obligation.location))
        status: str = result.status
        if result.status == "proved" and not result.dependencies_satisfied:
            status = "proved-without-dependencies"
        table[key] = status if key not in table or table[key] == status else f"{table[key]}|{status}"
    return table


def restrict_sites(emission: Emission) -> set[str]:
    return {_site(f.location) for f in emission.emitted_facts if f.kind == "restrict"}


def expected(*, separated: bool, witness_kind: WitnessKind | None) -> Expected:
    if separated:
        if witness_kind is not None:
            raise ValueError("a SEPARATED conjunct with a safety or annotation witness needs review")
        return "restrict"
    if witness_kind in ("safety", "callee"):
        return "guard"
    if witness_kind in ("annotation", "undefined"):
        return "annotation"
    return "none"


# -- witness search ------------------------------------------------------------------------------------------


def _entry_violation(outcome: interpret.Observation | interpret.Rejection, function: ir.Function) -> bool:
    return (
        isinstance(outcome, interpret.Rejection)
        and outcome.kind == "PreconditionViolated"
        and outcome.location in {c.location for c in function.contracts}
    )


def _classify(outcome: interpret.Observation | interpret.Rejection, inputs: interpret.Inputs) -> Witness | None:
    if isinstance(outcome, interpret.Rejection):
        if outcome.kind in ("OutOfBounds", "DivisionByZero", "Overflow"):
            return Witness("safety", outcome.location, inputs, f"{outcome.kind}: {outcome.message}")
        if outcome.kind == "PreconditionViolated":
            return Witness("callee", outcome.location, inputs, outcome.message)
        if outcome.kind == "AnnotationUndefined":
            return Witness("undefined", outcome.location, inputs, outcome.message)
        return None
    if outcome.annotation_violations:
        first = outcome.annotation_violations[0]
        line, column = (int(x) for x in first.rsplit(" at ", 1)[1].split(":"))
        return Witness("annotation", SourceLocation("<annotation>", line, column), inputs, first)
    return None


def find_witness(
    original: ir.Program,
    variant: ir.Program,
    target: Target,
    *,
    seed: int,
    draws: int = WITNESS_DRAWS,
    function: str | None = None,
) -> Search:
    """Small-domain search for an input the weakened contract admits, the original rejects, and that misbehaves."""
    name = function if function is not None else original.functions[0].name
    entry = next(f for f in original.functions if f.name == name)
    weakened_entry = next(f for f in variant.functions if f.name == name)
    rng = Random(seed)
    discriminating = 0
    for _ in range(draws):
        # Draw from the weakened contract: the sampler assigns REQUIRES equalities (ADR-0047), so drawing from
        # the original would satisfy a dropped equality on every draw and nothing could discriminate.
        inputs = fuzz.draw_inputs(weakened_entry, rng)
        weakened = interpret.run(variant, inputs, target=target, function=name)
        if _entry_violation(weakened, entry):
            continue
        strict = interpret.run(original, inputs, target=target, function=name)
        if not _entry_violation(strict, entry):
            continue
        discriminating += 1
        witness = _classify(weakened, inputs)
        if witness is not None:
            if witness.kind == "annotation" and witness.location is not None:
                witness = replace(
                    witness,
                    location=SourceLocation(entry.location.file, witness.location.line, witness.location.column),
                )
            return Search(draws, discriminating, witness)
    return Search(draws, discriminating, None)


# -- per-kernel run ------------------------------------------------------------------------------------------


def run_kernel(
    program: ir.Program,
    *,
    target: Target,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    seed: int = 0,
    function: str | None = None,
    draws: int = WITNESS_DRAWS,
) -> KernelConjuncts:
    name = function if function is not None else program.functions[0].name
    entry = next(f for f in program.functions if f.name == name)
    original_proof = smt.check(program, target, timeout_ms=timeout_ms)
    original = emit(program, target, mode="verified", proof=original_proof, facts=COMPARISON_FACTS)
    original_decisions = decisions(original)
    original_verdicts = verdicts(original_proof)
    original_restrict = restrict_sites(original)
    original_c = normalize_c(original.c)
    ablations = []
    for conjunct in conjuncts(entry):
        variant = drop(program, conjunct, function=name)
        search = find_witness(program, variant, target, seed=seed, draws=draws, function=name)
        kind = search.witness.kind if search.witness else None
        try:
            expectation = expected(separated=conjunct.separated, witness_kind=kind)
        except ValueError:
            expectation = "annotation"
        proof = smt.check(variant, target, timeout_ms=timeout_ms)
        emission = emit(variant, target, mode="verified", proof=proof, facts=COMPARISON_FACTS)
        variant_decisions = decisions(emission)
        restored, newly_removed = compare_decisions(original_decisions, variant_decisions)
        variant_verdicts = verdicts(proof)
        lost = any(
            original_verdicts[k] == "proved" and variant_verdicts.get(k) != "proved" for k in original_verdicts
        ) or (original_proof.required_passed and not proof.required_passed)
        identical = original_verdicts == variant_verdicts
        site_restored: bool | None = None
        if search.witness is not None and search.witness.kind == "safety" and search.witness.location is not None:
            site_restored = any(site == _site(search.witness.location) for _kind, site in restored)
        withdrawn = bool(original_restrict - restrict_sites(emission))
        c_identical = original_c == normalize_c(emission.c)
        if expectation == "guard":
            ok = lost and bool(restored) and (site_restored is not False)
        elif expectation == "restrict":
            ok = withdrawn and not restored and not lost
        elif expectation == "none":
            ok = identical and c_identical and not restored
        else:
            ok = lost
        ok = ok and not newly_removed
        ablations.append(
            Ablation(
                conjunct,
                search,
                expectation,
                lost,
                identical,
                bool(restored),
                site_restored,
                len(newly_removed),
                withdrawn,
                c_identical,
                ok,
                emission.c,
                proof.required_passed,
                restored,
            )
        )
    guard = [a for a in ablations if a.expected == "guard"]
    none = [a for a in ablations if a.expected == "none"]
    separated = [a for a in ablations if a.expected == "restrict"]
    return KernelConjuncts(
        program.functions[0].name if function is None else name,
        name,
        original_proof.required_passed,
        original.c,
        tuple(ablations),
        (sum(a.as_expected for a in guard), len(guard)),
        (sum(a.as_expected for a in none), len(none)),
        (sum(a.restrict_withdrawn for a in separated), len(separated)),
        sum(a.expected == "annotation" for a in ablations),
        sum(a.guards_newly_removed > 0 for a in ablations),
    )


# -- evidence ------------------------------------------------------------------------------------------------


def rows(result: KernelConjuncts, kernel: str) -> list[dict[str, object]]:
    table: list[dict[str, object]] = []
    for a in result.ablations:
        witness = a.search.witness
        table.append(
            {
                "kernel": kernel,
                "function": result.function,
                "requires_index": a.conjunct.requires_index,
                "conjunct_index": a.conjunct.index,
                "conjunct_text": a.conjunct.text,
                "separated": a.conjunct.separated,
                "witness_kind": witness.kind if witness else None,
                "witness_line": witness.location.line if witness and witness.location else None,
                "search_draws": a.search.draws,
                "discriminating_draws": a.search.discriminating,
                "expected": a.expected,
                "proof_lost": a.proof_lost,
                "verdicts_identical": a.verdicts_identical,
                "guard_restored": a.guard_restored,
                "witness_site_restored": a.witness_site_restored,
                "guards_newly_removed": a.guards_newly_removed,
                "restrict_withdrawn": a.restrict_withdrawn,
                "c_identical": a.c_identical,
                "as_expected": a.as_expected,
                "restored_sites": sites_text(a.restored),
                "restored_site_count": len(a.restored),
            }
        )
    return table


def summarize(results: Mapping[str, KernelConjuncts]) -> dict[str, object]:
    kernels: dict[str, object] = {}
    for name, result in results.items():
        kernels[name] = {
            "function": result.function,
            "conjuncts": len(result.ablations),
            "original_required_passed": result.original_required_passed,
            "sensitivity": {"as_expected": result.sensitivity[0], "total": result.sensitivity[1]},
            "specificity": {"as_expected": result.specificity[0], "total": result.specificity[1]},
            "separated": {"withdrawn": result.separated[0], "total": result.separated[1]},
            "annotation_dependent": result.annotation_dependent,
            "anomalies": result.anomalies,
            "all_as_expected": all(a.as_expected for a in result.ablations),
        }
    return {
        "experiment": "conjuncts",
        "kernels": kernels,
        "passed": all(all(a.as_expected for a in r.ablations) and r.anomalies == 0 for r in results.values()),
    }


def _ratio(pair: Mapping[str, int]) -> str:
    return "n/a" if pair["total"] == 0 else f"{pair['as_expected']}/{pair['total']}"


def render(summary: Mapping[str, object]) -> str:
    lines = [
        "| kernel | conjuncts | sensitivity (guard restored / safety-witnessed) "
        "| specificity (identical / unwitnessed) | SEPARATED restrict withdrawn | annotation-dependent "
        "| anomalies | all as expected |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    for name, e in kernels.items():
        separated = e["separated"]
        lines.append(
            f"| {name} | {e['conjuncts']} | {_ratio(e['sensitivity'])} | {_ratio(e['specificity'])} | "
            f"{separated['withdrawn']}/{separated['total']} | {e['annotation_dependent']} | {e['anomalies']} | "
            f"{'yes' if e['all_as_expected'] else 'no'} |"
        )
    lines.append("")
    lines.append(
        "Expected consequences come from a seeded small-domain witness search with the reference interpreter; "
        "absence of a witness is finite evidence, not proof of independence. Annotation-dependent conjuncts are "
        "those whose only witness falsifies a proof annotation or makes contract arithmetic undefined."
    )
    return "\n".join(lines) + "\n"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "conjunct"]


# -- reconciliation of sealed campaigns ----------------------------------------------------------------------


def _split_sites(text: object) -> list[str]:
    return [token for token in str(text).split(";") if token] if isinstance(text, str) else []


def site_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Conjuncts, conjunct–site pairs and distinct sites, over guard-expected rows and over all rows."""
    bound = [r for r in rows if r.get("sealed_c_identical") is True]
    guard = [r for r in bound if r.get("expected") == "guard"]
    guard_tokens = [token for r in guard for token in _split_sites(r.get("restored_sites"))]
    all_tokens = [token for r in bound for token in _split_sites(r.get("restored_sites"))]
    unbound = sum(r.get("sealed_c_identical") is not True for r in rows)
    return {
        "guard_conjuncts": sum(r.get("expected") == "guard" for r in rows),
        "guard_pairs": len(guard_tokens),
        "guard_distinct_sites": len(set(guard_tokens)),
        "all_pairs": len(all_tokens),
        "all_distinct_sites": len(set(all_tokens)),
        "unbound": unbound,
        "all_bound": unbound == 0,
    }


def _sealed_source(sealed: Path, name: str) -> str:
    return (sealed / "sources" / name).read_text(encoding="utf-8")


def reconcile_kernel(
    program: ir.Program,
    sealed: Path,
    kernel: str,
    sealed_rows: Sequence[Mapping[str, object]],
    *,
    target: Target,
    timeout_ms: int,
) -> list[dict[str, object]]:
    """Re-emit the original and every sealed variant of one kernel; bind each to its sealed C and list sites."""
    entry = program.functions[0]
    original_proof = smt.check(program, target, timeout_ms=timeout_ms)
    original = emit(program, target, mode="verified", proof=original_proof, facts=COMPARISON_FACTS)
    original_identical = normalize_c(_sealed_source(sealed, f"{kernel}-original-verified.c")) == normalize_c(original.c)
    original_decisions = decisions(original)
    by_index = {(c.requires_index, c.index): c for c in conjuncts(entry)}
    table: list[dict[str, object]] = []
    for row in sealed_rows:
        key = (int(str(row["requires_index"])), int(str(row["conjunct_index"])))
        conjunct = by_index.get(key)
        if conjunct is None or conjunct.text != row["conjunct_text"]:
            raise ValueError(f"{kernel}: sealed conjunct {key} {row['conjunct_text']!r} is absent from the program")
        variant = drop(program, conjunct, function=entry.name)
        proof = smt.check(variant, target, timeout_ms=timeout_ms)
        emission = emit(variant, target, mode="verified", proof=proof, facts=COMPARISON_FACTS)
        sealed_c = _sealed_source(sealed, f"{kernel}-{key[0]}-{key[1]}-verified.c")
        binding = bind_sites(sealed_c, emission.c, original_decisions, decisions(emission))
        bound = binding.identical and original_identical
        restored = binding.restored if bound else None
        sealed_restored = bool(row["guard_restored"])
        table.append(
            {
                "kernel": kernel,
                "function": entry.name,
                "requires_index": key[0],
                "conjunct_index": key[1],
                "conjunct_text": conjunct.text,
                "expected": row["expected"],
                "sealed_guard_restored": sealed_restored,
                "original_c_identical": original_identical,
                "sealed_c_identical": bound,
                "restored_sites": sites_text(restored) if restored is not None else None,
                "restored_site_count": len(restored) if restored is not None else None,
                "consistent": (bool(restored) == sealed_restored) if restored is not None else None,
            }
        )
    return table


def render_sites(summary: Mapping[str, object]) -> str:
    kernels = summary["kernels"]
    assert isinstance(kernels, dict)
    lines = [
        "| kernel | conjuncts | guard conjuncts | bound variants | conjunct–site pairs (guard) "
        "| distinct sites (guard) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, e in kernels.items():
        lines.append(
            f"| {name} | {e['conjuncts']} | {e['guard_conjuncts']} | {e['bound']}/{e['conjuncts']} | "
            f"{e['guard_pairs']} | {e['guard_distinct_sites']} |"
        )
    lines.append("")
    lines.append(
        f"Totals over guard-expected conjuncts: {summary['guard_conjuncts']} conjuncts, {summary['guard_pairs']} "
        f"conjunct–site pairs, {summary['guard_distinct_sites']} distinct guard sites; over every conjunct: "
        f"{summary['all_pairs']} pairs, {summary['all_distinct_sites']} distinct sites. Sites are reported only for "
        "variants whose re-emitted C equals the sealed C up to citations and digests; "
        f"{summary['unbound']} variant(s) could not be bound."
    )
    return "\n".join(lines) + "\n"


def derive_site_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "site"]


def reconcile_directories(
    sealed: Path,
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """Recompute restored guard sites for a sealed `conjuncts` campaign into a new `conjunct_sites` directory."""
    loaded = evidence.load(sealed)
    if loaded.configuration.get("experiment") != "conjuncts":
        raise ValueError(f"{sealed} is not a sealed conjuncts campaign")
    settings = loaded.configuration.get("settings")
    if not isinstance(settings, dict):
        raise ValueError(f"{sealed} has no settings record")
    sealed_kernels = [str(k) for k in settings.get("kernels", [])]
    sources = workflow.discover(tuple(directories))
    programs = {source.parent.name: lower(parse_source(source, cpp=cpp)) for source in sources}
    missing = sorted(set(sealed_kernels) - set(programs))
    if missing:
        raise ValueError(f"sealed kernels absent from the given directories: {', '.join(missing)}")
    rows_by_kernel = {k: [r for r in loaded.observations if r["kernel"] == k] for k in sealed_kernels}
    if dry_run:
        for name in sealed_kernels:
            print(f"{name}: {len(rows_by_kernel[name])} sealed conjunct(s) to re-emit and bind")
        print(f"Preview: source campaign {sealed.name}, solver timeout {timeout_ms} ms. No files were written.")
        return True
    manifest_digest = hashlib.sha256((sealed / "manifest.json").read_bytes()).hexdigest()
    provenance = loaded.configuration.get("provenance")
    run = campaign.start(
        "conjunct_sites",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=None,
        settings={
            "timeout_ms": timeout_ms,
            "facts": sorted(COMPARISON_FACTS),
            "kernels": sealed_kernels,
            "source_campaign": sealed.name,
            "source_manifest_sha256": manifest_digest,
            "source_commit": provenance.get("commit") if isinstance(provenance, dict) else None,
        },
        schema=RECONCILE_SCHEMA,
    )
    all_rows: list[dict[str, object]] = []
    kernels: dict[str, object] = {}
    for name in sealed_kernels:
        if progress:
            progress(f"conjunct sites: {name}")
        table = reconcile_kernel(
            programs[name], sealed, name, rows_by_kernel[name], target=run.target, timeout_ms=timeout_ms
        )
        for row in table:
            run.evidence.record("site", name, f"{row['requires_index']}-{row['conjunct_index']}", row)
        all_rows.extend(table)
        local = site_summary(table)
        kernels[name] = {
            "conjuncts": len(table),
            "bound": sum(r["sealed_c_identical"] is True for r in table),
            **{k: local[k] for k in ("guard_conjuncts", "guard_pairs", "guard_distinct_sites")},
        }
    summary: dict[str, object] = {
        "experiment": "conjunct_sites",
        "source_campaign": sealed.name,
        "source_manifest_sha256": manifest_digest,
        "emitter": EMITTER,
        "kernels": kernels,
        **site_summary(all_rows),
    }
    summary["passed"] = bool(summary["all_bound"]) and all(r["consistent"] is True for r in all_rows)
    campaign.finish(run, summary, render_sites(summary), derive_site_rows)
    return bool(summary["passed"])


def run_directories(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    seed: int = 0,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    reconcile: Path | None = None,
) -> bool:
    if reconcile is not None:
        return reconcile_directories(
            reconcile,
            directories,
            output,
            compiler=compiler,
            cpp=cpp,
            timeout_ms=timeout_ms,
            dry_run=dry_run,
            progress=progress,
        )
    sources = workflow.discover(tuple(directories))
    programs = [(source.parent.name, lower(parse_source(source, cpp=cpp))) for source in sources]
    if dry_run:
        for name, program in programs:
            found = conjuncts(program.functions[0])
            groups = len({c.requires_index for c in found})
            print(f"{name}: {len(found)} conjunct(s) in {groups} REQUIRES; {sum(c.separated for c in found)} SEPARATED")
        print(f"Preview: seed {seed}, solver timeout {timeout_ms} ms, {WITNESS_DRAWS} witness draws per conjunct.")
        print("No files were written.")
        return True
    run = campaign.start(
        "conjuncts",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "timeout_ms": timeout_ms,
            "facts": sorted(COMPARISON_FACTS),
            "witness_draws": WITNESS_DRAWS,
            "witness_domain": fuzz.domain(),
            "kernels": [name for name, _ in programs],
        },
        schema=SCHEMA,
    )
    results: dict[str, KernelConjuncts] = {}
    for name, program in programs:
        if progress:
            progress(f"conjuncts: {name}")
        result = run_kernel(program, target=run.target, timeout_ms=timeout_ms, seed=seed)
        results[name] = result
        for row in rows(result, name):
            run.evidence.record("conjunct", name, f"{row['requires_index']}-{row['conjunct_index']}", row)
        run.evidence.write_text(f"sources/{name}-original-verified.c", result.original_c)
        for a in result.ablations:
            tag = f"{a.conjunct.requires_index}-{a.conjunct.index}"
            run.evidence.write_text(f"sources/{name}-{tag}-verified.c", a.variant_c)
            witness = a.search.witness
            run.evidence.record(
                "witness",
                name,
                tag,
                {
                    "conjunct_text": a.conjunct.text,
                    "kind": witness.kind if witness else None,
                    "message": witness.message if witness else None,
                    "inputs": (
                        {
                            "scalars": dict(witness.inputs.scalars),
                            "arrays": {k: list(v) for k, v in witness.inputs.arrays.items()},
                        }
                        if witness
                        else None
                    ),
                    "variant_required_passed": a.variant_required_passed,
                },
            )
    summary = summarize(results)
    campaign.finish(run, summary, render(summary), derive_rows)
    return bool(summary["passed"])


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run_directories)
