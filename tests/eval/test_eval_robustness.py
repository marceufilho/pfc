"""Semantics-preserving variants with stated side conditions and hand-derived examples (roadmap E7, EV-R12)."""

from pathlib import Path

from pfc import ir
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import expressions, robustness

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def program(tmp_path, text):
    path = tmp_path / "kernel.c"
    path.write_text('#include "spec.h"\n' + text, encoding="utf-8")
    return lower(parse_source(path))


def test_rename_is_consistent_fresh_and_invertible():
    original = kernel("sum")
    variant = robustness.rename(original, seed=1)
    names = {p.symbol.name for p in variant.program.functions[0].parameters}
    assert names.isdisjoint({"a", "a_len", "n"}) and len(names) == 3
    array = next(p for p in variant.program.functions[0].parameters if p.length is not None)
    assert array.length.name == f"{array.symbol.name}_len"
    printed = ir.print_c(variant.program)
    assert "long s =" not in printed and "long i =" not in printed and "REQUIRES" in printed
    assert robustness.unrename(printed, variant.mapping) == ir.print_c(original)
    assert variant.kind == "rename" and set(variant.mapping) == {"a", "a_len", "n", "s", "i"}
    assert robustness.rename(original, seed=1).program == variant.program
    # Different seeds permute the fresh names; with four symbols a single pair may coincide, a set of six cannot.
    assert len({tuple(sorted(robustness.rename(original, seed=k).mapping.items())) for k in range(1, 7)}) > 1
    # Locations are untouched, so verdicts and decisions can be compared by (kind, location).
    assert [s.location for s in variant.program.functions[0].body.statements] == [
        s.location for s in original.functions[0].body.statements
    ]


def test_map_inputs_preserves_aliasing_between_arrays():
    shared = [1, 2]
    mapped = robustness.map_inputs({"x_len": 2, "y_len": 2}, {"x": shared, "y": shared}, {"x": "v0", "y": "v1"})
    assert mapped.arrays["v0"] is mapped.arrays["v1"] is shared


def test_rename_maps_inputs_for_the_interpreter():
    variant = robustness.rename(kernel("saxpy"), seed=3)
    inputs = robustness.map_inputs({"alpha": 2, "x_len": 1, "y_len": 1, "n": 1}, {"x": [1], "y": [1]}, variant.mapping)
    inverse = {new: old for old, new in variant.mapping.items()}
    assert set(inputs.scalars) == {k for k, v in inverse.items() if v in ("alpha", "x_len", "y_len", "n")}
    assert set(inputs.arrays) == {k for k, v in inverse.items() if v in ("x", "y")}


def test_reorder_swaps_only_adjacent_independent_call_free_locals(tmp_path):
    original = kernel("sum")  # long s = 0; long i = 0;
    candidates = robustness.reorderable(original.functions[0])
    assert len(candidates) == 1
    variant = robustness.reorder(original, 0)
    statements = variant.program.functions[0].body.statements
    assert [s.symbol.name for s in statements if isinstance(s, ir.Local)] == ["i", "s"]
    dependent = program(tmp_path, "long f(long n)\n{\n    long i = n;\n    long j = i + 1;\n    return j;\n}\n")
    assert robustness.reorderable(dependent.functions[0]) == ()
    with_call = program(
        tmp_path,
        "long g(long x)\n{\n    return x;\n}\n\n"
        "long f(long n)\n{\n    long i = g(n);\n    long j = 2;\n    return i + j;\n}\n",
    )
    assert robustness.reorderable(with_call.functions[1]) == ()
    search = kernel("binary_search")  # long lo = 0; long hi = n - 1;
    assert len(robustness.reorderable(search.functions[0])) == 1
    assert robustness.reorderable(kernel("prefix_sum").functions[0]) == ()


def test_commutative_swaps_exclude_short_circuit_and_non_commutative_operators(tmp_path):
    p = program(
        tmp_path,
        "long f(long a, long b)\n{\n    REQUIRES(b != 0 && a / b == 1);\n    long c = a + b;\n    long d = c - a;\n"
        "    return d * b;\n}\n",
    )
    sites = robustness.commutative_sites(p.functions[0])
    assert [(s.operator, s.location.line) for s in sites] == [("!=", 4), ("==", 4), ("+", 5), ("*", 7)]
    variant = robustness.swap(p, 2)
    local = variant.program.functions[0].body.statements[0]
    assert isinstance(local, ir.Local) and expressions.render(local.initializer) == "(b + a)"
    assert variant.kind == "swap" and "line 5" in variant.detail


def test_ten_variants_per_kernel_are_seeded_and_cover_every_applicable_kind():
    sum_variants = robustness.variants(kernel("sum"), seed=0, count=10)
    assert len(sum_variants) == 10 and [v.identifier for v in sum_variants] == [f"v{k:02d}" for k in range(10)]
    kinds = [v.kind for v in sum_variants]
    assert set(kinds) == {"rename", "reorder", "swap"} and kinds.count("reorder") == 1  # one reorderable pair only
    assert robustness.variants(kernel("sum"), seed=0, count=10) == sum_variants
    assert robustness.variants(kernel("sum"), seed=1, count=10) != sum_variants
    prefix = robustness.variants(kernel("prefix_sum"), seed=0, count=10)
    assert "reorder" not in {v.kind for v in prefix} and len(prefix) == 10


def test_behavior_comparison_runs_both_programs_on_the_same_mapped_inputs():
    original = kernel("sum")
    for variant in robustness.variants(original, seed=0, count=10):
        outcome = robustness.compare_behavior(original, variant, T64, seed=0, cases=30)
        assert outcome.cases == 30 and outcome.divergent == 0, variant.detail


def test_obfuscate_composes_renaming_with_every_commutative_swap():
    original = kernel("dot")
    variant = robustness.obfuscate(original, seed=5)
    assert variant.kind == "obfuscate" and len(variant.swapped) == len(
        robustness.commutative_sites(original.functions[0])
    )
    assert robustness.unrename(ir.print_c(variant.program), variant.mapping) != ir.print_c(original)
    outcome = robustness.compare_behavior(original, variant, T64, seed=0, cases=20)
    assert outcome.divergent == 0


def test_c_comparison_applies_only_the_transformations_normalization():
    original = kernel("sum")
    renamed = robustness.variants(original, seed=0, count=10)[0]
    assert renamed.kind == "rename" and robustness.c_comparable(renamed)
    swapped = next(v for v in robustness.variants(original, seed=0, count=10) if v.kind == "swap")
    assert not robustness.c_comparable(swapped)
