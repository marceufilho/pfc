"""Seeded valid sum inputs and exact, independent observation comparisons."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from pfc.diagnostics import FrontendError
from pfc.difftest import (
    Observation,
    SumInput,
    compare,
    driver_source,
    generate_inputs,
    invoke,
    parse_observations,
    require_sum_profile,
    run_sum,
    validate_input,
)
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import Target

ROOT = Path(__file__).resolve().parents[1]
TARGET = Target("test clang", "test-target", 32, 64)


def test_seeded_corpus_has_1000_valid_cases_and_boundary_coverage():
    cases = generate_inputs(TARGET, seed=17)
    assert len(cases) == 1000 and cases == generate_inputs(TARGET, seed=17)
    assert cases != generate_inputs(TARGET, seed=18)
    assert any(not case.values for case in cases)
    assert any(case.n == 0 and case.values for case in cases)
    assert any(0 < case.n < len(case.values) for case in cases)
    assert any(len(case.values) == 64 for case in cases)
    assert any(-(2**63) in case.values for case in cases)
    assert any(2**63 - 1 in case.values for case in cases)
    for case in cases:
        validate_input(case, TARGET)
        assert 0 <= case.n <= len(case.values) <= 64
        running = 0
        for value in case.values[: case.n]:
            running += value
            assert -(2**63) <= running <= 2**63 - 1


@pytest.mark.parametrize("seed", [-1, 2**32, True, 1.5])
def test_invalid_seed_is_rejected(seed):
    with pytest.raises(FrontendError, match="seed"):
        generate_inputs(TARGET, seed=seed)


@pytest.mark.parametrize(
    "case",
    [
        SumInput((1,), -1),
        SumInput((1,), 2),
        SumInput((1,), True),
        SumInput((True,), 1),
        SumInput((2**63,), 0),
        SumInput((2**63 - 1, 1, -1), 3),
        SumInput((0,) * 65, 0),
    ],
)
def test_invalid_input_and_intermediate_overflow_rejected(case):
    with pytest.raises(FrontendError):
        validate_input(case, TARGET)


def test_long32_boundary_cases_and_literals_are_safe():
    target = replace(TARGET, long_bits=32)
    cases = generate_inputs(target, seed=0)
    for case in cases:
        validate_input(case, target)
    driver = driver_source((SumInput((-(2**31), 2**31 - 1), 2),), target)
    assert "(-2147483647L - 1L)" in driver
    assert "-2147483648L" not in driver


def test_profile_accepts_canonical_sum_ignoring_locations_and_rejects_other_semantics():
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    require_sum_profile(program)
    source = (
        (ROOT / "kernels/sum/kernel.c").read_text().replace('#include "spec.h"', "").replace('#include "kernel.h"', "")
    )
    source = source.replace("REQUIRES", "__spec_requires").replace("INVARIANT", "__spec_invariant")
    require_sum_profile(lower(parse_preprocessed(source, "different-location.c")))
    for text in [
        source.replace("s + a[i]", "s - a[i]"),
        source.replace("n <= a_len", "n < a_len"),
        source.replace("sum(", "other("),
    ]:
        with pytest.raises(FrontendError, match="reviewed sum"):
            require_sum_profile(lower(parse_preprocessed(text, "case.c")))


def test_parser_checks_ids_sizes_values_and_full_array():
    cases = (SumInput((), 0), SumInput((4, -2, 7), 2))
    observations = parse_observations("0 0 0\n1 2 3 4 -2 7\n", cases, TARGET)
    assert observations == (Observation(0, ()), Observation(2, (4, -2, 7)))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "0 2 2 4 -2\n",
        "1 2 3 4 -2 7\n",
        "0 x 3 4 -2 7\n",
        "0 2 3 4 -2 7\nextra\n",
        "0 2 3 4 -2 9223372036854775808\n",
    ],
)
def test_parser_rejects_missing_malformed_extra_or_out_of_range_output(text):
    with pytest.raises(ValueError):
        parse_observations(text, (SumInput((4, -2, 7), 2),), TARGET)


def test_comparison_detects_common_wrong_results_and_checksum_preserving_mutation():
    cases = (SumInput((3, 5), 2), SumInput((3, 5), 1))
    correct = (Observation(8, (3, 5)), Observation(3, (3, 5)))
    assert compare(cases, correct, correct).agreed == 2
    wrong = (Observation(7, (3, 5)), Observation(3, (4, 4)))
    report = compare(cases, wrong, wrong)
    assert report.compared == 2 and report.agreed == 0
    assert [item.index for item in report.failures] == [0, 1]
    assert report.failures[1].case == cases[1]


@pytest.mark.parametrize(
    "error,expected",
    [(subprocess.TimeoutExpired("tool", 10), "timeout"), (FileNotFoundError(), "error"), (UnicodeError(), "error")],
)
def test_process_errors_are_distinct_and_do_not_expose_payload(monkeypatch, tmp_path, error, expected):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("pfc.difftest.subprocess.run", fail)
    result = invoke(("tool",), tmp_path)
    assert result.status == expected and result.stdout == ""


def test_process_nonzero_stderr_and_oversized_output_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "pfc.difftest.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "private source"),
    )
    result = invoke(("tool",), tmp_path)
    assert result.status == "error" and "private" not in result.message
    monkeypatch.setattr("pfc.difftest.MAX_DIFF_OUTPUT_BYTES", 3)
    monkeypatch.setattr(
        "pfc.difftest.subprocess.run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "1234", "")
    )
    assert invoke(("tool",), tmp_path).status == "error"


def test_profile_failure_happens_before_compilation(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("must reject before probing or running external tools")

    monkeypatch.setattr("pfc.difftest.read_target", unexpected)
    with pytest.raises(FrontendError):
        run_sum(lower(parse_preprocessed("long f(long n) { return n; }", "case.c")), compiler="clang")
