"""Check-removal authorization uses exact evidence and the arithmetic supporting it."""

from dataclasses import replace
from pathlib import Path

import pytest

from pfc import smt
from pfc.emit import GUARD_LINE, emit
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import Target

ROOT = Path(__file__).resolve().parents[1]
TARGET = Target("test clang", "test-target", 32, 64)


def unit(source):
    return lower(parse_preprocessed(source, "case.c"))


@pytest.fixture
def sum_program():
    return lower(parse_source(ROOT / "kernels/sum/kernel.c"))


def test_checked_retains_bounds_even_with_proof(sum_program):
    report = emit(sum_program, TARGET, mode="checked", proof=smt.check(sum_program, TARGET))
    assert "__builtin_trap()" in report.c
    assert "// proved: obligation" not in report.c
    assert len(report.decisions) == 1 and not report.decisions[0].removed
    assert "REQUIRES(" not in report.c and "INVARIANT(" not in report.c
    assert "__builtin_assume" not in report.c


def test_verified_sum_removes_only_with_cited_proof(sum_program):
    proof = smt.check(sum_program, TARGET)
    report = emit(sum_program, TARGET, mode="verified", proof=proof)
    bounds = next(item.obligation for item in proof.results if item.obligation.kind == "bounds")
    increment = proof.results[-1].obligation.id
    assert f"// proved: obligation {bounds.id}" in report.c
    assert not GUARD_LINE.findall(report.c)  # no guard-shaped trap; the unproved accumulator overflow trap stays (v5)
    assert report.decisions[0].removed
    assert increment in report.arithmetic_dependencies
    assert proof.results[-2].obligation.id not in report.arithmetic_dependencies
    assert "_Static_assert" in report.c


@pytest.mark.parametrize("status", ["failed", "timeout", "unknown", "error"])
@pytest.mark.parametrize("kind", ["bounds", "entry", "preservation"])
def test_failed_or_unavailable_proof_retains_check(sum_program, status, kind):
    proof = smt.check(sum_program, TARGET)
    results = tuple(replace(item, status=status) if item.obligation.kind == kind else item for item in proof.results)
    report = emit(sum_program, TARGET, mode="verified", proof=replace(proof, results=results))
    assert not report.decisions[0].removed and "__builtin_trap()" in report.c


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "formula", "context", "query", "target", "version", "encoding", "timeout"]
)
def test_stale_or_incomplete_evidence_cannot_authorize(sum_program, change):
    proof = smt.check(sum_program, TARGET)
    if change == "missing":
        proof = replace(proof, results=proof.results[:-1])
    elif change == "duplicate":
        proof = replace(proof, results=proof.results + (proof.results[0],))
    elif change in {"formula", "query"}:
        index = next(i for i, result in enumerate(proof.results) if result.obligation.kind == "bounds")
        item = proof.results[index]
        item = (
            replace(item, query_id="stale")
            if change == "query"
            else replace(item, obligation=replace(item.obligation, dependencies=()))
        )
        proof = replace(proof, results=proof.results[:index] + (item,) + proof.results[index + 1 :])
    else:
        field, value = {
            "context": ("context_id", "stale"),
            "target": ("target", replace(TARGET, long_bits=32)),
            "version": ("solver_version", "stale"),
            "encoding": ("encoding", "stale"),
            "timeout": ("timeout_ms", 0),
        }[change]
        proof = replace(proof, **{field: value})
    assert not emit(sum_program, TARGET, mode="verified", proof=proof).decisions[0].removed


def test_missing_report_and_changed_program_retain_checks(sum_program):
    assert not emit(sum_program, TARGET, mode="verified").decisions[0].removed
    changed = unit("long f(const long a[], long a_len, long n) { return a[n]; }")
    assert not emit(changed, TARGET, mode="verified", proof=smt.check(sum_program, TARGET)).decisions[0].removed


def test_relevant_overflow_failure_retains_bounds_even_if_raw_bounds_proved(sum_program):
    proof = smt.check(sum_program, TARGET)
    last = replace(proof.results[-1], status="unknown")
    report = emit(sum_program, TARGET, mode="verified", proof=replace(proof, results=proof.results[:-1] + (last,)))
    assert not report.decisions[0].removed
    assert "arithmetic" in report.decisions[0].reason


