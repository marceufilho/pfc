"""Expected-trap campaign: precondition violations against verified, overflowing inputs against checked (phase 2D)."""

import json
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import differential, evidence, generate, negative

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def profile(name):
    return generate.load_profile(ROOT / "pfc_eval" / "profiles" / f"{name}.json")


def test_collect_cases_separates_precondition_violations_from_overflows_and_keeps_inputs():
    program = kernel("sum")
    collected = negative.collect(program, profile("sum"), T64, seed=0, per_class=5)
    assert {c.kind for c in collected} == {"precondition", "overflow"}
    assert sum(c.kind == "precondition" for c in collected) == 5 and sum(c.kind == "overflow" for c in collected) == 5
    for case in collected:
        assert isinstance(case.inputs.arrays["a"], tuple) and case.rejection.kind in (
            "PreconditionViolated",
            "Overflow",
        )
        if case.kind == "precondition":
            assert case.false_conjuncts and all(i in (0, 1) for i in case.false_conjuncts)  # 0 <= n, n <= a_len
        else:
            assert case.false_conjuncts == ()
    assert negative.collect(program, profile("sum"), T64, seed=0, per_class=5) == collected  # seeded


def test_expectations_follow_the_rendered_conjuncts_and_the_build():
    # precondition cases trap in verified iff a *rendered* conjunct is false; overflow cases always trap (both builds)
    assert negative.expected_trap("precondition", "verified", false_conjuncts=(1,), rendered={0, 1}) is True
    assert negative.expected_trap("precondition", "verified", false_conjuncts=(2,), rendered={0, 1}) is False
    assert negative.expected_trap("precondition", "checked", false_conjuncts=(1,), rendered={0, 1}) is None
    assert negative.expected_trap("overflow", "checked", false_conjuncts=(), rendered=set()) is True
    assert negative.expected_trap("overflow", "verified", false_conjuncts=(), rendered=set()) is True


class FakeRunner:
    """Compiles for free; every run traps with SIGILL unless the case bytes were marked as benign."""

    def __init__(self, benign):
        self.benign = benign
        self.runs = 0

    def __call__(self, arguments, cwd, timeout_s):
        if "-o" in arguments:
            return differential.ProcessOutcome("ok", 0, 1.0, "")
        self.runs += 1
        if Path(arguments[1]).read_bytes() in self.benign:
            return differential.ProcessOutcome("ok", 0, 1.0, "")
        return differential.ProcessOutcome("crash", -4, 1.0, "signal 4")


def test_rows_record_agreement_per_case_and_build_and_never_count_a_compile_failure_as_agreement(tmp_path, monkeypatch):
    program = kernel("sum")
    collected = negative.collect(program, profile("sum"), T64, seed=1, per_class=3)
    runner = FakeRunner(benign=set())
    result = negative.run_kernel(program, profile("sum"), collected, compiler="clang-19", target=T64, runner=runner)
    rows = negative.rows(result, "sum")
    assert {r["class"] for r in rows} == {"precondition", "overflow"} and {r["build"] for r in rows} == {
        "checked",
        "verified",
    }
    assert all(r["observed"] == "sigill" for r in rows) and runner.runs == len(rows)
    verified_pre = [r for r in rows if r["class"] == "precondition" and r["build"] == "verified"]
    assert all(
        r["expected_trap"] is True and r["agreement"] is True for r in verified_pre
    )  # sum renders both conjuncts
    assert all(
        r["expected_trap"] is None and r["agreement"] is None
        for r in rows
        if r["class"] == "precondition" and r["build"] == "checked"
    )
    assert all(r["agreement"] is True for r in rows if r["class"] == "overflow")
    for row in rows:
        evidence.validate_row(negative.SCHEMA, row)

    def failing(arguments, cwd, timeout_s):
        return differential.ProcessOutcome("error", 1, 1.0, "compile error")

    broken = negative.run_kernel(program, profile("sum"), collected, compiler="clang-19", target=T64, runner=failing)
    broken_rows = negative.rows(broken, "sum")
    assert all(r["observed"] == "compile" and r["agreement"] in (False, None) for r in broken_rows)
    assert all(r["agreement"] is False for r in broken_rows if r["expected_trap"] is not None)
    assert negative.summarize({"sum": broken_rows})["passed"] is False


@pytest.mark.integration
def test_cli_negative_runs_sum_and_seals_evidence(tmp_path, monkeypatch):
    from pfc_eval.cli import main

    monkeypatch.chdir(ROOT)
    output = tmp_path / "negative"
    assert main(["negative", "kernels/sum", "--output", str(output), "--seed", "0", "--cases", "4"]) == 0
    assert evidence.status(output).complete
    rows = evidence.load(output).observations
    assert len(rows) == 4 * 2 * 2  # two classes, two builds
    assert all(r["agreement"] in (True, None) for r in rows)
    assert all(r["observed"] == "sigill" for r in rows if r["class"] == "overflow")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True and summary["kernels"]["sum"]["precondition"]["skipped_conjunct_cases"] == 0


def test_false_conjuncts_are_found_when_the_entry_is_not_the_first_function(tmp_path):
    """E26: collect() evaluated conjuncts against functions[0]; with a helper first, none was ever reported false."""
    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n\nlong helper(long x)\n{\n    return x;\n}\n\n'
        "long sum(const long a[], long a_len, long n)\n{\n    REQUIRES(0 <= n && n <= a_len);\n    long s = 0;\n"
        "    long i = 0;\n    while (i < n) {\n        INVARIANT(0 <= i && i <= n);\n        s = s + a[i];\n"
        "        i = i + 1;\n    }\n    return s;\n}\n",
        encoding="utf-8",
    )
    program = lower(parse_source(path))
    assert [f.name for f in program.functions] == ["helper", "sum"]
    data = json.loads((ROOT / "pfc_eval" / "profiles" / "sum.json").read_text(encoding="utf-8"))
    bound = generate.parse_profile({**data, "program_sha256": generate.program_digest(program)})
    collected = negative.collect(program, bound, T64, seed=0, per_class=5)
    violations = [c for c in collected if c.kind == "precondition"]
    assert len(violations) == 5 and all(c.false_conjuncts for c in violations)
