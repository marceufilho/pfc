"""B2 baseline: dynamic inference of candidate invariants from interpreter traces, then Houdini (plan phase 4)."""

from pathlib import Path

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import daikon, generate, houdini

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def program(tmp_path, text):
    path = tmp_path / "kernel.c"
    path.write_text('#include "spec.h"\n' + text, encoding="utf-8")
    return lower(parse_source(path))


def test_only_completed_executions_contribute_states(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(0 <= n);\n    long i = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i && i <= n);\n        i = i + 1;\n    }\n    return i;\n}\n",
    )
    states = daikon.collect(p, T64, seed=0, samples=60)
    assert states.runs == 60 and 0 < states.accepted < 60  # fuzz draws n in [-4, 8]; negatives are rejected
    heads = states.per_loop[0]
    assert heads and all(set(h) == {"n", "i"} and 0 <= h["i"] <= h["n"] for h in heads)
    assert states.sources == {"fuzz": 60, "profile": 0}


def test_candidates_hold_on_every_observed_state_are_deduplicated_and_capped_in_fixed_order():
    states = [{"i": 2 * k, "k": k, "n": 7} for k in range(25)]
    inference = daikon.infer(["i", "k", "n"], states)
    texts = inference.candidates
    assert "(i == (2 * k))" in texts and "(n == 7)" in texts and "(0 <= i)" in texts and "(0 <= k)" in texts
    assert "(k <= i)" in texts and "(i <= (k + 24))" not in texts  # |24| exceeds the literal bound
    assert "(i == k)" not in texts and "(i <= n)" not in texts  # falsified by the states
    assert len(set(texts)) == len(texts) and inference.truncated == 0
    assert texts == daikon.infer(["i", "k", "n"], states).candidates  # deterministic
    capped = daikon.infer(["i", "k", "n"], states, cap=5)
    assert len(capped.candidates) == 5 and capped.truncated == len(texts) - 5 and capped.candidates == texts[:5]


def test_constants_come_from_observed_extremes_within_the_bound():
    states = [{"i": i, "n": 5, "big": 100 + i} for i in range(0, 4)] * 6
    texts = daikon.infer(["i", "n", "big"], states).candidates
    assert "(0 <= i)" in texts and "(i <= 3)" in texts and "(i <= (n - 2))" in texts and "(n == 5)" in texts
    assert not any("100" in t or "big <= 1" in t for t in texts)  # |c| > 8 is never a literal
    assert "(big == (i + 100))" not in texts


def test_three_variable_and_implication_candidates_are_enumerated(tmp_path):
    states = [{"s": i + j, "p": i * j, "d": i - j, "i": i, "j": j} for i in range(5) for j in range(5)]
    texts = daikon.infer(["s", "p", "d", "i", "j"], states).candidates
    assert "(s == (i + j))" in texts and "(p == (i * j))" in texts and "(d == (i - j))" in texts
    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(0 <= n && n <= 20);\n    long i = 0;\n    long k = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i && i <= n && 0 <= k);\n        if (i < 3) {\n            k = k + 1;\n        }\n"
        "        i = i + 1;\n    }\n    return k;\n}\n",
    )
    conditions = daikon.loop_conditions(p.functions[0], 0)
    assert [daikon.render(c) for c in conditions] == ["(i < 3)"]
    assert daikon.evaluate(conditions[0], {"i": 2, "n": 9, "k": 0}) == 1
    heads = daikon.collect(p, T64, seed=1, samples=120).per_loop[0]
    texts = daikon.infer(["n", "i", "k"], heads, conditions).candidates
    assert "((!(i < 3)) || (k == i))" in texts and "((i < 3) || (k == 3))" in texts


def test_too_few_states_yields_true_and_is_recorded(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(n == 1);\n    long i = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i);\n        i = i + 1;\n    }\n    return i;\n}\n",
    )
    family = daikon.family(p, T64, seed=0, samples=3)
    assert family.states_observed == {0: 6} and family.candidates == {0: []} and family.truncated == {0: 0}
    assert houdini._conjoin(family.candidates[0]) == "1"


def test_profile_draws_are_added_only_when_the_profile_binds_to_the_program():
    profile = generate.committed_profile("sum")
    bound = daikon.collect(kernel("sum"), T64, seed=0, samples=10, profile=profile, profile_samples=15)
    assert bound.sources == {"fuzz": 10, "profile": 15} and bound.runs == 25
    unbound = daikon.collect(kernel("dot"), T64, seed=0, samples=10, profile=profile, profile_samples=15)
    assert unbound.sources == {"fuzz": 10, "profile": 0} and unbound.runs == 10
