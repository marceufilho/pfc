"""Validity, dependency admission and entry consistency are separate report dimensions."""

from dataclasses import replace

import pytest

from pfc import smt
from pfc.lower import lower
from pfc.parse import parse_preprocessed
from pfc.target import Target

TARGET = Target("test", "x86_64", 32, 64)


def check(source):
    return smt.check(lower(parse_preprocessed(source)), TARGET)


@pytest.mark.parametrize("status", ["sat", "unsat", "timeout", "unknown", "error"])
def test_headline_separates_entry_gate_from_satisfied_dependencies(status):
    proof = check("long f(long n) { __spec_requires(n > 0); __spec_assert(n >= 0); return n; }")
    proof = replace(
        proof,
        entry_results=(replace(proof.entry_results[0], status=status),),
        required_passed=status == "sat",
    )
    headline = smt.render_report(proof).splitlines()[0]
    assert "1/1 required formulas proved; required dependencies passed" in headline
    expected = "entry consistency established" if status == "sat" else "entry consistency not established"
    assert expected in headline


def test_failed_dependency_is_not_reported_as_entry_failure():
    proof = check("long f(long n) { __spec_assert(n > 0); __spec_assert(n >= 0); return n; }")
    headline = smt.render_report(proof).splitlines()[0]
    assert not proof.required_passed
    assert "required dependencies not passed; entry consistency established" in headline


def test_failed_formula_without_dependencies_does_not_invent_a_dependency_failure():
    proof = check("long f(long n) { __spec_assert(n > 0); return n; }")
    headline = smt.render_report(proof).splitlines()[0]
    assert not proof.required_passed
    assert "0/1 required formulas proved; required dependencies passed; entry consistency established" in headline
