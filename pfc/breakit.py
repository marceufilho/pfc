"""Ablate the reviewed sum precondition and retain the complete proof/emission pair."""

from dataclasses import dataclass, replace

from pfc import ir, smt
from pfc.config import SOLVER_TIMEOUT_MS
from pfc.difftest import require_sum_profile
from pfc.digest import sha256_text
from pfc.emit import GUARD_LINE, Emission, emit
from pfc.target import Target

# Independent, deliberately sum-specific expectation, not derived from emitter decisions.
SUM_GUARD = "if (!(0 <= i && i < a_len)) __builtin_trap();"


@dataclass(frozen=True)
class CaseReport:
    program: ir.Program
    source: str
    source_id: str  # SHA-256 of reconstructed annotated C, not the original input bytes.
    proof: smt.CheckReport
    checked: Emission
    verified: Emission


@dataclass(frozen=True)
class BreakReport:
    removed: ir.Contract
    original: CaseReport
    altered: CaseReport
    passed: bool


def drop_requires(program: ir.Program) -> tuple[ir.Program, ir.Contract]:
    """Remove sum's one reviewed annotation, retaining every other node and coordinate."""
    require_sum_profile(program)
    function = program.functions[0]
    removed = function.contracts[0]
    return replace(program, functions=(replace(function, contracts=()),)), removed


def retains_sum_bounds(code: str) -> bool:
    """Check the expected guard text and ordering independently of proof metadata."""
    return (
        code.count(SUM_GUARD) == 1
        and "= a[i];" in code
        and code.index(SUM_GUARD) < code.index("= a[i];")
        and "// proved: obligation" not in code
    )


def _case(program: ir.Program, target: Target, timeout_ms: int) -> CaseReport:
    source = ir.print_c(program)
    proof = smt.check(program, target, timeout_ms=timeout_ms)
    return CaseReport(
        program,
        source,
        sha256_text(source),
        proof,
        emit(program, target, mode="checked"),
        emit(program, target, mode="verified", proof=proof),
    )


def _valid_pair(original: CaseReport, altered: CaseReport) -> bool:
    before = [r for r in original.proof.results if r.obligation.kind == "bounds"]
    after = [r for r in altered.proof.results if r.obligation.kind == "bounds"]
    if len(before) != 1 or len(after) != 1:
        return False
    proved, failed = before[0], after[0]
    decisions = original.verified.decisions
    restored = altered.verified.decisions
    return (
        original.proof.required_passed
        and proved.status == "proved"
        and proved.dependencies_satisfied
        and failed.status == "failed"
        and failed.counterexample is not None
        and not altered.proof.required_passed
        and original.proof.context_id != altered.proof.context_id
        and proved.obligation.id != failed.obligation.id
        and proved.obligation.location == failed.obligation.location
        and len(decisions) == len(restored) == 1
        and decisions[0].removed
        and decisions[0].obligation_ids == (proved.obligation.id,)
        and not restored[0].removed
        and restored[0].obligation_ids == (failed.obligation.id,)
        and original.verified.c.count(f"// proved: obligation {proved.obligation.id}") == 1
        and not GUARD_LINE.findall(original.verified.c)  # guard-shaped traps; overflow traps are separate (v5)
        and retains_sum_bounds(original.checked.c)
        and retains_sum_bounds(altered.checked.c)
        and retains_sum_bounds(altered.verified.c)
    )


def check_sum(program: ir.Program, target: Target, *, timeout_ms: int = SOLVER_TIMEOUT_MS) -> BreakReport:
    """Check the actual SAT loss of bounds proof and restoration; unknown is not success."""
    altered, removed = drop_requires(program)
    before = _case(program, target, timeout_ms)
    after = _case(altered, target, timeout_ms)
    return BreakReport(removed, before, after, _valid_pair(before, after))


@dataclass(frozen=True)
class Ablation:
    removed: ir.Contract
    altered: ir.Program
    proof: smt.CheckReport
    emission: Emission
    proof_lost: bool
    guard_restored: bool
    alias_fact_removed: bool
    passed: bool


@dataclass(frozen=True)
class KernelBreakReport:
    kernel: str
    criterion: str
    original_proof: smt.CheckReport
    original_emission: Emission
    cases: tuple[Ablation, ...]
    passed: bool


def check_kernel(program: ir.Program, target: Target, *, timeout_ms: int = SOLVER_TIMEOUT_MS) -> KernelBreakReport:
    """Drop each REQUIRES independently; distinguish guards from compiler alias facts.

    User-approved criterion: bounds preconditions must restore guards; a pure
    SEPARATED precondition may instead withdraw its dependent restrict fact.
    """
    from pfc.corpus import profile
    from pfc.facts import ALL_FACTS
    from pfc.parse import SpecKind

    name = profile(program)
    function = program.functions[0]
    original_proof = smt.check(program, target, timeout_ms=timeout_ms)
    original = emit(program, target, mode="verified", proof=original_proof, facts=ALL_FACTS)
    cases = []
    for index, contract in enumerate(function.contracts):
        if contract.kind is not SpecKind.REQUIRES:
            continue
        altered = replace(
            program,
            functions=(replace(function, contracts=function.contracts[:index] + function.contracts[index + 1 :]),),
        )
        proof = smt.check(altered, target, timeout_ms=timeout_ms)
        emission = emit(altered, target, mode="verified", proof=proof, facts=ALL_FACTS)
        lost_sites = {
            r.obligation.location
            for r in proof.results
            if r.obligation.kind in {"bounds", "division"} and r.status == "failed" and r.counterexample
        }
        previous = {(d.kind, d.location): d for d in original.decisions}
        restored = any(
            not d.removed
            and d.location in lost_sites
            and (d.kind, d.location) in previous
            and previous[(d.kind, d.location)].removed
            for d in emission.decisions
        )
        restored = restored and len(GUARD_LINE.findall(emission.c)) == sum(not d.removed for d in emission.decisions)
        old_alias = {f.location for f in original.emitted_facts if f.kind == "restrict"}
        new_alias = {f.location for f in emission.emitted_facts if f.kind == "restrict"}
        proof_lost = bool(lost_sites)
        dependent_alias = {
            f.location for f in original.emitted_facts if f.kind == "restrict" and contract.location in f.contracts
        }
        alias_removed = bool((old_alias - new_alias) & dependent_alias) and (
            original.c.count("[restrict]") == len(old_alias) and emission.c.count("[restrict]") == len(new_alias)
        )
        predicate = contract.condition
        while isinstance(predicate, ir.Convert):
            predicate = predicate.operand
        alias_consequence = isinstance(predicate, ir.Separated) and proof.required_passed and alias_removed
        passed = original_proof.required_passed and (proof_lost and restored or alias_consequence)
        cases.append(Ablation(contract, altered, proof, emission, proof_lost, restored, alias_removed, passed))
    return KernelBreakReport(
        name,
        "dependent-consequence",
        original_proof,
        original,
        tuple(cases),
        bool(cases) and all(case.passed for case in cases),
    )
