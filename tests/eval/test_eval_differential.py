"""General three-build differential: driver, retention check, failure classes (roadmap E3, EV-R11)."""

import struct
from dataclasses import replace
from pathlib import Path

import pytest

from pfc import emit as emission_module
from pfc import smt
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import differential, generate, interpret

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def sum_profile(cases=4):
    return generate.Profile(
        kernel="sum",
        function="sum",
        program_sha256=generate.program_digest(kernel("sum")),
        cases=cases,
        arrays={"a": generate.ArraySpec(1, 8, -9, 9)},
        scalars={"n": generate.ScalarSpec("prefix", of=("a",))},
        boundary=(generate.Boundary({"a": ()}, {"n": 0}), generate.Boundary({"a": (1, 2)}, {"n": 2})),
        stress=0.0,
    )


def test_driver_reads_cases_from_a_file_and_writes_binary_observations():
    program = kernel("saxpy")
    source = differential.driver_source(program.functions[0], T64)
    assert "long saxpy(long alpha, const long x[], long x_len, long y[], long y_len, long n);" in source
    assert "fopen(argv[1]" in source and "fopen(argv[2]" in source and "malloc" in source
    assert "fflush" in source and "RLIMIT_CORE" in source
    assert "saxpy(alpha, x, x_len, y, y_len, n)" in source


