"""Houdini B1 baseline: fixed family, probe construction, elimination rules (roadmap E6, EV-R14)."""

from pathlib import Path

import pytest

from pfc import ir
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import houdini

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def program(tmp_path, text):
    path = tmp_path / "kernel.c"
    path.write_text('#include "spec.h"\n' + text, encoding="utf-8")
    return lower(parse_source(path))


def test_candidate_enumeration_follows_the_family_over_scope_variables():
    found = houdini.candidates(kernel("sum").functions[0])
    assert len(found) == 1
    texts = found[0]
    names = ["a_len", "n", "s", "i"]
    assert len(texts) == 3 * len(names) + 3 * len(names) * (len(names) - 1) == 48
    assert "(0 <= i)" in texts and "(-1 <= s)" in texts and "(1 <= a_len)" in texts
    assert "(i <= n)" in texts and "(i <= (n + 1))" in texts and "(i <= (n - 1))" in texts
    assert "(n <= a_len)" in texts and "(i <= i)" not in texts
    assert len(set(texts)) == len(texts)
    outer, inner = houdini.candidates(kernel("matvec").functions[0])
    assert len(outer) == 3 * 6 + 3 * 6 * 5 and len(inner) == 3 * 8 + 3 * 8 * 7


def test_probe_carries_the_full_conjunction_and_places_asserts_at_head_and_body_end():
    p = kernel("sum")
    probe = houdini.build_probe(p, "sum", {0: ["(0 <= i)", "(i <= n)", "(1 <= i)"]})
    function = probe.program.functions[0]
    loop = [s for s in function.body.statements if isinstance(s, ir.While)][0]
    assert loop.invariant is not None
    assert houdini.render(loop.invariant.condition) == "(((0 <= i) && (i <= n)) && (1 <= i))"
    before = function.body.statements[function.body.statements.index(loop) - 3 : function.body.statements.index(loop)]
    assert all(isinstance(s, ir.Contract) for s in before)
    tail = loop.body.statements[-3:]
    assert all(isinstance(s, ir.Contract) for s in tail)
    assert sorted(probe.table.values()) == sorted(
        [(0, k, phase) for k in range(3) for phase in ("initiation", "preservation")]
    )
    assert len(probe.table) == 6


def test_two_conjunct_fixpoint_with_one_falsified_candidate(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(0 <= n);\n    long i = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i);\n        i = i + 1;\n    }\n    return i;\n}\n",
    )
    result = houdini.run_kernel(
        p, T64, candidates={0: ["(0 <= i)", "(i <= n)", "(1 <= i)", "(i <= 0)"]}, timeout_ms=5000
    )
    # Round 1: initiation refutes `1 <= i` (i = 0). Preservation of `i <= 0` is vacuous while the head
    # conjunction still contains `1 <= i` (1 <= i && i <= 0 is inconsistent), so it falls only in round 2.
    assert result.status == "fixpoint" and len(result.rounds) == 3
    first, second, third = result.rounds
    assert first.removed == {0: ("(1 <= i)",)} and first.failed_initiation == {0: ("(1 <= i)",)}
    assert first.failed_preservation == {}
    assert second.removed == {0: ("(i <= 0)",)} and second.failed_preservation == {0: ("(i <= 0)",)}
    assert third.removed == {} and third.timeouts == 0 and third.unknown == 0
    assert result.invariants == ("((0 <= i) && (i <= n))",)
    assert result.queries == 4 and result.solver_ms > 0  # three single-chunk probe rounds plus the final admission
    assert result.accepted is True