def test_false_invariant_and_removed_precondition_retain_checks():
    source = """long f(const long a[], long a_len, long n) {
    __spec_requires(0<=n && n<=a_len); long i=0;
    while(i<n) { __spec_invariant(0<=i && i<=n); long s=a[i]; i=i+1; } return i; }"""
    for text in [source.replace("i<=n", "i<n"), source.replace("__spec_requires(0<=n && n<=a_len);", "")]:
        program = unit(text)
        assert not emit(program, TARGET, mode="verified", proof=smt.check(program, TARGET)).decisions[0].removed


def test_transitive_data_dependencies_include_overflow():
    program = unit("""long f(const long a[], long a_len, long n) {
        __spec_requires(0<=n && n<a_len); long j=n+1; long i=j; return a[i]; }""")
    proof = smt.check(program, TARGET)
    report = emit(program, TARGET, mode="verified", proof=proof)
    assert proof.results[0].obligation.id in report.arithmetic_dependencies
    assert not report.decisions[0].removed


def test_generated_temporaries_do_not_capture_source_names():
    program = unit("long f(long pfc_tmp_1) { long pfc_tmp_2=pfc_tmp_1+1; return pfc_tmp_2/2; }")
    report = emit(program, TARGET, mode="checked")
    assert report.c.count("long pfc_tmp_1") == 1
    assert report.c.count("long pfc_tmp_2 =") == 1


def test_invalid_mode_is_rejected(sum_program):
    from pfc.diagnostics import FrontendError

    with pytest.raises(FrontendError):
        emit(sum_program, TARGET, mode="invalid")


def test_ambiguous_locations_retain_each_operation():
    program = unit("long f(const long a[], long a_len) { __spec_requires(a_len>1); return a[0]+a[1]; }")
    function = program.functions[0]
    statement = function.body.statements[0]
    expression = statement.value
    right = replace(expression.right, location=expression.left.location)
    statement = replace(statement, value=replace(expression, right=right))
    program = replace(program, functions=(replace(function, body=replace(function.body, statements=(statement,))),))
    proof = smt.check(program, TARGET)
    assert proof.required_passed
    report = emit(program, TARGET, mode="verified", proof=proof)
    assert len(report.decisions) == 2 and all(not item.removed for item in report.decisions)
    assert all(item.obligation_ids == () for item in report.decisions)


@pytest.mark.parametrize("mode", ["checked", "verified"])
def test_sum_emission_matches_reviewed_golden(mode):
    source = (ROOT / "kernels/sum/kernel.c").read_text()
    source = source.replace('#include "spec.h"', "").replace('#include "kernel.h"', "")
    source = source.replace("REQUIRES", "__spec_requires").replace("INVARIANT", "__spec_invariant")
    program = lower(parse_preprocessed(source, "kernels/sum/kernel.c"))
    output = emit(program, TARGET, mode=mode, proof=smt.check(program, TARGET) if mode == "verified" else None)
    assert output.c == (ROOT / f"tests/golden/sum-{mode}.c").read_text()


# --- emitter candidate v5: pure-scalar REQUIRES entry checks (verified) and overflow traps unless proved ---


def test_verified_emits_sequential_entry_checks_for_pure_scalar_conjuncts(sum_program):
    report = emit(sum_program, TARGET, mode="verified", proof=smt.check(sum_program, TARGET))
    lines = report.c.splitlines()
    first = next(i for i, line in enumerate(lines) if "// entry check 1:" in line)
    assert lines[first + 1].strip() == "_Bool pfc_entry_1 = ((long)0 <= n);"
    assert lines[first + 2].strip() == "if (!pfc_entry_1) __builtin_trap();"
    assert lines[first + 3].strip().startswith("// entry check 2:")
    assert lines[first + 4].strip() == "_Bool pfc_entry_2 = (n <= a_len);"
    assert lines[first + 5].strip() == "if (!pfc_entry_2) __builtin_trap();"
    body = next(i for i, line in enumerate(lines) if line.strip().startswith("long pfc_tmp_1"))
    assert first < body  # entry checks precede the first body statement and any capacity temporary
    assert [(c.conjunct_index, c.rendered, c.reason) for c in report.requires_checks] == [
        (0, "((long)0 <= n)", None),
        (1, "(n <= a_len)", None),
    ]
    assert all(c.name == f"pfc_entry_{k + 1}" and c.requires_index == 0 for k, c in enumerate(report.requires_checks))
    assert report.emitter.startswith("emitter-v5")


