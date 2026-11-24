"""Per-conjunct break-it: splitting, dropping, witness search and change definitions (roadmap E4, EV-R12)."""

from pathlib import Path

import pytest

from pfc import ir
from pfc.lower import lower
from pfc.parse import SpecKind, parse_source
from pfc.target import Target
from pfc_eval import conjuncts, interpret

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def program(tmp_path, text):
    path = tmp_path / "kernel.c"
    path.write_text('#include "spec.h"\n' + text, encoding="utf-8")
    return lower(parse_source(path))


def test_requires_conjunctions_split_in_source_order():
    found = conjuncts.conjuncts(kernel("sum").functions[0])
    assert [(c.requires_index, c.index, c.text, c.separated) for c in found] == [
        (0, 0, "(0 <= n)", False),
        (0, 1, "(n <= a_len)", False),
    ]
    found = conjuncts.conjuncts(kernel("matvec").functions[0])
    assert [c.text for c in found[:4]] == [
        "(0 <= rows)",
        "(0 < cols)",
        "(0 <= matrix_len)",
        "(rows <= (matrix_len / cols))",
    ]
    assert [(c.requires_index, c.index) for c in found] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (1, 0),
        (1, 1),
        (2, 0),
        (3, 0),
    ]
    assert [c.separated for c in found] == [False] * 6 + [True, True]
    assert found[6].text.startswith("SEPARATED(matrix, matrix_len, y, y_len)")


def test_dropping_a_conjunct_rebuilds_or_removes_the_contract():
    sum_program = kernel("sum")
    first, second = conjuncts.conjuncts(sum_program.functions[0])
    variant = conjuncts.drop(sum_program, first)
    printed = ir.print_c(variant)
    assert "REQUIRES((n <= a_len));" in printed and "0 <= n" not in printed
    contract = variant.functions[0].contracts[0]
    assert contract.kind is SpecKind.REQUIRES and isinstance(contract.condition, ir.Convert)
    assert (
        contract.condition.type is ir.ScalarType.BOOL
        and contract.location == sum_program.functions[0].contracts[0].location
    )
    saxpy = kernel("saxpy")
    separated = [c for c in conjuncts.conjuncts(saxpy.functions[0]) if c.separated][0]
    without = conjuncts.drop(saxpy, separated)
    assert len(without.functions[0].contracts) == len(saxpy.functions[0].contracts) - 1
    assert "SEPARATED" not in ir.print_c(without)
    assert sum_program == kernel("sum")  # pure: the original program is not mutated


def test_normalized_c_ignores_proof_citations_only():
    a = "x;\n// proved: obligation bounds:aaa\ny;\n"
    b = "x;\n// proved: obligation bounds:bbb\ny;\n"
    assert conjuncts.normalize_c(a) == conjuncts.normalize_c(b) == "x;\ny;\n"
    assert conjuncts.normalize_c("x;\nif (!(0 <= i)) __builtin_trap();\n") != conjuncts.normalize_c("x;\n")
    digest_a = "/* justified restrict: context " + "a" * 64 + "; full entry regions */\n"
    digest_b = "/* justified restrict: context " + "b" * 64 + "; full entry regions */\n"
    assert conjuncts.normalize_c(digest_a) == conjuncts.normalize_c(digest_b)


def test_decision_comparison_separates_restored_from_newly_removed():
    original = {("bounds", "k.c:12:13"): True, ("bounds", "k.c:14:9"): False}
    variant = {("bounds", "k.c:12:13"): False, ("bounds", "k.c:14:9"): True}
    restored, newly_removed = conjuncts.compare_decisions(original, variant)
    assert restored == (("bounds", "k.c:12:13"),) and newly_removed == (("bounds", "k.c:14:9"),)


def test_witness_search_finds_the_out_of_bounds_read_behind_the_length_conjunct():
    sum_program = kernel("sum")
    lower_bound, upper_bound = conjuncts.conjuncts(sum_program.functions[0])
    search = conjuncts.find_witness(sum_program, conjuncts.drop(sum_program, upper_bound), T64, seed=0)
    assert search.witness is not None and search.witness.kind == "safety"
    assert (
        search.witness.location.line == 12
        and search.witness.inputs.scalars["n"] > search.witness.inputs.scalars["a_len"]
    )
    assert search.discriminating > 0
    search = conjuncts.find_witness(sum_program, conjuncts.drop(sum_program, lower_bound), T64, seed=0)
    assert search.witness is not None and search.witness.kind == "annotation"
    assert search.witness.inputs.scalars["n"] < 0 and search.witness.location.line == 11


def test_witness_search_reports_absence_and_aliasing_cases():
    saxpy = kernel("saxpy")
    separated = [c for c in conjuncts.conjuncts(saxpy.functions[0]) if c.separated][0]
    search = conjuncts.find_witness(saxpy, conjuncts.drop(saxpy, separated), T64, seed=0)
    assert search.witness is None and search.discriminating > 0
    matvec = kernel("matvec")
    cols = [c for c in conjuncts.conjuncts(matvec.functions[0]) if c.text == "(0 < cols)"][0]
    search = conjuncts.find_witness(matvec, conjuncts.drop(matvec, cols), T64, seed=0)
    assert search.witness is not None and search.witness.kind == "undefined"
    matrix_len = [c for c in conjuncts.conjuncts(matvec.functions[0]) if c.text == "(0 <= matrix_len)"][0]
    search = conjuncts.find_witness(matvec, conjuncts.drop(matvec, matrix_len), T64, seed=0)
    assert search.witness is None and search.discriminating == 0


