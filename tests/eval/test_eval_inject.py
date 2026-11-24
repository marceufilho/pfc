"""Systematic false-invariant injection: mutation family, selection, model classes, reconstruction (roadmap E5)."""

from pathlib import Path

import pytest

from pfc import smt
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import inject, interpret

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def test_loops_expose_invariant_text_and_in_scope_scalars():
    found = inject.loops(kernel("sum").functions[0])
    assert len(found) == 1 and found[0].location.line == 11
    assert inject.render(found[0].invariant.condition) == "((0 <= i) && (i <= n))"
    assert [s.name for s in found[0].scope] == ["a_len", "n", "s", "i"]
    outer, inner = inject.loops(kernel("matvec").functions[0])
    assert (outer.index, inner.index) == (0, 1) and inner.location.line == 16
    assert [s.name for s in inner.scope] == ["matrix_len", "x_len", "y_len", "rows", "cols", "i", "s", "j"]


def test_mutation_family_for_sum_is_systematic_and_deduplicated():
    program = kernel("sum")
    loop = inject.loops(program.functions[0])[0]
    family = inject.family(program, "sum", loop, near_miss=("0 <= i && i < n",))
    texts = {(c.operator, c.text) for c in family}
    assert ("literal_false", "0") in texts and ("near_miss", "0 <= i && i < n") in texts
    assert {t for op, t in texts if op == "off_by_one"} == {
        "((1 <= i) && (i <= n))",
        "((-1 <= i) && (i <= n))",
        "((0 <= i) && (i <= (n + 1)))",
        "((0 <= i) && (i <= (n - 1)))",
    }
    assert {t for op, t in texts if op == "reversed"} == {"((0 >= i) && (i <= n))", "((0 <= i) && (i >= n))"}
    assert {t for op, t in texts if op == "dropped"} == {"(i <= n)", "(0 <= i)"}
    wrong = {t for op, t in texts if op == "wrong_variable"}
    assert (
        "((0 <= n) && (i <= n))" in wrong
        and "((0 <= i) && (i <= a_len))" in wrong
        and "((0 <= i) && (i <= i))" in wrong
    )
    assert len(wrong) == 9 and len(family) == 2 + 4 + 2 + 2 + 9
    assert all(c.invariants == (c.text,) for c in family)
    assert len({c.text for c in family}) == len(family)


def test_selection_is_seeded_keeps_controls_first_and_caps_at_twenty():
    program = kernel("matvec")
    inner = inject.loops(program.functions[0])[1]
    family = inject.family(program, "matvec", inner, near_miss=("0 <= i && i < rows", "0 <= j && j <= cols"))
    assert len(family) > 20
    assert [c.operator for c in family[:2]] == [
        "literal_false",
        "off_by_one",
    ]  # the curated inner entry equals the hand invariant
    chosen = inject.select(family, seed=3)
    assert len(chosen) == 20 and chosen[0].operator == "literal_false"
    assert chosen == inject.select(family, seed=3) and chosen != inject.select(family, seed=4)
    # Mutating the inner loop keeps the outer hand invariant in preorder position 0.
    assert all(c.invariants[0] == "((0 <= i) && (i <= rows))" for c in chosen)
    assert chosen[0].invariants == ("((0 <= i) && (i <= rows))", "0")
    outer = inject.loops(program.functions[0])[0]
    outer_family = inject.family(program, "matvec", outer, near_miss=("0 <= i && i < rows", "0 <= j && j <= cols"))
    assert [c.operator for c in outer_family[:2]] == ["literal_false", "near_miss"]
    assert outer_family[1].invariants == ("0 <= i && i < rows", "((0 <= j) && (j <= cols))")


def test_dropping_the_only_conjunct_yields_true():
    program = kernel("prefix_sum")
    loop = inject.loops(program.functions[0])[0]
    assert inject.render(loop.invariant.condition) == "((1 <= i) && (i <= n))"
    single = inject.family(program, "prefix_sum", loop, near_miss=None)
    dropped = {c.text for c in single if c.operator == "dropped"}
    assert dropped == {"(i <= n)", "(1 <= i)"}


def test_model_class_is_entry_only_for_parameters_and_memory_bases():
    function = kernel("sum").functions[0]
    ids = {p.symbol.name: p.symbol.id for p in function.parameters}
    i_id = next(s.id for s in inject.loops(function)[0].scope if s.name == "i")
    entry = smt.Counterexample(
        "entry-observation",
        (
            smt.ScalarValue(ids["n"], "n", -1),
            smt.ScalarValue(ids["a_len"], "a_len", 0),
            smt.ScalarValue("vc:3", "base_a", 0),  # VC memory base: entry-level, not loop state
            smt.ScalarValue(i_id, "i", 0, "entry-derived"),
        ),
        (),
    )
    assert inject.model_class(entry, function, "entry") == "entry"
    loop_state = smt.Counterexample(
        "abstract-state", (smt.ScalarValue(i_id, "i", 4), smt.ScalarValue(ids["n"], "n", 5)), ()
    )
    assert inject.model_class(loop_state, function, "bounds") == "abstract"
    havocked = smt.Counterexample(
        "abstract-state", (smt.ScalarValue("vc:7", "i", 4), smt.ScalarValue(ids["n"], "n", 5)), ()
    )
    assert inject.model_class(havocked, function, "bounds") == "abstract"
    assert inject.model_class(entry, function, "preservation") == "abstract"