def test_checked_and_plain_emit_no_entry_checks(sum_program):
    for mode in ("checked", "plain"):
        report = emit(sum_program, TARGET, mode=mode)
        assert "pfc_entry_" not in report.c and report.requires_checks == ()


def test_entry_checks_record_skipped_conjuncts_with_reason():
    saxpy = lower(parse_source(ROOT / "kernels/saxpy/kernel.c"))
    report = emit(saxpy, TARGET, mode="verified", proof=smt.check(saxpy, TARGET))
    skipped = [c for c in report.requires_checks if c.rendered is None]
    assert skipped and all("separation" in c.reason for c in skipped)
    assert "// entry check skipped:" in report.c and "SEPARATED" not in report.c.split("{", 1)[1]
    memory = unit("long f(const long a[], long a_len) { __spec_requires(a_len > 0 && a[0] > 0); return a[0]; }")
    report = emit(memory, TARGET, mode="verified", proof=smt.check(memory, TARGET))
    assert [c.rendered for c in report.requires_checks] == ["(a_len > (long)0)", None]
    assert "memory" in report.requires_checks[1].reason
    arithmetic = unit("long g(const long a[], long a_len, long n) { __spec_requires(n + 1 <= a_len); return n; }")
    report = emit(arithmetic, TARGET, mode="verified", proof=smt.check(arithmetic, TARGET))
    assert report.requires_checks[0].rendered is None and "arithmetic" in report.requires_checks[0].reason
    negated = unit("long h(long n) { __spec_requires(-n <= 0); return n; }")
    report = emit(negated, TARGET, mode="verified", proof=smt.check(negated, TARGET))
    assert report.requires_checks[0].rendered is None


def test_division_conjunct_renders_only_after_a_rendered_positive_divisor():
    matvec = lower(parse_source(ROOT / "kernels/matvec/kernel.c"))
    report = emit(matvec, TARGET, mode="verified", proof=smt.check(matvec, TARGET))
    by_text = {c.text: c for c in report.requires_checks}
    assert by_text["(rows <= (matrix_len / cols))"].rendered == "(rows <= (matrix_len / cols))"
    without = unit(
        "long f(const long m[], long m_len, long rows, long cols) {"
        " __spec_requires(0 <= rows && rows <= m_len / cols); return rows; }"
    )
    report = emit(without, TARGET, mode="verified", proof=smt.check(without, TARGET))
    assert report.requires_checks[1].rendered is None and "divisor" in report.requires_checks[1].reason
    literal = unit(
        "long g(const long a[], long a_len, long n) { __spec_requires(a_len > 0 && n <= a_len / 2); return n; }"
    )
    report = emit(literal, TARGET, mode="verified", proof=smt.check(literal, TARGET))
    assert report.requires_checks[1].rendered == "(n <= (a_len / (long)2))"
    after_skip = unit(
        "long h(const long a[], long a_len, const long b[], long b_len, long c, long r) {"
        " __spec_requires(__spec_separated(a, a_len, b, b_len) && 0 < c && r <= a_len / c); return r; }"
    )
    report = emit(after_skip, TARGET, mode="verified", proof=smt.check(after_skip, TARGET))
    assert [c.rendered is None for c in report.requires_checks] == [True, False, True]  # skipped earlier: no division


def test_entry_checks_preserve_bool_and_int_conversions():
    flagged = unit("long f(_Bool flag, long n) { __spec_requires((flag == 1 || flag == 0) && 0 <= n); return n; }")
    report = emit(flagged, TARGET, mode="verified", proof=smt.check(flagged, TARGET))
    assert all(c.rendered is not None for c in report.requires_checks)
    assert "(int)flag" in report.requires_checks[0].rendered and "(long)0 <= n" in report.requires_checks[1].rendered