def test_timeout_and_unknown_never_remove_a_candidate(tmp_path):
    import z3

    p = program(
        tmp_path,
        "long f(long n)\n{\n    long i = 0;\n    while (i < n) {\n        INVARIANT(0 <= i);\n"
        "        i = i + 1;\n    }\n    return i;\n}\n",
    )

    class Stuck:
        def __init__(self):
            self.inner = z3.Solver()

        def set(self, **kwargs):
            self.inner.set(**kwargs)

        def add(self, *args):
            self.inner.add(*args)

        def check(self):
            return z3.unknown

        def reason_unknown(self):
            return "timeout"

        def model(self):
            raise AssertionError("no model for unknown")

    result = houdini.run_kernel(
        p, T64, candidates={0: ["(1 <= i)"]}, timeout_ms=1000, solver_factory=Stuck, max_rounds=2
    )
    # Nothing was refuted, but nothing was decided either: not a fixpoint, and repeating the round is pointless.
    assert result.status == "inconclusive" and len(result.rounds) == 1
    assert result.rounds[0].removed == {} and result.rounds[0].timeouts == 2
    assert result.invariants == ("(1 <= i)",) and result.accepted is False  # the frozen admission uses real Z3


def test_round_limit_is_distinguished_from_fixpoint(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(0 <= n);\n    long i = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i);\n        i = i + 1;\n    }\n    return i;\n}\n",
    )
    result = houdini.run_kernel(
        p, T64, candidates={0: ["(0 <= i)", "(i <= n)", "(1 <= i)"]}, timeout_ms=5000, max_rounds=1
    )
    assert result.status == "round-limit" and len(result.rounds) == 1
    assert result.rounds[0].removed == {0: ("(1 <= i)",)}
    assert result.invariants == ("((0 <= i) && (i <= n))",)


def test_empty_candidate_set_becomes_true(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    long i = 0;\n    while (i < n) {\n        INVARIANT(0 <= i);\n"
        "        i = i + 1;\n    }\n    return i;\n}\n",
    )
    result = houdini.run_kernel(p, T64, candidates={0: []}, timeout_ms=5000)
    assert result.invariants == ("1",) and result.status == "fixpoint" and len(result.rounds) == 1


def test_rejects_programs_without_loops(tmp_path):
    p = program(tmp_path, "long f(long n)\n{\n    return n;\n}\n")
    with pytest.raises(ValueError, match="loop"):
        houdini.run_kernel(p, T64, timeout_ms=1000)


def test_for_loop_preservation_is_checked_after_the_update():
    count = lower(parse_source(ROOT / "bench/external/acsl/count/kernel.c"))
    probe = houdini.build_probe(count, "count", {0: ["(i <= n)", "(i <= (n - 1))"]})
    function = probe.program.functions[0]
    loop = [s for s in function.body.statements if isinstance(s, ir.While)]
    assert len(loop) == 1 and not any(isinstance(s, ir.For) for s in function.body.statements)
    body = loop[0].body.statements
    assert isinstance(body[-3], ir.Assign) and houdini.render(body[-3].value) == "(i + 1)"  # the for update
    assert all(isinstance(s, ir.Contract) for s in body[-2:])
    result = houdini.run_kernel(count, T64, candidates={0: ["(0 <= i)", "(i <= n)", "(i <= (n - 1))"]}, timeout_ms=5000)
    assert result.remaining == (("(0 <= i)", "(i <= n)"),)  # i <= n - 1 fails after i = i + 1 when i = n - 1
    assert result.rounds[0].failed_preservation == {0: ("(i <= (n - 1))",)}


def test_hand_in_family_is_syntactic_up_to_strict_comparisons():
    search = kernel("binary_search").functions[0]
    loop = houdini.loops(search)[0]
    assert houdini._hand_in_family(loop, houdini.candidates(search)[0])  # hi < n reads as hi <= (n - 1)
    reverse = lower(parse_source(ROOT / "bench/external/acsl/reverse/kernel.c")).functions[0]
    loop = houdini.loops(reverse)[0]
    assert houdini.render(loop.invariant.condition) == "(((0 <= i) && (i <= half)) && (half <= (n - i)))"
    assert not houdini._hand_in_family(loop, houdini.candidates(reverse)[0])  # three variables: outside B1


def test_a_probe_over_the_vc_size_cap_is_split_until_it_fits_and_the_splits_are_recorded(tmp_path):
    from pfc import vc
    from pfc.diagnostics import FrontendError, SourceLocation

    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(0 <= n);\n    long i = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i);\n        i = i + 1;\n    }\n    return i;\n}\n",
    )
    family = {0: ["(0 <= i)", "(i <= n)", "(1 <= i)", "(i <= 0)"]}
    reference = houdini.run_kernel(p, T64, candidates=family, timeout_ms=5000)

    def capped(program):
        # The frozen generator refuses large probes with `vc-complexity`; here any probe asserting more than
        # one candidate (two `pfc_probe` tests per candidate) is "too large".
        if ir.print_c(program).count("pfc_probe !=") > 2:
            raise FrontendError("vc-complexity", "VC report exceeds the cumulative formula limit", SourceLocation("p"))
        return vc.generate(program)

    result = houdini.run_kernel(p, T64, candidates=family, timeout_ms=5000, generate=capped)
    assert result.status == reference.status == "fixpoint" and result.invariants == reference.invariants
    assert [r.removed for r in result.rounds] == [r.removed for r in reference.rounds]
    assert reference.probe_splits == 0 and result.probe_splits > 0 and result.queries > reference.queries
    assert houdini.rows(result, "f", p.functions[0])[0]["probe_splits"] == result.probe_splits


