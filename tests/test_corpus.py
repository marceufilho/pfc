import os
from pathlib import Path

import pytest

from pfc import ir
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["sum", "dot", "saxpy", "prefix_sum"])
def test_seeded_cases_are_valid_and_profile_is_bound(name):
    from pfc.corpus import generate, profile, validate

    p = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    target = read_target(os.environ.get("PFC_CLANG", "clang-19"))
    assert profile(p) == name
    cases = generate(name, target, seed=19)
    assert len(cases) == 1000 and cases == generate(name, target, seed=19)
    for case in cases:
        validate(name, case, target)
    assert ir.print_c(p)


def test_oracles_compare_every_mutated_and_untouched_cell():
    from pfc.corpus import Case, oracle
    from pfc.difftest import Observation

    assert oracle("dot", Case(((2, 3, 99), (4, 5, 88)), 2)) == Observation(23, (2, 3, 99, 4, 5, 88))
    assert oracle("saxpy", Case(((2, 3, 99), (4, 5, 88)), 2, 2)) == Observation(2, (2, 3, 99, 8, 11, 88))
    assert oracle("prefix_sum", Case(((2, 3, 99),), 2)) == Observation(2, (2, 5, 99))


@pytest.mark.integration
@pytest.mark.parametrize("name", ["dot", "saxpy", "prefix_sum"])
def test_thousand_cases_agree_in_three_real_binaries(name):
    from pfc.difftest import run_kernel

    report = run_kernel(
        lower(parse_source(ROOT / "kernels" / name / "kernel.c")),
        compiler=os.environ.get("PFC_CLANG", "clang-19"),
        seed=11,
    )
    assert report.status == "passed" and report.compared == report.agreed == 1000
    assert len(report.emissions) == 3


def test_unreviewed_program_and_invalid_arithmetic_are_rejected():
    from pfc.corpus import Case, profile, validate
    from pfc.diagnostics import FrontendError
    from pfc.parse import parse_preprocessed
    from pfc.target import Target

    target = Target("test", "test", 32, 64)
    with pytest.raises(FrontendError, match="difftest-profile"):
        profile(lower(parse_preprocessed("long dot(void){return 0;}")))
    with pytest.raises(ValueError, match="overflow"):
        validate("dot", Case(((2**63 - 1,), (2,)), 1), target)
    with pytest.raises(ValueError, match="length"):
        validate("prefix_sum", Case(((1,),), 2), target)


@pytest.mark.integration
def test_mutated_array_with_unchanged_return_is_detected_and_preserves_input(monkeypatch):
    from dataclasses import replace

    from pfc import difftest

    original = difftest.emit

    def mutant(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["mode"] == "verified":
            result = replace(result, c=result.c.replace("return n;", "a[0] = 0; return n;"))
        return result

    monkeypatch.setattr(difftest, "emit", mutant)
    p = lower(parse_source(ROOT / "kernels/prefix_sum/kernel.c"))
    result = difftest.run_kernel(p, compiler=os.environ.get("PFC_CLANG", "clang-19"), seed=17)
    assert result.status == "mismatch" and result.agreed < 1000
    failure = result.failures[0]
    assert failure.case.n > 0
    assert all(observed.value == failure.expected.value for observed in failure.observed)
    assert any(observed.values != failure.expected.values for observed in failure.observed)


@pytest.mark.parametrize("stage", ["compile", "run"])
def test_process_timeouts_are_distinct_and_never_agreement(stage):
    from pfc.difftest import ProcessResult, run_kernel

    calls = []

    def runner(args, cwd):
        calls.append(args)
        if stage == "compile" or len(calls) > 1:
            return ProcessResult("timeout", "", None, "process timeout")
        return ProcessResult("ok", "", 0, "")

    result = run_kernel(
        lower(parse_source(ROOT / "kernels/dot/kernel.c")),
        compiler=os.environ.get("PFC_CLANG", "clang-19"),
        runner=runner,
    )
    assert result.status == "timeout" and result.compared == result.agreed == 0