def test_checked_sum_traps_accumulator_and_increment_overflow(sum_program):
    report = emit(sum_program, TARGET, mode="checked")
    assert report.c.count("__builtin_add_overflow(") == 2 and "__builtin_trap();" in report.c
    assert len(report.decisions) == 1 and len(report.overflow) == 2
    assert all(
        d.kind == "overflow" and not d.removed and d.reason == "checked mode retains every check"
        for d in report.overflow
    )
    assert (
        "long pfc_tmp_5;\n" in report.c
        and "if (__builtin_add_overflow(s, pfc_tmp_4, &pfc_tmp_5)) __builtin_trap();" in report.c
    )


def test_verified_sum_removes_proved_increment_overflow_and_keeps_accumulator_trap(sum_program):
    proof = smt.check(sum_program, TARGET)
    report = emit(sum_program, TARGET, mode="verified", proof=proof)
    accumulator, increment = report.overflow
    assert not accumulator.removed and accumulator.reason == "overflow obligation not proved"
    assert increment.removed and increment.reason == "overflow obligation proved with dependencies"
    assert report.c.count("__builtin_add_overflow(") == 1  # the accumulator trap stays
    assert f"// proved: obligation {increment.obligation_ids[0]}" in report.c
    assert report.decisions[0].removed  # the bounds guard is still removed (ADR-0023 unchanged)
    assert all(d.condition for d in (*report.decisions, *report.overflow))
    assert report.decisions[0].site_id.startswith("bounds:")


def test_unproved_overflow_keeps_trap_even_when_unit_is_allowed(sum_program):
    from pfc import vc
    from pfc.authorization import authorize

    proof = smt.check(sum_program, TARGET)
    assert authorize(sum_program, vc.generate(sum_program), TARGET, proof).allowed
    report = emit(sum_program, TARGET, mode="verified", proof=proof)
    assert sum(not d.removed for d in report.overflow) == 1


def test_division_has_no_separate_overflow_trap():
    search = lower(parse_source(ROOT / "kernels/binary_search/kernel.c"))
    report = emit(search, TARGET, mode="checked")
    assert report.c.count("__builtin_trap();") == 2 + len(
        report.overflow
    )  # division guard + bounds guard + overflow sites
    assert len(report.overflow) == 5  # n-1, lo+(...), hi-lo, mid+1, mid-1; neither the `/` nor the literal -1 is a site
    assert "__builtin_mul_overflow" not in report.c


def test_unary_minus_and_int_arithmetic_use_sub_overflow_with_their_type():
    search = lower(parse_source(ROOT / "kernels/binary_search/kernel.c"))
    report = emit(search, TARGET, mode="checked")
    assert "int pfc_tmp_16 = (-1);" in report.c and "__builtin_sub_overflow(0, 1" not in report.c  # literal: no site
    negate = unit("long f(long x) { return -x; }")
    report = emit(negate, TARGET, mode="checked")
    assert "if (__builtin_sub_overflow(0L, x, &pfc_tmp_1)) __builtin_trap();" in report.c


def test_overflow_sites_do_not_enter_decisions_or_admission(sum_program):
    from pfc.build import emission_passed
    from pfc.propose.invariants import admit

    proof = smt.check(sum_program, TARGET)
    report = emit(sum_program, TARGET, mode="verified", proof=proof)
    assert emission_passed(report) and all(d.kind in ("bounds", "division") for d in report.decisions)
    assert admit(sum_program, '{"invariants": ["0 <= i && i <= n"]}', TARGET).accepted


def test_ambiguous_overflow_location_retains_trap():
    program = unit("long f(long x, long y) { return x + y + (x + y); }")
    proof = smt.check(program, TARGET)
    report = emit(program, TARGET, mode="verified", proof=proof)
    assert report.overflow and all(not d.removed for d in report.overflow)


def test_emitted_header_states_the_v5_policy(sum_program):
    report = emit(sum_program, TARGET, mode="checked")
    assert "Overflow traps guard" in report.c.splitlines()[1] and "Overflow is not instrumented" not in report.c
