import os
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["sum", "dot", "saxpy", "prefix_sum"])
def test_each_requires_has_an_explicit_observed_consequence(name):
    from pfc.breakit import check_kernel

    p = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    report = check_kernel(p, read_target(os.environ.get("PFC_CLANG", "clang-19")))
    assert len(report.cases) == len(p.functions[0].contracts)
    safety = report.cases[0]
    assert safety.guard_restored and safety.proof_lost and safety.passed
    if name == "saxpy":
        alias = report.cases[1]
        assert alias.alias_fact_removed and not alias.guard_restored and not alias.proof_lost
        assert alias.passed  # User-approved alias consequence, explicitly distinct from bounds.
    else:
        assert report.passed


def test_approved_alias_criterion_is_explicitly_recorded():
    from pfc.breakit import check_kernel

    p = lower(parse_source(ROOT / "kernels/saxpy/kernel.c"))
    report = check_kernel(p, read_target(os.environ.get("PFC_CLANG", "clang-19")))
    assert report.passed and report.criterion == "dependent-consequence"
    assert report.cases[1].alias_fact_removed and not report.cases[1].guard_restored


def test_removed_guard_in_c_cannot_be_hidden_by_unchanged_decision_metadata(monkeypatch):
    from dataclasses import replace

    from pfc import breakit

    original = breakit.emit

    def mutant(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, c=result.c.replace("__builtin_trap();", "(void)0;"))

    monkeypatch.setattr(breakit, "emit", mutant)
    p = lower(parse_source(ROOT / "kernels/dot/kernel.c"))
    report = breakit.check_kernel(p, read_target(os.environ.get("PFC_CLANG", "clang-19")))
    assert report.cases[0].proof_lost and not report.cases[0].guard_restored and not report.passed
