"""Small-domain sampler: equality conjuncts of REQUIRES are satisfied by construction (writing phase W1, ADR-0047)."""

from pathlib import Path
from random import Random

from pfc.lower import lower
from pfc.parse import parse_source
from pfc_eval import fuzz

ROOT = Path(__file__).resolve().parents[2]


def program(tmp_path, text):
    path = tmp_path / "kernel.c"
    path.write_text('#include "spec.h"\n' + text, encoding="utf-8")
    return lower(parse_source(path))


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def test_parameter_equalities_in_requires_are_assigned_after_sampling(tmp_path):
    # Rejection sampling over [-4, 8] satisfies `i == 0 && k == n && j == 1` in about one draw in 2,000; the
    # composed Zilu programs have up to five such conjuncts. The sampler assigns them instead.
    p = program(
        tmp_path,
        "long f(long i, long j, long k, long n)\n{\n    REQUIRES(i == 0 && k == n && n >= 0 && 1 == j);\n"
        "    while (i < n) {\n        INVARIANT(i <= n);\n        i = i + 1;\n    }\n    return i + j + k;\n}\n",
    )
    rng = Random(0)
    draws = [fuzz.draw_inputs(p.functions[0], rng) for _ in range(50)]
    assert all(d.scalars["i"] == 0 and d.scalars["j"] == 1 and d.scalars["k"] == d.scalars["n"] for d in draws)
    assert len({d.scalars["n"] for d in draws}) > 1  # the free parameter still varies
    assert fuzz.equalities(p.functions[0]) == (("i", 0), ("k", "n"), ("j", 1))


def test_lengths_and_inequalities_are_left_to_rejection_sampling(tmp_path):
    p = program(
        tmp_path,
        "long f(const long a[], long a_len, long n, long m)\n{\n    REQUIRES(n == a_len && 0 <= m && m < n);\n"
        "    return n;\n}\n",
    )
    rng = Random(1)
    draws = [fuzz.draw_inputs(p.functions[0], rng) for _ in range(50)]
    # `n == a_len` is honoured through the length binding (n takes the array's length); `m < n` is not assigned.
    assert all(d.scalars["n"] == len(d.arrays["a"]) == d.scalars["a_len"] for d in draws)
    assert any(not (0 <= d.scalars["m"] < d.scalars["n"]) for d in draws)
    assert fuzz.equalities(p.functions[0]) == (("n", "a_len"),)


def test_development_kernels_have_no_equalities_so_their_draws_are_unchanged():
    for name in ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search"):
        assert fuzz.equalities(kernel(name).functions[0]) == ()
    assert "equalities" in fuzz.domain()
