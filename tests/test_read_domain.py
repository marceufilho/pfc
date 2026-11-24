"""vc-v3: a value read from a typed object is representable in that type (ADR-0025)."""

import os
from pathlib import Path

import pytest

from pfc import smt, vc
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def target():
    return read_target(os.environ.get("PFC_CLANG", "clang-19"))


def unit(source):
    return lower(parse_preprocessed(source, "case.c"))


def test_current_model_retains_typed_reads():
    assert vc.MODEL.startswith("vc-v4:")


def test_sum_read_adds_one_type_domain_hypothesis_after_its_bounds_obligation():
    report = vc.generate(lower(parse_source(ROOT / "kernels/sum/kernel.c")))
    entry, preservation, bounds, addition, increment = report.obligations
    domain = "((LONG_MIN <= a[i]) && (a[i] <= LONG_MAX))"
    # The bounds obligation precedes the read; the read's domain may only help what follows it.
    assert domain not in [vc.render_term(h.formula) for h in bounds.hypotheses]
    assert [vc.render_term(h.formula) for h in addition.hypotheses if h.kind == "type-domain"][-1] == domain
    assert [vc.render_term(h.formula) for h in increment.hypotheses if h.kind == "type-domain"][-1] == domain
    assert vc.render_term(preservation.goal) == f"({domain} => ((0 <= (i + 1)) && ((i + 1) <= n)))"
    # Initiation never observes the body's read.
    assert all(vc.render_term(h.formula) != domain for h in entry.hypotheses)


def test_short_circuit_read_domain_is_conditioned_on_its_path():
    report = vc.generate(
        unit("long f(const long a[], long a_len, long n) { __spec_requires(a_len > 0); return n > 0 && a[0] + 1 > n; }")
    )
    overflow = next(item for item in report.obligations if item.kind == "overflow")
    domains = [vc.render_term(h.formula) for h in overflow.hypotheses if h.kind == "type-domain"]
    assert domains[-1] == "((n > 0) => ((LONG_MIN <= a[0]) && (a[0] <= LONG_MAX)))"


def test_bool_array_reads_are_zero_or_one():
    report = vc.generate(
        unit("long f(const _Bool a[], long a_len) { __spec_requires(a_len > 0); long x = a[0]; return x + 1; }")
    )
    domains = [vc.render_term(h.formula) for o in report.obligations for h in o.hypotheses if h.kind == "type-domain"]
    assert "((0 <= a[0]) && (a[0] <= 1))" in domains


@pytest.mark.parametrize("name", ["sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search"])
def test_counterexample_cells_are_representable_and_verdicts_unchanged(name, target):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    report = smt.check(program, target)
    assert report.required_passed
    low, high = target.long_range()
    models = [r.counterexample for r in report.results if r.counterexample]
    assert all(low <= cell.value <= high for model in models for cell in model.reads)
    # Synthetic memory bases are mathematical addresses, not C long objects.
    assert all(
        low <= value.value <= high
        for model in models
        for value in model.scalars
        if not (value.symbol_id.startswith("vc:") and value.name.startswith("base_"))
    )
    expected_failed = {"sum": 1, "dot": 2, "saxpy": 2, "prefix_sum": 1, "matvec": 2, "binary_search": 0}[name]
    assert sum(r.status == "failed" for r in report.results) == expected_failed
    assert all(r.obligation.category == "reported" for r in report.results if r.status == "failed")


def test_in_range_cells_make_a_straight_line_safety_model_replayable(target):
    """A read feeding a second index: the model's cell value must be a legal C long for replay."""
    from pfc.replay import reconstruct

    program = unit("long f(const long a[], long a_len) { __spec_requires(a_len > 0); return a[a[0]]; }")
    report = smt.check(program, target)
    failed = [r for r in report.results if r.status == "failed" and r.obligation.kind == "bounds"]
    assert failed, "the outer index a[0] is unconstrained and must fail bounds"
    candidate = reconstruct(program, report, failed[0].obligation.id)
    assert candidate.reason != "array model value is outside C long range"