def test_a_single_candidate_probe_over_the_cap_counts_as_an_error_and_keeps_the_candidate(tmp_path):
    from pfc.diagnostics import FrontendError, SourceLocation

    p = program(
        tmp_path,
        "long f(long n)\n{\n    long i = 0;\n    while (i < n) {\n        INVARIANT(0 <= i);\n"
        "        i = i + 1;\n    }\n    return i;\n}\n",
    )

    def always_capped(program):
        raise FrontendError("vc-complexity", "too many structured execution paths", SourceLocation("p"))

    result = houdini.run_kernel(p, T64, candidates={0: ["(1 <= i)"]}, timeout_ms=1000, generate=always_capped)
    assert result.status == "inconclusive" and result.rounds[0].errors == 1 and result.rounds[0].removed == {}
    assert result.invariants == ("(1 <= i)",)


def test_rows_carry_the_family_and_the_state_columns(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(0 <= n);\n    long i = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i && i <= n);\n        i = i + 1;\n    }\n    return i;\n}\n",
    )
    result = houdini.run_kernel(p, T64, candidates={0: ["(0 <= i)", "(i <= n)"]}, timeout_ms=5000)
    default = houdini.rows(result, "f", p.functions[0])[0]
    assert default["family"] == "b1" and default["states_observed"] is None and default["candidates_truncated"] is None
    row = houdini.rows(result, "f", p.functions[0], family="b2", states={0: 120}, truncated={0: 3})[0]
    assert row["family"] == "b2" and row["states_observed"] == 120 and row["candidates_truncated"] == 3
    names = [c.name for c in houdini.SCHEMA.columns]
    assert {"family", "states_observed", "candidates_truncated"} <= set(names)


def test_rows_record_guard_sites_of_the_admission_when_a_target_is_given(tmp_path):
    p = program(
        tmp_path,
        "long f(const long a[], long a_len, long n)\n{\n    REQUIRES(0 <= n && n <= a_len);\n    long s = 0;\n"
        "    long i = 0;\n    while (i < n) {\n        INVARIANT(0 <= i && i <= n);\n        s = s + a[i];\n"
        "        i = i + 1;\n    }\n    return s;\n}\n",
    )
    result = houdini.run_kernel(p, T64, candidates={0: ["(0 <= i)", "(i <= n)"]}, timeout_ms=5000)
    row = houdini.rows(result, "f", p.functions[0], target=T64)[0]
    assert row["guards_total"] == 1 and row["guards_removed"] == (1 if result.accepted else 0)
    assert houdini.rows(result, "f", p.functions[0])[0]["guards_total"] is None
