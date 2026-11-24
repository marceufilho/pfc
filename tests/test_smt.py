"""Real Z3 expectations and injected solver failures at the adapter boundary."""

from dataclasses import replace
from pathlib import Path

import pytest
import z3

from pfc.diagnostics import FrontendError
from pfc.ir import ScalarType
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.smt import Encoder, check
from pfc.target import Target
from pfc.vc import Boolean, Integer, Limit, Operation

ROOT = Path(__file__).resolve().parents[1]
TARGET = Target("test clang", "test-target", 32, 64)


def unit(source):
    return lower(parse_preprocessed(source, "case.c"))


@pytest.mark.integration
def test_sum_required_prove_and_overflow_is_independent():
    report = check(lower(parse_source(ROOT / "kernels/sum/kernel.c")), TARGET)
    required = [item for item in report.results if item.obligation.category == "required"]
    assert len(required) == 3
    assert all(item.status == "proved" and item.dependencies_satisfied for item in required)
    assert report.required_passed
    assert [item.status for item in report.results[-2:]] == ["failed", "proved"]
    assert report.results[-2].counterexample is not None
    assert not report.check_removal_authorized
    assert sum(item.elapsed_ms for item in required) < 1000


@pytest.mark.integration
def test_false_invariant_has_entry_counterexample_with_derived_i():
    source = (ROOT / "kernels/sum/kernel.c").read_text()
    source = source.replace('#include "spec.h"', "").replace('#include "kernel.h"', "")
    source = source.replace("REQUIRES", "__spec_requires").replace("INVARIANT", "__spec_invariant")
    report = check(unit(source.replace("i <= n", "i < n")), TARGET)
    entry = report.results[0]
    assert entry.status == "failed"
    example = entry.counterexample
    values = {item.name: item.value for item in example.scalars}
    assert values["n"] == 0 and values["a_len"] >= 0 and values["i"] == 0
    assert next(item for item in example.scalars if item.name == "i").origin == "entry-derived"
    assert not example.replayed
    assert not report.required_passed
    assert not next(r for r in report.results if r.obligation.kind == "bounds").dependencies_satisfied


@pytest.mark.integration
def test_removing_requires_fails_bounds_with_genuine_abstract_model():
    report = check(
        unit("""long f(const long a[], long a_len, long n) {
        long i=0; while(i<n) { __spec_invariant(0<=i && i<=n); long x=a[i]; i=i+1; } return i;
    }"""),
        TARGET,
    )
    bounds = next(item for item in report.results if item.obligation.kind == "bounds")
    assert bounds.status == "failed"
    values = {item.name: item.value for item in bounds.counterexample.scalars}
    assert 0 <= values["i"] <= values["n"] and values["i"] < values["n"]
    assert not (0 <= values["i"] < values["a_len"])
    assert bounds.counterexample.kind == "abstract-state"
    assert not bounds.counterexample.replayed


@pytest.mark.parametrize(("left", "right", "expected"), [(-7, 3, -2), (7, -3, -2), (-7, -3, 2), (7, 3, 2), (1, 0, 0)])
def test_c_division_encoding_is_not_euclidean_division(left, right, expected):
    expression = Encoder(TARGET).encode(Operation("c_div", (Integer(left), Integer(right))))
    assert z3.simplify(expression).as_long() == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("long f(long n) { return 1L/0; }", "failed"),
        ("long f(long n) { return n/(-1L); }", "failed"),
        ("long f(long n) { __spec_requires(n>=0); return n/2L; }", "proved"),
        ("long f(long n) { return 0 && (1L/0); }", "proved"),
    ],
)
def test_required_division_and_short_circuit(source, expected):
    report = check(unit(source), TARGET)
    division = next(item for item in report.results if item.obligation.kind == "division")
    assert division.status == expected


def test_target_limits_are_instantiated_per_type():
    encoder = Encoder(TARGET)
    assert encoder.encode(Limit(ScalarType.LONG, "MIN")).as_long() == -(2**63)
    assert encoder.encode(Limit(ScalarType.INT, "MAX")).as_long() == 2**31 - 1


@pytest.mark.parametrize(
    "term",
    [
        Operation("evil", (Integer(1),)),
        Operation("+", (Integer(1),)),
        Operation("&&", (Integer(1), Boolean(True))),
        Operation("+", (Boolean(True), Integer(2))),
        Operation("select", (Integer(1), Integer(0))),
    ],
)
def test_encoding_rejects_unknown_operators_wrong_arity_and_sorts(term):
    with pytest.raises(ValueError):
        Encoder(TARGET).encode(term)


