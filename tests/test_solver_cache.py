"""Cache identity and admission must survive repeated checking, never invent proofs."""

from dataclasses import replace

import pytest
import z3

from pfc import smt
from pfc.lower import lower
from pfc.parse import parse_preprocessed
from pfc.target import Target

TARGET = Target("test", "x86_64", 32, 64)


def program(source="long f(long n) { __spec_assert(n>=0); return n; }"):
    return lower(parse_preprocessed(source, "cache.c"))


def test_identical_checks_reuse_solver_results():
    cache = smt.SolverCache()
    calls = []

    def factory():
        calls.append(1)
        return z3.Solver()

    first = smt.check(program(), TARGET, solver_factory=factory, cache=cache)
    second = smt.check(program(), TARGET, solver_factory=factory, cache=cache)
    assert len(calls) == 2  # One validity query and one entry-consistency query.
    assert not first.results[0].cache_hit and second.results[0].cache_hit
    assert second.results[0].counterexample == first.results[0].counterexample
    assert not second.required_passed


@pytest.mark.parametrize("change", ["source", "target", "timeout", "version", "encoding", "factory"])
def test_changed_context_cannot_reuse_results(change, monkeypatch):
    cache = smt.SolverCache()
    calls = []

    def factory():
        calls.append(1)
        return z3.Solver()

    kwargs = dict(solver_factory=factory, cache=cache)
    p = program()
    smt.check(p, TARGET, **kwargs)
    target = TARGET
    if change == "source":
        p = program("long f(long n) { __spec_assert(n>0); return n; }")
    elif change == "target":
        target = replace(TARGET, long_bits=32)
    elif change == "timeout":
        kwargs["timeout_ms"] = 1
    elif change == "version":
        monkeypatch.setattr(smt, "solver_version", lambda: "different")
    elif change == "encoding":
        monkeypatch.setattr(smt, "ENCODING", "different")
    else:
        kwargs["solver_factory"] = lambda: factory()
    report = smt.check(p, target, **kwargs)
    assert len(calls) == 4 and not report.results[0].cache_hit


def test_cache_does_not_admit_failed_assertion_dependencies():
    cache = smt.SolverCache()
    p = program("long f(long n) { __spec_assert(n>0); __spec_assert(n>=0); return n; }")
    smt.check(p, TARGET, cache=cache)
    report = smt.check(p, TARGET, cache=cache)
    assert all(r.cache_hit for r in report.results)
    assert report.results[1].status == "proved"
    assert not report.results[1].dependencies_satisfied and not report.required_passed


def test_timeout_is_retried_instead_of_becoming_a_permanent_cache_result():
    class Timeout:
        def set(self, **kwargs):
            pass

        def add(self, *args):
            pass

        def check(self):
            return z3.unknown

        def reason_unknown(self):
            return "timeout"

    cache = smt.SolverCache()
    for _ in range(2):
        report = smt.check(program(), TARGET, solver_factory=Timeout, cache=cache)
        assert report.results[0].status == "timeout" and not report.results[0].cache_hit


def test_cache_capacity_is_bounded_and_validated():
    with pytest.raises(ValueError):
        smt.SolverCache(capacity=0)
    cache = smt.SolverCache(capacity=1)
    p = program("long f(long n) { __spec_assert(n>0); __spec_assert(n>=0); return n; }")
    smt.check(p, TARGET, cache=cache)
    report = smt.check(p, TARGET, cache=cache)
    assert not any(r.cache_hit for r in report.results)
