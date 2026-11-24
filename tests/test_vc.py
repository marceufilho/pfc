"""Step 5 expectations are hand-derived, without calling the generator as an oracle."""

from dataclasses import replace
from pathlib import Path

import pytest

from pfc.diagnostics import FrontendError
from pfc.ir import ScalarType
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.vc import Integer, Operation, Reference, generate, render_term, substitute, weakest_precondition

ROOT = Path(__file__).resolve().parents[1]


def program(source):
    return lower(parse_preprocessed(source, "case.c"))


def sum_report():
    return generate(lower(parse_source(ROOT / "kernels/sum/kernel.c")))


def test_sum_has_exact_obligations_and_source_sites():
    report = sum_report()
    assert report.verification == "not-run"
    assert [(item.kind, item.category) for item in report.obligations] == [
        ("entry", "required"),
        ("preservation", "required"),
        ("bounds", "required"),
        ("overflow", "reported"),
        ("overflow", "reported"),
    ]
    assert [item.location.line for item in report.obligations] == [11, 11, 12, 12, 13]
    assert len({item.id for item in report.obligations}) == 5
    assert all(item.context_id == report.context_id for item in report.obligations)
    assert all(item.status == "not-run" for item in report.obligations)
    assert all(item.location.column is not None for item in report.obligations)


def test_sum_goals_match_hand_derived_formulas():
    entry, preservation, bounds, addition, increment = sum_report().obligations
    assert render_term(entry.goal) == "((0 <= 0) && (0 <= n))"
    # vc-v3: the body's read of a[i] contributes its object-domain premise to the ending (ADR-0025).
    domain = "((LONG_MIN <= a[i]) && (a[i] <= LONG_MAX))"
    assert render_term(preservation.goal) == f"({domain} => ((0 <= (i + 1)) && ((i + 1) <= n)))"
    assert render_term(bounds.goal) == "((0 <= i) && (i < a_len))"
    assert render_term(addition.goal) == "((LONG_MIN <= (s + a[i])) && ((s + a[i]) <= LONG_MAX))"
    assert render_term(increment.goal) == "((LONG_MIN <= (i + 1)) && ((i + 1) <= LONG_MAX))"
    assert [render_term(h.formula) for h in bounds.hypotheses if h.kind != "type-domain"] == [
        "((0 <= n) && (n <= a_len))",
        "((0 <= i) && (i <= n))",
        "(i < n)",
    ]
    assert [h.kind for h in entry.hypotheses if h.kind != "type-domain"] == ["requires"]
    assert [h.kind for h in preservation.hypotheses if h.kind != "type-domain"] == [
        "requires",
        "induction-hypothesis",
        "guard",
    ]
    assert bounds.dependencies == (entry.id, preservation.id)
    assert preservation.dependencies == ()  # Inductive premise is not self-certified.


def test_wp_substitutes_backwards_without_substituting_inside_replacement():
    function = program("long f(long x, long y) { x = x + 1; y = x; return y; }").functions[0]
    x, y = (p.symbol for p in function.parameters)
    post = Operation("==", (Reference(y), Integer(7)))
    result = weakest_precondition(function.body.statements[:2], post)
    assert render_term(result) == "((x + 1) == 7)"
    assert render_term(substitute(Reference(x), x.id, Operation("+", (Reference(x), Integer(1))))) == "(x + 1)"
    other = replace(x, id="different-scope")
    assert substitute(Reference(other), x.id, Integer(0)) == Reference(other)