class FakeSolver:
    def __init__(self, verdict, reason=""):
        self.verdict = verdict
        self.reason = reason
        self.settings = {}
        self.assertions = []

    def set(self, **options):
        self.settings.update(options)

    def add(self, *expressions):
        self.assertions.extend(expressions)

    def check(self):
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict

    def reason_unknown(self):
        return self.reason

    def model(self):
        raise AssertionError("No model may be requested for a non-SAT result")


@pytest.mark.parametrize(
    ("verdict", "reason", "expected"),
    [
        (z3.unsat, "", "proved"),
        (z3.unknown, "timeout", "timeout"),
        (z3.unknown, "incomplete theory", "unknown"),
        (z3.unknown, "canceled", "unknown"),
        (None, "", "error"),
        (z3.Z3Exception("private payload"), "", "error"),
    ],
)
def test_fail_closed_statuses_and_timeout_configuration(verdict, reason, expected):
    solvers = []

    def factory():
        solver = FakeSolver(verdict, reason)
        solvers.append(solver)
        return solver

    result = check(unit("long f(long n) { return n+1; }"), TARGET, timeout_ms=123, solver_factory=factory).results[0]
    assert result.status == expected and result.counterexample is None
    assert solvers[0].settings["timeout"] == 123
    assert len(solvers[0].assertions) >= 2
    assert "private payload" not in (result.reason or "")


@pytest.mark.parametrize("timeout", [0, -1, True, 1.5, 60001])
def test_invalid_timeout_is_not_an_unbounded_solver_call(timeout):
    with pytest.raises(FrontendError) as caught:
        check(unit("long f(long n) { return n; }"), TARGET, timeout_ms=timeout)
    assert caught.value.diagnostic.rule == "solver-config"


def test_query_identity_includes_target_settings_and_formula():
    program = unit("long f(long n) { return n+1; }")
    first = check(program, TARGET)
    again = check(program, TARGET)
    timeout = check(program, TARGET, timeout_ms=1)
    target = check(program, replace(TARGET, long_bits=32))
    assert first.results[0].query_id == again.results[0].query_id
    assert first.results[0].query_id != timeout.results[0].query_id
    assert first.results[0].query_id != target.results[0].query_id


def test_same_named_distinct_symbols_are_not_conflated():
    from pfc.diagnostics import SourceLocation
    from pfc.ir import Symbol
    from pfc.vc import Reference

    symbol = Symbol("first", "n", ScalarType.LONG, location=SourceLocation("case.c"))
    encoder = Encoder(TARGET)
    left = encoder.encode(Reference(symbol))
    right = encoder.encode(Reference(replace(symbol, id="second")))
    solver = z3.Solver()
    solver.add(left != right)
    assert solver.check() == z3.sat


def test_missing_invariant_result_cannot_admit_dependent_proofs(monkeypatch):
    from pfc import vc

    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    original = vc.generate(program)
    incomplete = replace(
        original, obligations=tuple(item for item in original.obligations if item.kind != "preservation")
    )
    monkeypatch.setattr(vc, "generate", lambda program: incomplete)
    report = check(program, TARGET)
    bounds = next(item for item in report.results if item.obligation.kind == "bounds")
    assert bounds.status == "proved" and not bounds.dependencies_satisfied
    assert not report.required_passed


def test_model_extraction_error_never_fabricates_a_counterexample():
    class BrokenModel(FakeSolver):
        def model(self):
            raise z3.Z3Exception("model unavailable")

    report = check(unit("long f(long n) { return n+1; }"), TARGET, solver_factory=lambda: BrokenModel(z3.sat))
    assert report.results[0].status == "error"
    assert report.results[0].counterexample is None


def test_reported_failure_does_not_turn_required_success_into_overflow_claim():
    report = check(unit("long f(long n) { return n+1; }"), TARGET)
    assert report.required_passed
    assert report.results[0].status == "failed"
    assert not report.check_removal_authorized


def test_boolean_conversion_and_int_overflow_have_real_solver_meaning():
    report = check(unit("long f(long n) { _Bool b=n; return b+2; }"), TARGET)
    assert report.results[0].status == "proved"
    assert report.results[0].obligation.c_type is ScalarType.INT