def test_reconstruction_builds_entry_inputs_conservatively():
    function = kernel("sum").functions[0]
    ids = {p.symbol.name: p.symbol.id for p in function.parameters}
    model = smt.Counterexample(
        "entry-observation",
        (smt.ScalarValue(ids["n"], "n", 2), smt.ScalarValue(ids["a_len"], "a_len", 3)),
        (smt.ArrayCell(ids["a"], "a", 1, 7),),
    )
    inputs = inject.reconstruct(function, model, T64)
    assert inputs == interpret.Inputs({"a_len": 3, "n": 2}, {"a": (0, 7, 0)})
    huge = smt.Counterexample(
        "entry-observation", (smt.ScalarValue(ids["n"], "n", 0), smt.ScalarValue(ids["a_len"], "a_len", 10**9)), ()
    )
    assert isinstance(inject.reconstruct(function, huge, T64), str)
    missing = smt.Counterexample("entry-observation", (smt.ScalarValue(ids["n"], "n", 0),), ())
    assert inject.reconstruct(function, missing, T64) == interpret.Inputs({"a_len": 0, "n": 0}, {"a": ()})


def test_refutation_finds_reachable_violations_and_respects_true_candidates():
    program = kernel("sum")
    loop = inject.loops(program.functions[0])[0]
    weak = inject.with_invariants(program, "sum", ("((0 <= i) && (i <= (n - 1)))",))
    refutation = inject.refute(weak, T64, seed=0, draws=500)
    # The mutant is re-lowered from text, so line numbers differ from the original file; the loop index does not.
    assert refutation is not None and refutation.loop == loop.index == 0
    assert refutation.inputs.scalars["n"] >= 1
    assert (
        inject.refute(inject.with_invariants(program, "sum", ("((0 <= i) && (i <= (n + 1)))",)), T64, seed=0, draws=500)
        is None
    )


@pytest.mark.parametrize(
    "refuted, accepted, invariant_proved, expected",
    [
        (True, False, False, "false"),
        (True, True, True, "false"),  # admitted although refuted: unsound, still classified false
        (False, True, True, "valid-and-sufficient"),
        (False, False, True, "valid-but-insufficient"),
        (False, False, False, "unclassified"),
    ],
)
def test_candidate_classes(refuted, accepted, invariant_proved, expected):
    assert inject.classify(refuted=refuted, accepted=accepted, invariant_proved=invariant_proved) == expected


def test_fabricated_reproducible_model_that_does_not_reproduce_is_a_discrepancy(monkeypatch):
    program = kernel("sum")
    function = program.functions[0]
    ids = {p.symbol.name: p.symbol.id for p in function.parameters}
    model = smt.Counterexample(
        "entry-observation", (smt.ScalarValue(ids["n"], "n", 3), smt.ScalarValue(ids["a_len"], "a_len", 3)), ()
    )
    loop = inject.loops(function)[0]
    outcome = inject.reproduce(program, function, "entry", loop.location, model, T64, compiler=None, runner=None)
    assert outcome.status == "discrepancy" and "no invariant violation" in outcome.reason


def test_an_entry_model_whose_violation_is_observed_before_the_step_budget_runs_out_is_reproduced(monkeypatch):
    # Z3 may bind an unconstrained loop bound to a huge value: the interpreter records the head violation,
    # then exhausts its budget. The violation was observed, so the model reproduced (zilu13 in inject-zilu-20261109).
    program = kernel("sum")
    function = program.functions[0]
    ids = {p.symbol.name: p.symbol.id for p in function.parameters}
    model = smt.Counterexample(
        "entry-observation", (smt.ScalarValue(ids["n"], "n", 3), smt.ScalarValue(ids["a_len"], "a_len", 3)), ()
    )
    loop = inject.loops(function)[0]
    expected = f"INVARIANT at {loop.location.line}:{loop.location.column}"

    def exhausted(*args, **kwargs):
        return interpret.Rejection("Inconclusive", "step budget of 7 exhausted", None, (expected,))

    monkeypatch.setattr(inject.interpret, "run", exhausted)
    outcome = inject.reproduce(program, function, "entry", loop.location, model, T64, compiler=None, runner=None)
    assert outcome.status == "reproduced" and "step budget" in outcome.reason

    def silent(*args, **kwargs):
        return interpret.Rejection("Inconclusive", "step budget of 7 exhausted", None, ())

    monkeypatch.setattr(inject.interpret, "run", silent)
    outcome = inject.reproduce(program, function, "entry", loop.location, model, T64, compiler=None, runner=None)
    assert outcome.status == "discrepancy" and "Inconclusive" in outcome.reason