def test_missing_requires_removes_the_hypothesis_and_changes_identity():
    source = (ROOT / "kernels/sum/kernel.c").read_text()
    source = source.replace('#include "spec.h"', "").replace('#include "kernel.h"', "")
    source = source.replace("REQUIRES", "__spec_requires").replace("INVARIANT", "__spec_invariant")
    original = generate(program(source))
    altered = generate(program(source.replace("__spec_requires(0 <= n && n <= a_len);", "")))
    old_bounds = next(item for item in original.obligations if item.kind == "bounds")
    new_bounds = next(item for item in altered.obligations if item.kind == "bounds")
    assert not any(h.kind == "requires" for h in new_bounds.hypotheses)
    assert original.context_id != altered.context_id
    assert old_bounds.id != new_bounds.id
    assert render_term(new_bounds.goal) == "((0 <= i) && (i < a_len))"


def test_incorrect_invariant_changes_entry_goal_without_becoming_a_fact():
    unit = program("""long f(long n) {
        __spec_requires(n >= 0); long i = 0;
        while (i < n) { __spec_invariant(0 <= i && i < n); i = i + 1; }
        return i;
    }""")
    entry = generate(unit).obligations[0]
    assert render_term(entry.goal) == "((0 <= 0) && (0 < n))"
    assert entry.status == "not-run"
    assert all(h.kind != "invariant" for h in entry.hypotheses)


def test_assignment_before_access_is_substituted_into_bounds():
    report = generate(program("long f(const long a[], long a_len, long n) { long i = n; i = i + 1; return a[i]; }"))
    bounds = next(item for item in report.obligations if item.kind == "bounds")
    assert render_term(bounds.goal) == "((0 <= (n + 1)) && ((n + 1) < a_len))"


@pytest.mark.parametrize(("operator", "condition"), [("&&", "(n != 0)"), ("||", "(!(n != 0))")])
def test_short_circuit_guards_division_and_reports_overflow(operator, condition):
    report = generate(program(f"long f(long n, long d) {{ return n != 0 {operator} 8L / d; }}"))
    division, overflow = report.obligations
    assert division.kind == "division" and division.category == "required"
    assert render_term(division.goal) == f"({condition} => ((d != 0) && (!((8 == LONG_MIN) && (d == -1)))))"
    assert overflow.kind == "overflow" and overflow.category == "reported"
    assert "c_div(8, d)" in render_term(overflow.goal)


def test_loop_guard_safety_does_not_assume_guard_is_true():
    report = generate(program("""long f(const long a[], long a_len, long n) {
        long i = 0; while (a[i] < n) { __spec_invariant(i >= 0); i = i + 1; } return i;
    }"""))
    bounds = next(item for item in report.obligations if item.kind == "bounds")
    assert not any(h.kind == "guard" for h in bounds.hypotheses)
    assert bounds.dependencies == (report.obligations[0].id, report.obligations[1].id)


def test_exit_access_uses_negated_guard_and_arbitrary_loop_state():
    report = generate(program("""long f(const long a[], long a_len, long n) {
        long i = 0; while (i < n) { __spec_invariant(0 <= i && i <= n); i = i + 1; } return a[i];
    }"""))
    bounds = next(item for item in report.obligations if item.kind == "bounds")
    assert render_term(bounds.goal) == "((0 <= i) && (i < a_len))"
    assert [render_term(h.formula) for h in bounds.hypotheses if h.kind == "guard"] == ["(!(i < n))"]


@pytest.mark.parametrize(
    "source",
    [
        "long f(long n) { while(n) { n=n-1; } return n; }",
        "long f(long n) { for(; n; ) {} return n; }",
    ],
)
def test_loops_still_require_an_invariant(source):
    with pytest.raises(FrontendError) as caught:
        generate(program(source))
    assert caught.value.diagnostic.rule == "vc-invariant"
    assert caught.value.diagnostic.location.file == "case.c"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("long f(long n) { if(n) return 1; else return 0; }", []),
        ("long f(long a[], long a_len) { a[0]=1; return 0; }", ["bounds"]),
        ("long f(long n) { n=0; return n; }", []),
        ("long f(long n) { __spec_ensures(n>0); return n; }", ["postcondition"]),
        ("long f(long n) { __spec_assert(n>0); return n; }", ["assert"]),
        ("long f(long n) { __spec_requires(1/n); return n; }", ["division"]),
        ("long f(long n) { __spec_requires(n+1>0); return n; }", []),
        ("long f(long a[],long a_len) { __spec_requires(a[0]>0); return 0; }", ["bounds"]),
        ("long f(void) { return 0; } long g(void) { return 0; }", []),
    ],
)
def test_step11_replaces_slice_rejections_with_explicit_obligations(source, expected):
    report = generate(program(source))
    assert [item.kind for item in report.obligations] == expected
    assert report.verification == "not-run"


