"""Only reconstructed concrete inputs and the intended guarded binary count as replay."""

import os
import signal
from dataclasses import replace

import pytest

from pfc import smt
from pfc.difftest import ProcessResult
from pfc.lower import lower
from pfc.parse import parse_preprocessed
from pfc.target import read_target


@pytest.fixture
def target():
    return read_target(os.environ.get("PFC_CLANG", "clang-19"))


def prepare(source, target):
    p = lower(parse_preprocessed(source, "replay.c"))
    report = smt.check(p, target)
    result = next(r for r in report.results if r.status == "failed" and r.obligation.kind in {"bounds", "division"})
    return p, report, result.obligation.id


@pytest.mark.integration
@pytest.mark.parametrize(
    "source",
    [
        "long f(long a[],long a_len,long i) { __spec_requires(a_len==2 && i==2); return a[i]; }",
        "long f(long n,long d) { __spec_requires(n==1 && d==0); return n/d; }",
    ],
)
def test_reconstructible_model_traps_checked_binary(source, target):
    from pfc.replay import replay

    p, report, identifier = prepare(source, target)
    result = replay(p, report, identifier, compiler=os.environ.get("PFC_CLANG", "clang-19"))
    assert result.replayed and result.input is not None
    assert result.execution.returncode == -signal.SIGILL
    assert all(not r.counterexample or not r.counterexample.replayed for r in report.results)


def test_arbitrary_loop_state_is_not_declared_reachable(target):
    from pfc.replay import reconstruct

    p, report, identifier = prepare(
        """long f(long a[],long a_len,long n) { long i=0;
while(i<n) { __spec_invariant(0<=i && i<=n); long x=a[i]; i=i+1; } return i; }""",
        target,
    )
    candidate = reconstruct(p, report, identifier)
    assert candidate.input is None and "straight-line" in candidate.reason


def test_changed_source_does_not_reconstruct_stale_model(target):
    from pfc.replay import reconstruct

    p, report, identifier = prepare("long f(long a[],long a_len) { return a[0]; }", target)
    changed = lower(parse_preprocessed("long f(long a[],long a_len) { return a[1]; }", "replay.c"))
    assert reconstruct(changed, report, identifier).input is None


def test_huge_or_negative_array_capacity_is_not_allocated(target):
    from pfc.replay import reconstruct

    for length in (-1, 1000):
        p, report, identifier = prepare(
            f"""long f(long a[],long a_len) {{
__spec_requires(a_len=={length}); return a[a_len]; }}""",
            target,
        )
        assert reconstruct(p, report, identifier).input is None


def test_other_signals_do_not_count_as_replay(target):
    from pfc.replay import replay

    p, report, identifier = prepare("long f(long n,long d) { return n/d; }", target)
    calls = []

    def runner(args, cwd):
        calls.append(args)
        return (
            ProcessResult("ok", "", 0, "") if len(calls) == 1 else ProcessResult("error", "", -signal.SIGSEGV, "signal")
        )

    result = replay(p, report, identifier, compiler=os.environ.get("PFC_CLANG", "clang-19"), runner=runner)
    assert not result.replayed and len(calls) == 2


def test_no_model_or_wrong_status_cannot_be_replayed(target):
    from pfc.replay import reconstruct

    p, report, identifier = prepare("long f(long n,long d) { return n/d; }", target)
    result = report.results[0]
    for status in ("proved", "timeout", "unknown", "error"):
        changed = replace(report, results=(replace(result, status=status, counterexample=None), *report.results[1:]))
        assert reconstruct(p, changed, identifier).input is None