def test_invariant_is_checked_at_the_loop_head_including_the_exit_check(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    long i = 0;\n    while (i < n) {\n        INVARIANT(0 <= i && i <= n);\n"
        "        i = i + 1;\n    }\n    return i;\n}\n",
    )
    observation = interpret.run(p, interpret.Inputs({"n": -1}, {}), target=T64)
    assert observation.annotation_violations == ("INVARIANT at 6:9",)
    assert interpret.run(p, interpret.Inputs({"n": 3}, {}), target=T64).annotation_violations == ()


def test_callee_precondition_witness_is_classified_separately(tmp_path):
    p = program(
        tmp_path,
        "long inc(long x)\n{\n    REQUIRES(x < 5);\n    return x + 1;\n}\n\n"
        "long twice(long x)\n{\n    REQUIRES(0 <= x && x < 4);\n    long y = inc(x);\n    return inc(y);\n}\n",
    )
    upper = [c for c in conjuncts.conjuncts(p.functions[1]) if c.text == "(x < 4)"][0]
    search = conjuncts.find_witness(p, conjuncts.drop(p, upper, function="twice"), T64, seed=1, function="twice")
    assert search.witness is not None and search.witness.kind == "callee" and search.witness.location.line in (11, 12)


def test_expectation_rules():
    assert conjuncts.expected(separated=True, witness_kind=None) == "restrict"
    assert conjuncts.expected(separated=False, witness_kind="safety") == "guard"
    assert conjuncts.expected(separated=False, witness_kind="callee") == "guard"
    assert conjuncts.expected(separated=False, witness_kind="annotation") == "annotation"
    assert conjuncts.expected(separated=False, witness_kind="undefined") == "annotation"
    assert conjuncts.expected(separated=False, witness_kind=None) == "none"
    with pytest.raises(ValueError):
        conjuncts.expected(separated=True, witness_kind="safety")


def test_witness_search_samples_from_the_weakened_contract_so_a_dropped_equality_can_be_violated(tmp_path):
    # With equality conjuncts assigned by the sampler (ADR-0047), drawing from the original contract would
    # satisfy the dropped `i == 0` on every draw and never discriminate. The search must draw from the variant.
    p = program(
        tmp_path,
        "long f(long i, long j, long k)\n{\n    REQUIRES(i == 0 && j == 0);\n    while (i <= k) {\n"
        "        INVARIANT(i == j);\n        i = i + 1;\n        j = j + 1;\n    }\n    ASSERT(j == i);\n"
        "    return j;\n}\n",
    )
    first = conjuncts.conjuncts(p.functions[0])[0]
    assert first.text == "(i == 0)"
    variant = conjuncts.drop(p, first)
    search = conjuncts.find_witness(p, variant, T64, seed=0, draws=200)
    assert search.discriminating > 0 and search.witness is not None and search.witness.kind == "annotation"


def _ablation(restored):
    conjunct = conjuncts.Conjunct(0, 1, "(n <= a_len)", None, False, None)
    search = conjuncts.Search(10, 1, None)
    return conjuncts.Ablation(
        conjunct, search, "guard", True, False, bool(restored), None, 0, False, False, True, "", False, restored
    )


def test_restored_sites_are_recorded_per_conjunct_with_kind_and_location():
    restored = (("bounds", "k.c:12:13"), ("division", "k.c:14:9"))
    result = conjuncts.KernelConjuncts(
        "k", "f", True, "", (_ablation(restored), _ablation(())), (1, 1), (0, 0), (0, 0), 0, 0
    )
    first, second = conjuncts.rows(result, "k")
    assert first["restored_sites"] == "bounds@k.c:12:13;division@k.c:14:9" and first["restored_site_count"] == 2
    assert second["restored_sites"] == "" and second["restored_site_count"] == 0
    names = [c.name for c in conjuncts.SCHEMA.columns]
    assert "restored_sites" in names and "restored_site_count" in names
    assert conjuncts.sites_text(restored) == "bounds@k.c:12:13;division@k.c:14:9"


def test_reconcile_binds_to_sealed_sources_by_normalized_c_identity_and_reports_a_mismatch():
    original = {("bounds", "k.c:12:13"): True, ("bounds", "k.c:14:9"): True}
    variant = {("bounds", "k.c:12:13"): False, ("bounds", "k.c:14:9"): True}
    sealed = "x;\n// proved: obligation bounds:aaa\nif (!(0 <= i)) __builtin_trap();\n"
    fresh = "x;\n// proved: obligation bounds:bbb\nif (!(0 <= i)) __builtin_trap();\n"
    binding = conjuncts.bind_sites(sealed, fresh, original, variant)
    assert binding.identical and binding.restored == (("bounds", "k.c:12:13"),)
    mismatch = conjuncts.bind_sites(sealed, "x;\n", original, variant)
    assert not mismatch.identical and mismatch.restored is None


def test_site_summary_counts_pairs_and_distinct_sites_over_guard_conjuncts_only():
    rows = [
        {"expected": "guard", "sealed_c_identical": True, "restored_sites": "bounds@k.c:1:1;bounds@k.c:2:2"},
        {"expected": "guard", "sealed_c_identical": True, "restored_sites": "bounds@k.c:2:2"},
        {"expected": "annotation", "sealed_c_identical": True, "restored_sites": "bounds@k.c:9:9"},
        {"expected": "guard", "sealed_c_identical": False, "restored_sites": None},
    ]
    summary = conjuncts.site_summary(rows)
    assert summary["guard_conjuncts"] == 3 and summary["guard_pairs"] == 3 and summary["guard_distinct_sites"] == 2
    assert summary["all_pairs"] == 4 and summary["all_distinct_sites"] == 3
    assert summary["unbound"] == 1 and summary["all_bound"] is False