def test_numeric_bool_conversion_and_integer_promotions_survive():
    report = generate(program("long f(long n) { _Bool b = n; return b + 2; }"))
    overflow = report.obligations[0]
    assert overflow.c_type is ScalarType.INT
    assert "ite((n != 0), 1, 0)" in render_term(overflow.goal)
    assert "INT_MIN" in render_term(overflow.goal) and "INT_MAX" in render_term(overflow.goal)


def test_identity_is_deterministic_and_tracks_program_model_and_location():
    unit = program("long f(long n) { return n + 1; }")
    report = generate(unit)
    assert generate(unit) == report
    changed = generate(program("\nlong f(long n) { return n + 1; }"))
    assert changed.context_id != report.context_id
    assert changed.obligations[0].id != report.obligations[0].id
    assert "target" in report.model.lower() and "not" in report.model.lower()


def test_formula_growth_fails_closed_before_large_output(monkeypatch):
    import pfc.vc as vc

    monkeypatch.setattr(vc, "MAX_VC_TERM_NODES", 200, raising=False)
    unit = program("long f(long n) { long x=n; " + "x=x+x; " * 10 + "return x; }")
    with pytest.raises(FrontendError) as caught:
        generate(unit)
    assert caught.value.diagnostic.rule == "vc-complexity"


def test_dead_assignment_still_emits_its_division_obligation():
    report = generate(program("long f(long n) { long ignored=1L/n; return 0; }"))
    assert [item.kind for item in report.obligations] == ["division", "overflow"]
    assert "(n != 0)" in render_term(report.obligations[0].goal)


def test_each_repeated_site_has_a_distinct_identity():
    report = generate(program("long f(const long a[], long a_len) { return a[0] + a[0]; }"))
    assert [item.kind for item in report.obligations] == ["bounds", "bounds", "overflow"]
    assert len({item.id for item in report.obligations}) == 3
    assert report.obligations[0].location.column != report.obligations[1].location.column


def test_nested_short_circuit_accumulates_path_conditions():
    report = generate(program("long f(long n, long d) { return n && (d || 3L/n); }"))
    goal = render_term(report.obligations[0].goal)
    assert goal.startswith("(((n != 0) && (!(d != 0))) => ")


def test_arithmetic_reports_keep_distinct_types_and_all_sites():
    report = generate(program("long f(long n) { return -(n * n) - 1; }"))
    assert [item.kind for item in report.obligations] == ["overflow"] * 3
    assert all(item.c_type is ScalarType.LONG for item in report.obligations)
    assert render_term(report.obligations[0].goal) == "((LONG_MIN <= (n * n)) && ((n * n) <= LONG_MAX))"


def test_straight_line_program_can_have_no_obligations_without_claiming_verified():
    report = generate(program("long f(long n) { return n; }"))
    assert report.obligations == () and report.verification == "not-run"


def test_identity_covers_generated_formula_not_only_input(monkeypatch):
    import pfc.vc as vc

    unit = program("long f(long n) { return n + 1; }")
    original = generate(unit)
    monkeypatch.setattr(vc, "_range", lambda term, type_: vc.Boolean(True))
    changed = generate(unit)
    assert original.context_id == changed.context_id
    assert original.obligations[0].goal != changed.obligations[0].goal
    assert original.obligations[0].id != changed.obligations[0].id
