"""Precondition ablation must change proof and emitted code, not merely flags."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from pfc import breakit
from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.diagnostics import FrontendError
from pfc.emit import GUARD_LINE
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def program():
    return lower(parse_source(ROOT / "kernels/sum/kernel.c"))


@pytest.fixture(scope="module")
def target():
    return read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))


def test_drop_requires_preserves_body_locations_and_original(program):
    altered, removed = breakit.drop_requires(program)
    assert len(program.functions[0].contracts) == 1
    assert not altered.functions[0].contracts
    assert removed == program.functions[0].contracts[0]
    assert altered.functions[0].body is program.functions[0].body
    assert altered.functions[0].parameters == program.functions[0].parameters


def test_unreviewed_program_rejected_before_solver(program, target, monkeypatch):
    changed = replace(program, functions=(replace(program.functions[0], name="other"),))
    monkeypatch.setattr("pfc.breakit.smt.check", lambda *a, **k: pytest.fail("must reject before checking"))
    with pytest.raises(FrontendError, match="reviewed sum"):
        breakit.check_sum(changed, target)


@pytest.mark.integration
def test_real_ablation_loses_bounds_proof_restores_check_and_records_model(program, target):
    report = breakit.check_sum(program, target)
    assert report.passed
    before = next(r for r in report.original.proof.results if r.obligation.kind == "bounds")
    after = next(r for r in report.altered.proof.results if r.obligation.kind == "bounds")
    assert before.status == "proved" and before.dependencies_satisfied
    assert after.status == "failed" and after.counterexample and not after.counterexample.replayed
    values = {v.name: v.value for v in after.counterexample.scalars}
    assert 0 <= values["i"] <= values["n"] and values["i"] < values["n"]
    assert not (0 <= values["i"] < values["a_len"])
    assert before.obligation.location == after.obligation.location
    assert before.obligation.id != after.obligation.id
    assert report.original.proof.context_id != report.altered.proof.context_id
    assert report.original.source_id != report.altered.source_id
    assert f"// proved: obligation {before.obligation.id}" in report.original.verified.c
    assert not GUARD_LINE.findall(report.original.verified.c)  # v5: the accumulator overflow trap is not a guard
    assert breakit.retains_sum_bounds(report.altered.verified.c)
    assert breakit.retains_sum_bounds(report.original.checked.c)
    assert not report.altered.verified.decisions[0].removed


@pytest.mark.parametrize("status", ["timeout", "unknown", "error", "proved"])
def test_non_sat_altered_bounds_cannot_pass_gate(program, target, monkeypatch, status):
    original = breakit.smt.check

    def check(p, *a, **kw):
        result = original(p, *a, **kw)
        if not p.functions[0].contracts:
            result = replace(
                result,
                results=tuple(
                    replace(r, status=status, counterexample=None) if r.obligation.kind == "bounds" else r
                    for r in result.results
                ),
            )
        return result

    monkeypatch.setattr(breakit.smt, "check", check)
    assert not breakit.check_sum(program, target).passed


@pytest.mark.parametrize("damage", ["guard", "citation", "metadata"])
def test_gate_rejects_emission_defects(program, target, monkeypatch, damage):
    original = breakit.emit

    def emit(p, *a, **kw):
        output = original(p, *a, **kw)
        if kw["mode"] == "verified":
            if damage == "guard" and not p.functions[0].contracts:
                return replace(output, c=output.c.replace(breakit.SUM_GUARD, ""))
            if damage == "citation" and p.functions[0].contracts:
                return replace(output, c=output.c.replace("// proved: obligation", "// missing: obligation"))
            if damage == "metadata" and p.functions[0].contracts:
                return replace(output, decisions=())
        return output

    monkeypatch.setattr(breakit, "emit", emit)
    assert not breakit.check_sum(program, target).passed


def test_retention_oracle_detects_deletion_off_by_one_and_late_guard(program, target):
    code = breakit.emit(program, target, mode="verified").c
    assert breakit.retains_sum_bounds(code)
    assert not breakit.retains_sum_bounds(code.replace(breakit.SUM_GUARD, ""))
    assert not breakit.retains_sum_bounds(code.replace("i < a_len", "i <= a_len"))
    assert not breakit.retains_sum_bounds(code.replace(breakit.SUM_GUARD, "") + breakit.SUM_GUARD)
    assert not breakit.retains_sum_bounds(code + "\n// proved: obligation fake")