def test_case_encoding_round_trips_and_expected_bytes_follow_the_output_layout():
    program = kernel("saxpy")
    function = program.functions[0]
    inputs = interpret.Inputs({"alpha": 2, "x_len": 2, "y_len": 3, "n": 2}, {"x": [1, -1], "y": [10, 20, 30]})
    expected = interpret.Observation(2, {"x": (1, -1), "y": (12, 18, 30)}, 1, ())
    case = generate.Case("0000", True, inputs, expected)
    encoded = differential.encode_cases((case,), function, T64)
    values = struct.unpack("<" + "q" * (len(encoded) // 8), encoded)
    # count, then per parameter in signature order: alpha; x count + values; x_len; y count + values; y_len; n.
    assert values == (1, 2, 2, 1, -1, 2, 3, 10, 20, 30, 3, 2)
    observed = differential.expected_bytes(case, function, T64)
    assert struct.unpack("<" + "q" * 6, observed) == (2, 1, -1, 12, 18, 30)
    assert differential.decode_observations(observed, (case,), function, T64) == [
        (2, {"x": (1, -1), "y": (12, 18, 30)})
    ]


def test_retention_check_accepts_real_emissions_and_catches_the_historical_mutant(monkeypatch):
    program = kernel("sum")
    checked = emission_module.emit(program, T64, mode="checked")
    unproved = emission_module.emit(program, T64, mode="verified")
    assert differential.retention_problems(checked, None) == ()
    assert differential.retention_problems(unproved, None) == ()
    original = emission_module._Emitter.guard

    def delete_without_proof(self, *args, **kwargs):
        start = len(self.lines)
        original(self, *args, **kwargs)
        del self.lines[start:]

    monkeypatch.setattr(emission_module._Emitter, "guard", delete_without_proof)
    mutant = emission_module.emit(program, T64, mode="verified")
    problems = differential.retention_problems(mutant, None)
    assert problems and "guard" in problems[0]


def test_removed_decision_requires_its_cited_proofs():
    program = kernel("sum")
    proof = smt.check(program, T64)
    verified = emission_module.emit(program, T64, mode="verified", proof=proof)
    assert any(d.removed for d in verified.decisions)
    assert differential.retention_problems(verified, proof) == ()
    stale = replace(
        proof,
        results=tuple(
            replace(r, status="failed", counterexample=None, dependencies_satisfied=False) for r in proof.results
        ),
        required_passed=False,
    )
    problems = differential.retention_problems(verified, stale)
    assert problems and "proved" in problems[0]
    assert differential.retention_problems(verified, None)


class FakeRunner:
    """Simulates the compile and run boundary without clang; the first call compiles, the second runs."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def __call__(self, arguments, cwd, timeout_s):
        self.calls.append(arguments)
        if arguments[0].endswith("clang-19") or "-o" in arguments:
            return differential.ProcessOutcome("ok", 0, 1.0, "")
        mode = Path(arguments[0]).name
        action = self.behaviour.get(mode, "ok")
        output = Path(arguments[2])
        case_bytes = self.expected
        if action == "ok":
            output.write_bytes(b"".join(case_bytes))
            return differential.ProcessOutcome("ok", 0, 1.0, "")
        if action == "timeout":
            output.write_bytes(b"".join(case_bytes[:1]))
            return differential.ProcessOutcome("timeout", None, 10.0, "process timeout")
        if action == "crash":
            output.write_bytes(b"".join(case_bytes[:2]))
            return differential.ProcessOutcome("crash", -4, 1.0, "signal 4")
        if action == "truncated":
            output.write_bytes(b"".join(case_bytes)[:-3])
            return differential.ProcessOutcome("ok", 0, 1.0, "")
        if action == "wrong":
            altered = list(case_bytes)
            altered[-1] = bytes(b ^ 1 for b in altered[-1])
            output.write_bytes(b"".join(altered))
            return differential.ProcessOutcome("ok", 0, 1.0, "")
        raise AssertionError(action)


def _run(behaviour, monkeypatch):
    program = kernel("sum")
    profile = sum_profile(cases=4)
    runner = FakeRunner(behaviour)
    generated = generate.generate(profile, program, T64, seed=0)
    runner.expected = [differential.expected_bytes(c, program.functions[0], T64) for c in generated.cases]
    monkeypatch.setattr(differential, "_clang_version_matches", lambda compiler, target: True)
    result = differential.run_kernel(
        program, profile, compiler="clang-19", target=T64, seed=0, timeout_ms=1000, runner=runner, generated=generated
    )
    return result


def test_all_builds_agreeing_passes(monkeypatch):
    result = _run({}, monkeypatch)
    assert result.passed and result.failures == 0
    assert [c.agreement for c in result.cases] == [True] * 4 and all(c.failure_kind is None for c in result.cases)
    assert {b.mode for b in result.builds} == {"plain", "checked", "verified"}
    assert all(b.observed_cases == 4 for b in result.builds)


def test_timeout_crash_truncation_and_mismatch_are_failures_never_agreement(monkeypatch):
    result = _run({"checked": "timeout"}, monkeypatch)
    assert not result.passed and [c.failure_kind for c in result.cases] == ["timeout"] * 4
    result = _run({"verified": "crash"}, monkeypatch)
    assert [c.failure_kind for c in result.cases] == [None, None, "crash", "not-run"]
    assert [c.agreement for c in result.cases] == [True, True, False, False] and result.failures == 2
    result = _run({"plain": "truncated"}, monkeypatch)
    assert result.cases[-1].failure_kind == "truncated" and result.failures == 1
    result = _run({"verified": "wrong"}, monkeypatch)
    assert result.cases[-1].failure_kind == "mismatch" and result.failures == 1


def test_mutant_retention_failure_blocks_the_kernel(monkeypatch):
    original = emission_module._Emitter.guard

    def delete_without_proof(self, *args, **kwargs):
        start = len(self.lines)
        original(self, *args, **kwargs)
        del self.lines[start:]

    monkeypatch.setattr(emission_module._Emitter, "guard", delete_without_proof)
    result = _run({}, monkeypatch)
    assert not result.passed and result.problem is not None and "retention" in result.problem
    assert any(b.retention for b in result.builds if b.mode == "verified")


def test_table_rows_and_bound_follow_the_preregistered_definitions(monkeypatch):
    result = _run({}, monkeypatch)
    rows = differential.rows(result)
    assert len(rows) == result.generated.drawn
    accepted = [r for r in rows if r["accepted"]]
    assert len(accepted) == 4 and {r["kernel"] for r in rows} == {"sum"}
    assert accepted[0]["boundary"] is True and accepted[0]["rejected_reason"] is None
    summary = differential.summarize((result,))
    entry = summary["kernels"]["sum"]
    assert entry["accepted"] == 4 and entry["seeded"] == 2 and entry["failures"] == 0
    assert entry["seeded_upper_bound"] == pytest.approx(1 - 0.05 ** (1 / 2))
    assert entry["rule_of_three"] == pytest.approx(3 / 2)
    table = differential.render(summary)
    assert "| sum |" in table and "bound" in table


def test_rows_record_guard_totals_and_removals_of_the_verified_build(monkeypatch):
    result = _run({}, monkeypatch)
    assert (
        result.guards_total == 1 and result.guards_removed == 1
    )  # sum: one bounds site, removed with the hand invariant
    rows = differential.rows(result)
    assert all(r["guards_total"] == 1 and r["guards_removed"] == 1 for r in rows)
    columns = {c.name: c for c in differential.SCHEMA.columns}
    assert "guards_total" in columns and "guards_removed" in columns
