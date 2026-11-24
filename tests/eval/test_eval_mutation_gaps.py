"""Behaviours the first mutation campaign (bench/eval/mutate-20261105) left uncovered (roadmap E9, ADR-0042).

Each test names the surviving mutant it kills. They exercise frozen compiler behaviour through its public
API from the harness side; the harness's own survivors are covered at the end.
"""

# ruff: noqa: E501  (C program texts and their expectation tables are kept one statement per string)

import subprocess
from pathlib import Path

import pytest

from pfc import smt
from pfc.diagnostics import FrontendError
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc.vc import generate
from pfc_eval import interpret, stats

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def target():
    return read_target("clang-19")


def _program(tmp_path, text):
    path = tmp_path / "kernel.c"
    path.write_text('#include "spec.h"\n\n' + text, encoding="utf-8")
    return lower(parse_source(path))


def test_short_circuit_operators_in_executable_code_are_materialized_and_compile(tmp_path, target):
    """emit-02: deleting the temporary of `&&`/`||` crashed emission; no layer executed that path."""
    program = _program(
        tmp_path,
        "long both(long x, long y)\n{\n    long r = 0;\n    if (x > 0 && y > 0) {\n        r = 1;\n    }\n"
        "    if (x < 0 || y < 0) {\n        r = 2;\n    }\n    return r;\n}\n",
    )
    emission = emit(program, target, mode="checked")
    assert "!= 0" in emission.c and emission.c.count("if (") >= 4
    out = tmp_path / "both.c"
    out.write_text(emission.c, encoding="utf-8")
    result = subprocess.run(
        ["clang-19", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", str(out)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_justified_restrict_fact_is_cited_in_the_emitted_c(target):
    """emit-08: the traceability comment of a restrict fact was never asserted by any test."""
    program = lower(parse_source(ROOT / "bench/external/acsl/copy/kernel.c"))
    proof = smt.check(program, target, timeout_ms=10000)
    emission = emit(program, target, mode="verified", proof=proof, facts=frozenset({"delete", "restrict"}))
    assert proof.required_passed
    assert "/* justified restrict: context " in emission.c and "restrict" in emission.c.split("{", 1)[0]


def test_vc_complexity_errors_surface_at_the_first_function(tmp_path):
    """vc-08/vc-09/vc-10: a `<VC>` FrontendError (single-term depth cap) is re-raised, never swallowed, at the
    first function's location; a raw `<VC>` location or the last function's would both be wrong."""
    chain = "x" + " + 1" * 105  # term depth above MAX_VC_TERM_DEPTH, below the parser's nesting limit
    program = _program(
        tmp_path, f"long first(long x)\n{{\n    return x;\n}}\n\nlong deep(long x)\n{{\n    return {chain};\n}}\n"
    )
    with pytest.raises(FrontendError) as error:
        generate(program)
    diagnostic = error.value.diagnostic
    assert diagnostic.rule == "vc-complexity" and "expanded VC" in diagnostic.message
    assert diagnostic.location.file == str(tmp_path / "kernel.c") and diagnostic.location.line == 3


def test_vc_depth_cap_boundary_is_exact(tmp_path):
    """vc-03: the depth cap counts leaves as one level; 97 additions pass and 98 exceed the limit."""
    accepted = _program(tmp_path, "long deep(long x)\n{\n    return " + "x" + " + 1" * 97 + ";\n}\n")
    assert generate(accepted).obligations
    rejected = _program(tmp_path, "long deep(long x)\n{\n    return " + "x" + " + 1" * 98 + ";\n}\n")
    with pytest.raises(FrontendError, match="vc-complexity"):
        generate(rejected)


def test_callee_return_values_carry_their_type_domain(tmp_path, target):
    """wp-04: the type-domain hypothesis on a call result is what proves r / 2 + 1 free of overflow."""
    program = _program(
        tmp_path,
        "long same(long x)\n{\n    return x;\n}\n\nlong half_plus_one(long x)\n{\n    long r = same(x);\n"
        "    long s = r / 2 + 1;\n    return s;\n}\n",
    )
    proof = smt.check(program, target, timeout_ms=10000)
    overflow = [r for r in proof.results if r.obligation.kind == "overflow" and r.obligation.location.line == 11]
    assert overflow and all(r.status == "proved" for r in overflow), [(r.obligation.id, r.status) for r in overflow]


def test_interpreter_int_range_reaches_the_full_lower_bound(tmp_path, target):
    """interpret-01: int-typed intermediate values just above -2^31 are in range, four times lower is not."""
    program = _program(
        tmp_path,
        "long edge(long x)\n{\n    long a = (-32767 - 1) * 32767 * 2;\n    long b = (-32767 - 1) * 32767 * 4;\n"
        "    return a + b + x;\n}\n",
    )
    outcome = interpret.run(program, interpret.Inputs({"x": 0}), target=target)
    assert isinstance(outcome, interpret.Rejection) and outcome.kind == "Overflow" and outcome.location.line == 6
    program = _program(
        tmp_path,
        "long edge(long x)\n{\n    long a = (-32767 - 1) * 32767 * 2;\n    return a + x;\n}\n",
    )
    outcome = interpret.run(program, interpret.Inputs({"x": 0}), target=target)
    assert isinstance(outcome, interpret.Observation) and outcome.result == -2147418112


def test_interpreter_default_entry_is_the_first_function(target):
    """interpret-05: without `function=`, the first function runs (callees are defined first in the subset)."""
    program = lower(parse_source(ROOT / "examples/06_calls/kernel.c"))
    outcome = interpret.run(program, interpret.Inputs({"x": 3}), target=target)
    assert isinstance(outcome, interpret.Observation) and outcome.result == 4  # increment, not increment_twice
    twice = interpret.run(program, interpret.Inputs({"x": 3}), target=target, function="increment_twice")
    assert isinstance(twice, interpret.Observation) and twice.result == 5


def test_wilson_interval_with_a_single_trial():
    """stats-01: one trial is a legal sample; Wilson 1/1 at 95% is (0.2065, 1.0)."""
    interval = stats.wilson(1, 1)
    assert interval.low == pytest.approx(0.2065, abs=5e-4) and interval.high == 1.0
    assert stats.wilson(0, 1).low == 0.0 and stats.wilson(0, 1).high == pytest.approx(0.7935, abs=5e-4)


def test_csv_of_a_single_row_uses_that_row_for_the_header():
    """mutate-harness-20261106 survivor tables-01: `rows[1]` as the header crashes every one-row table."""
    from pfc_eval import tables

    assert tables.to_csv([{"kernel": "sum", "value": "1"}]) == "kernel,value\nsum,1\n"
    assert tables.to_csv([]) == "\n"


# -- survivors of the v5 campaigns (bench/eval/mutate-20261110 and mutate-full-*-20261110; docs/steps/E25) ----


def test_decision_sites_are_numbered_from_one_in_emission_order(target):
    """emit-10 (mutate-20261110): `site=index + 0` shifted every site number; no test read the numbers."""
    program = lower(parse_source(ROOT / "kernels" / "matvec" / "kernel.c"))
    emission = emit(program, target, mode="checked")
    assert [d.site for d in emission.decisions] == [1, 2, 3]
    assert [d.site for d in emission.overflow] == list(range(1, len(emission.overflow) + 1))


def test_an_expression_node_the_emitter_does_not_know_is_rejected_with_its_rule(tmp_path, target):
    """emit-02 (mutate-20261110): the final `raise` of Emitter.expression became `pass` and nothing noticed."""
    from dataclasses import dataclass, replace

    from pfc import ir

    program = _program(tmp_path, "long same(long x)\n{\n    return x;\n}\n")
    function = program.functions[0]
    returned = function.body.statements[-1]

    @dataclass(frozen=True)
    class Unknown:
        location: object
        type: object

    alien = Unknown(returned.value.location, returned.value.type)
    body = replace(function.body, statements=(*function.body.statements[:-1], replace(returned, value=alien)))
    broken = replace(program, functions=(replace(function, body=body),))
    assert isinstance(returned, ir.Return)
    with pytest.raises(FrontendError) as error:
        emit(broken, target, mode="plain")
    assert error.value.diagnostic.rule == "emit-expression"


def test_adjudication_counts_wp_only_goals_as_valid_minus_shared():
    """tables-03 (mutate-20261110): `valid - both_adjudicated` swapped; sealed evidence never has a WP-only cell."""
    from pfc_eval import tables

    cells = [{"function": "f", "kind": "bounds", "both": 1, "pfc_only": 0, "wp_only": 2, "neither": 0}]
    goals = [
        {"tool": "wp", "kind": "property", "kernel": "f", "identifier": f"f_assert_bounds_{i}", "status": "valid"}
        for i in range(3)
    ]
    wp_rows = [{**goal, "budget_s": 60} for goal in goals]
    (row,) = tables.oracle_adjudication(cells, wp_rows)
    # pfc proved 1; WP proves 3 at the largest budget: 1 shared, 0 pfc-only, 2 WP-only (hand count).
    assert (row["both_adjudicated"], row["pfc_only_adjudicated"], row["wp_only_adjudicated"]) == (1, 0, 2)
    assert row["adjudicated"] is False


UNPROVABLE_BOUNDS = {
    # mutant: (contract, index expression, why the access is out of bounds for an admitted input)
    "smt-226": ("0 <= i && i < a_len && i < 1000", "i * 2", "i = a_len - 1 gives 2*a_len - 2 >= a_len for a_len >= 2"),
    "smt-236": ("i == -1 && a_len >= 2", "i / 1", "C division: -1 / 1 = -1"),
    "smt-240": ("i == 1 && a_len >= 2", "i / -1", "C division: 1 / -1 = -1"),
    "lower-115": ("i == 0 && a_len == 9", "010 - 9", "octal 010 is 8, so the index is -1"),
    "lower-121": ("i == 0 && a_len == 20", "0x1F", "hexadecimal 0x1F is 31 >= 20"),
}


@pytest.mark.parametrize("mutant", sorted(UNPROVABLE_BOUNDS))
def test_out_of_bounds_index_arithmetic_is_never_proved(mutant, tmp_path, target):
    """Soundness gaps of mutate-full-extended-20261110: each mutant proved this access and removed its guard."""
    contract, index, _why = UNPROVABLE_BOUNDS[mutant]
    program = _program(
        tmp_path,
        f"long read(const long a[], long a_len, long i)\n{{\n    REQUIRES({contract});\n    return a[{index}];\n}}\n",
    )
    proof = smt.check(program, target)
    bounds = [r for r in proof.results if r.obligation.kind == "bounds"]
    assert [r.status for r in bounds] == ["failed"]
    decisions = emit(program, target, mode="verified", proof=proof).decisions
    assert [d.removed for d in decisions if d.kind == "bounds"] == [False]


# -- emitter v5: entry checks, nested statements, prototypes, unary-minus and ambiguous overflow sites -------


def _syntax_ok(text, tmp_path, name):
    path = tmp_path / f"{name}.c"
    path.write_text(text, encoding="utf-8")
    arguments = ["clang-19", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", str(path)]
    return subprocess.run(arguments, capture_output=True, text=True, timeout=60)


def _three_modes(program, target):
    proof = smt.check(program, target)
    return {
        "plain": emit(program, target, mode="plain"),
        "checked": emit(program, target, mode="checked"),
        "verified": emit(program, target, mode="verified", proof=proof),
    }


DIVISOR_BOUNDS = {
    # earlier conjunct: does it bound d below by 1, so that `n / d` is defined and may be rendered? (hand-derived)
    "1 <= d": True,
    "d >= 1": True,
    "d > 0": True,
    "0 < d": True,
    "1 < d": True,
    "2 <= d": True,
    "d >= 0": False,
    "0 <= d": False,
    "d > -1": False,
    "d != 5": False,
}


@pytest.mark.parametrize("bound", sorted(DIVISOR_BOUNDS))
def test_entry_check_renders_a_division_only_after_a_bound_by_one(bound, tmp_path, target):
    """emit-430/433/434/435/440/443/444/450/452/455–459, 481, 482, 491: no test pinned `_positive_divisor`."""
    program = _program(
        tmp_path,
        f"long quotient(long n, long d)\n{{\n    REQUIRES({bound});\n    REQUIRES(n / d >= -9);\n    return n;\n}}\n",
    )
    verified = _three_modes(program, target)["verified"]
    first, second = verified.requires_checks
    assert bool(first.rendered) is True
    assert bool(second.rendered) is DIVISOR_BOUNDS[bound]
    if DIVISOR_BOUNDS[bound]:
        assert "n / d" in second.rendered and "-9" in second.rendered
    else:
        assert "n / d" not in verified.c.split("long pfc_")[0] and second.reason
    assert _syntax_ok(verified.c, tmp_path, "verified").returncode == 0


LITERAL_CONJUNCTS = {
    "n / 1 >= -9": True,  # literal divisor one is defined
    "n / 2 >= -9": True,
    "n / 0 == 0": False,  # undefined: never rendered
    "(n / 2) / 2 >= -9": False,  # compound dividend is outside the pure-scalar class
    "n >= 0": True,
    "n > -100": True,
    "+n >= -50": True,
    "!flag": True,
}


@pytest.mark.parametrize("conjunct", sorted(LITERAL_CONJUNCTS))
def test_entry_check_rendering_of_literals_and_unary_operators(conjunct, tmp_path, target):
    """emit-475/478/481/482/491/499/504/506/507: rendered text and skipped conjuncts of the pure-scalar class."""
    program = _program(
        tmp_path,
        "#include <stdbool.h>\n\nlong gate(long n, bool flag)\n"
        f"{{\n    REQUIRES({conjunct});\n    if (flag) {{\n        return n;\n    }}\n"
        "    return n;\n}\n",
    )
    verified = _three_modes(program, target)["verified"]
    (check,) = verified.requires_checks
    assert bool(check.rendered) is LITERAL_CONJUNCTS[conjunct]
    assert "None" not in verified.c and (check.rendered or check.reason)
    assert verified.c.count("pfc_entry_") == (2 if LITERAL_CONJUNCTS[conjunct] else 0)
    assert _syntax_ok(verified.c, tmp_path, "verified").returncode == 0


def test_nested_block_and_empty_statement_are_emitted_whole_in_the_three_modes(tmp_path, target):
    """emit-214/218/222: a nested `{ ... }` lost a brace or its whole body (218 compiled and returned a stale value)."""
    program = _program(
        tmp_path,
        "long inner(const long a[], long a_len, long n)\n{\n    REQUIRES(0 <= n && n < a_len);\n    long s = 0;\n"
        "    {\n        long t = a[n];\n        s = t;\n    }\n    ;\n    return s;\n}\n",
    )
    modes = _three_modes(program, target)
    for name, emission in modes.items():
        assert "a[n]" in emission.c and "s = " in emission.c, name
        assert _syntax_ok(emission.c, tmp_path, name).returncode == 0, name
    assert [d.kind for d in modes["checked"].decisions] == ["bounds"]
    assert [d.removed for d in modes["verified"].decisions] == [True]


def test_caller_defined_before_its_callee_compiles_in_the_three_modes(tmp_path, target):
    """emit-345/347/350: forward prototypes of a multi-function unit disappeared and nothing compiled such a unit."""
    program = _program(
        tmp_path,
        "long callee(long x);\n\nlong caller(long x)\n{\n    REQUIRES(0 <= x && x < 100);\n    long r = callee(x);\n"
        "    return r;\n}\n\nlong callee(long x)\n{\n    REQUIRES(0 <= x && x < 100);\n    return x + 1;\n}\n",
    )
    for name, emission in _three_modes(program, target).items():
        assert _syntax_ok(emission.c, tmp_path, name).returncode == 0, name


@pytest.mark.parametrize("operand", ["x", "(x < 3)"])
def test_proved_unary_minus_trap_is_removed_and_cited(operand, tmp_path, target):
    """emit-391/393/394/395: the negation site was never counted as unique, so its proved trap always stayed."""
    program = _program(
        tmp_path,
        f"long negate(long x)\n{{\n    REQUIRES(-100 < x && x < 100);\n    long y = -{operand};\n    return y;\n}}\n",
    )
    verified = _three_modes(program, target)["verified"]
    (site,) = verified.overflow
    assert site.removed is True and len(site.obligation_ids) == 1
    assert (
        "__builtin_sub_overflow" not in verified.c and f"// proved: obligation {site.obligation_ids[0]}" in verified.c
    )


def test_two_subtractions_at_one_coordinate_stay_ambiguous_and_keep_their_traps(tmp_path, target):
    """emit-065: `(x - 1) - 2` puts two `-` nodes at one location; ADR-0023 keeps both traps even when proved."""
    program = _program(
        tmp_path,
        "long twice(long x)\n{\n    REQUIRES(-100 < x && x < 100);\n    long z = (x - 1) - 2;\n    return z;\n}\n",
    )
    verified = _three_modes(program, target)["verified"]
    assert [site.removed for site in verified.overflow] == [False, False]
    assert verified.c.count("__builtin_sub_overflow") == 2


def test_a_proved_overflow_trap_is_removed_even_when_other_arithmetic_is_unproved(tmp_path, target):
    """emit-094: one unproved site made every proved trap stay; per-site removal (ADR-0056) was not pinned."""
    program = _program(
        tmp_path,
        "long mixed(const long a[], long a_len, long i, long k)\n{\n    REQUIRES(0 <= i && i < a_len);\n"
        "    long j = i + k;\n    long m = i - 1;\n    return a[j] + m;\n}\n",
    )
    verified = _three_modes(program, target)["verified"]
    by_operator = {site.condition.split("(")[0]: site.removed for site in verified.overflow[:2]}
    assert by_operator == {"__builtin_add_overflow": False, "__builtin_sub_overflow": True}
    assert [d.removed for d in verified.decisions] == [False]


# -- authorization: the arithmetic-influence closure (mutate-full-compiler-20261110) -------------------------

INFLUENCE_PROGRAMS = {
    # mutant: body whose guard operand is (i + k) - k; the bounds proof holds, the overflow of i + k does not.
    "authorization-023": ("long a[]", "    a[(i + k) - k] = 0;\n    return 0;\n"),
    "authorization-018": ("const long a[]", "    long v = a[(i + k) - k];\n    return v;\n"),
    "authorization-020": ("const long a[]", "    long v = 0;\n    v = a[(i + k) - k];\n    return v;\n"),
}


@pytest.mark.parametrize("mutant", sorted(INFLUENCE_PROGRAMS))
def test_guard_stays_when_arithmetic_feeding_its_operand_is_unproved(mutant, tmp_path, target):
    """authorization-018/020/023: an index inside an initializer, an assignment or a write target left the closure."""
    array, body = INFLUENCE_PROGRAMS[mutant]
    program = _program(
        tmp_path,
        f"long touch({array}, long a_len, long i, long k)\n{{\n    REQUIRES(0 <= i && i < a_len);\n{body}}}\n",
    )
    proof = smt.check(program, target)
    assert [r.status for r in proof.results if r.obligation.kind == "bounds"] == ["proved"]
    assert "failed" in [r.status for r in proof.results if r.obligation.kind == "overflow"]
    (decision,) = emit(program, target, mode="verified", proof=proof).decisions
    assert decision.removed is False and "arithmetic" in decision.reason


def test_unproved_arithmetic_on_a_contract_variable_retains_the_guard(tmp_path, target):
    """authorization-008: contract conditions stopped being roots of the influence closure."""
    program = _program(
        tmp_path,
        "long bump(const long a[], long a_len, long x)\n{\n    REQUIRES(a_len > 0 && x >= 0);\n    x = x + 1;\n"
        "    return a[0];\n}\n",
    )
    proof = smt.check(program, target)
    (decision,) = emit(program, target, mode="verified", proof=proof).decisions
    assert decision.removed is False and "arithmetic" in decision.reason


# -- reference interpreter: operators and domains no test executed (mutate-full-harness-20261110) ------------

SEPARATED_TEXT = (
    "long apart(long a[], long a_len, long b[], long b_len, long n, long m)\n{\n"
    "    REQUIRES(SEPARATED(a, n, b, m));\n    return 0;\n}\n"
)
BOOL_LOCAL = "long sign(long x)\n{\n    bool b = x > 0;\n    if (b) {\n        return 1;\n    }\n    return 0;\n}\n"
INTERPRETER_CASES = [
    # (id, program, scalars, expected result or rejection kind); expectations are C semantics worked by hand.
    ("int-add", "long f(long x)\n{\n    long y = 1000 + 1;\n    return y;\n}\n", {"x": 0}, 1001),
    (
        "int-max",
        "long f(long x)\n{\n    long y = 32767 * 32767 * 2 + 32767 * 4 + 1;\n    return y;\n}\n",
        {"x": 0},
        2147483647,
    ),
    (
        "int-overflow",
        "long f(long x)\n{\n    long y = 32767 * 32767 * 2 + 32767 * 4 + 2;\n    return y;\n}\n",
        {"x": 0},
        "Overflow",
    ),
    ("bool-true", BOOL_LOCAL, {"x": 1}, 1),
    ("bool-false", BOOL_LOCAL, {"x": 0}, 0),
    ("not-zero", "long f(long x)\n{\n    if (!x) {\n        return 1;\n    }\n    return 0;\n}\n", {"x": 0}, 1),
    ("not-one", "long f(long x)\n{\n    if (!x) {\n        return 1;\n    }\n    return 0;\n}\n", {"x": 1}, 0),
    ("not-minus", "long f(long x)\n{\n    if (!x) {\n        return 1;\n    }\n    return 0;\n}\n", {"x": -1}, 0),
    ("not-five", "long f(long x)\n{\n    if (!x) {\n        return 1;\n    }\n    return 0;\n}\n", {"x": 5}, 0),
    ("plus", "long f(long x)\n{\n    return +x;\n}\n", {"x": 5}, 5),
    (
        "gt-equal",
        "long f(long x, long y)\n{\n    if (x > y) {\n        return 1;\n    }\n    return 0;\n}\n",
        {"x": 0, "y": 0},
        0,
    ),
    (
        "ge-equal",
        "long f(long x, long y)\n{\n    if (x >= y) {\n        return 1;\n    }\n    return 0;\n}\n",
        {"x": 1, "y": 1},
        1,
    ),
    ("div-neg-one", "long f(long x, long y)\n{\n    REQUIRES(y != 0);\n    return x / y;\n}\n", {"x": -1, "y": 1}, -1),
    ("div-neg-neg", "long f(long x, long y)\n{\n    REQUIRES(y != 0);\n    return x / y;\n}\n", {"x": -1, "y": -1}, 1),
] + [
    (
        f"or-{x}-{y}",
        "long f(long x, long y)\n{\n    if (x || y) {\n        return 1;\n    }\n    return 0;\n}\n",
        {"x": x, "y": y},
        expected,
    )
    for x, y, expected in [(0, 0, 0), (1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1)]
]


@pytest.mark.parametrize("case", INTERPRETER_CASES, ids=[c[0] for c in INTERPRETER_CASES])
def test_interpreter_operators_and_integer_domains(case, tmp_path, target):
    """interpret-031..047, 102..108, 127..134, 146, 149, 169: wrong `!`, `||`, `>`, `/`, int and _Bool ranges."""
    _, text, scalars, expected = case
    program = _program(tmp_path, "#include <stdbool.h>\n\n" + text)
    outcome = interpret.run(program, interpret.Inputs(scalars=scalars), target=target)
    if isinstance(expected, int):
        assert isinstance(outcome, interpret.Observation) and outcome.result == expected
    else:
        assert isinstance(outcome, interpret.Rejection) and outcome.kind == expected


@pytest.mark.parametrize(
    ("n", "m", "aliased", "accepted"),
    [
        (-1, 1, False, False),  # a negative length is not a region
        (1, -1, False, False),
        (-1, -1, False, False),
        (1, 1, False, True),  # two distinct buffers
        (1, 1, True, False),  # one buffer under two names overlaps itself
        (0, 1, True, True),  # an empty region is separated from anything
        (1, 0, True, True),
    ],
)
def test_interpreter_separated_with_negative_empty_and_aliased_regions(n, m, aliased, accepted, tmp_path, target):
    """interpret-205..214, 223..227: SEPARATED on negative lengths, empty regions and one aliased buffer."""
    program = _program(tmp_path, SEPARATED_TEXT)
    first = [1, 2]
    second = first if aliased else [3, 4]
    inputs = interpret.Inputs(scalars={"a_len": 2, "b_len": 2, "n": n, "m": m}, arrays={"a": first, "b": second})
    outcome = interpret.run(program, inputs, target=target)
    if accepted:
        assert isinstance(outcome, interpret.Observation)
    else:
        assert isinstance(outcome, interpret.Rejection) and outcome.kind == "PreconditionViolated"


def test_exact_bounds_at_the_edges_of_the_binomial():
    """stats-078, stats-153: all failures gives bound 1; one success in twenty has a positive exact lower limit."""
    assert stats.binomial_upper_bound(5, 5) == 1.0
    # Clopper–Pearson lower limit for x = 1: 1 - (1 - alpha/2)^(1/n) = 1 - 0.975^(1/20) = 0.0012651.
    assert stats.clopper_pearson(1, 20).low == pytest.approx(0.0012651, abs=1e-6)


# -- VC generator: constructs no kernel or oracle program used (mutate-full-compiler-20261110, wp-*) ---------

MIXED = "long a[], long a_len, bool b[], long b_len"
SETZ = "long setz(long a[], long a_len)\n{\n    REQUIRES(a_len > 0);\n    ENSURES(a[0] == 0);\n    a[0] = 0;\n    return 0;\n}\n\n"
WP_PROGRAMS = {
    # name: (program, {obligation kind: statuses in source order}); expectations worked by hand from C semantics.
    "not-as-value": (
        "long f(long x)\n{\n    long k = !x;\n    ASSERT(x != 0 || k == 1);\n    ASSERT(x == 0 || k == 0);\n    return k;\n}\n",
        {"assert": ["proved", "proved"]},
    ),
    "comparison-as-long": (
        "long f(const long a[], long a_len, long x, long y)\n{\n    REQUIRES(a_len == 3);\n    long k = (x < y) + 0;\n"
        "    long r = 0;\n    ASSERT(k == 0 || k == 1);\n    if (k == 1) {\n        r = a[5];\n    }\n    return r;\n}\n",
        {"assert": ["proved"], "bounds": ["failed"]},
    ),
    "comparison-as-bool-guards-the-read": (
        "long f(const long a[], long a_len, long i)\n{\n    bool ok = 0 <= i && i < a_len;\n    long r = 0;\n"
        "    if (ok) {\n        r = a[i];\n    }\n    return r;\n}\n",
        {"bounds": ["proved"]},
    ),
    "comparison-as-bool-else-branch": (
        "long f(const long a[], long a_len, long x, long y)\n{\n    REQUIRES(a_len == 3);\n    bool ok = x < y;\n"
        "    long r = 0;\n    if (ok) {\n        r = 0;\n    } else {\n        r = a[5];\n    }\n    return r;\n}\n",
        {"bounds": ["failed"]},
    ),
    "comparison-as-bool-then-branch": (
        "long f(const long a[], long a_len, long x, long y)\n{\n    REQUIRES(a_len == 3);\n    bool ok = x < y;\n"
        "    long r = 0;\n    if (ok) {\n        r = a[5];\n    }\n    return r;\n}\n",
        {"bounds": ["failed"]},
    ),
    "mixed-type-separated-means-non-negative-lengths": (
        f"long f({MIXED})\n{{\n    REQUIRES(SEPARATED(a, a_len, b, b_len));\n    ASSERT(a_len >= 0);\n"
        "    ASSERT(b_len >= 0);\n    ASSERT(a_len >= 1);\n    ASSERT(b_len >= 1);\n    return 0;\n}\n",
        {"assert": ["proved", "proved", "failed", "failed"]},
    ),
    "writing-callee-postcondition-holds-after-the-call": (
        SETZ
        + "long f(long a[], long a_len)\n{\n    REQUIRES(a_len > 0);\n    a[0] = 1;\n    long r = setz(a, a_len);\n"
        "    ASSERT(a[0] == 0);\n    ASSERT(a[0] == 1);\n    return r;\n}\n",
        {"assert": ["proved", "failed"]},
    ),
    "transitive-callee-writes-are-havocked": (
        SETZ
        + "long wrap(long a[], long a_len)\n{\n    REQUIRES(a_len > 0);\n    long r = setz(a, a_len);\n    return r;\n}\n\n"
        "long f(long a[], long a_len)\n{\n    REQUIRES(a_len > 0);\n    a[0] = 1;\n    long r = wrap(a, a_len);\n"
        "    ASSERT(a[0] == 1);\n    return r;\n}\n",
        {"assert": ["failed"]},
    ),
    "parameter-assigned-in-a-loop-is-havocked": (
        "long f(long n)\n{\n    REQUIRES(n == 5);\n    while (n > 0) {\n        INVARIANT(n >= 0);\n        n = n - 1;\n    }\n"
        "    ASSERT(n == 0);\n    ASSERT(n == 5);\n    return n;\n}\n",
        {"assert": ["proved", "failed"]},
    ),
    "invariant-division-is-checked-after-the-body": (
        "long f(long n)\n{\n    REQUIRES(n > 0);\n    long i = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i && i <= n && 100 / (n - i) >= 0);\n        i = i + 1;\n    }\n    return i;\n}\n",
        {"division": ["failed", "proved"]},  # same site: entry proved, after the body failed (sorted by status)
    ),
    "nested-block-and-empty-statement": (
        "long f(long x)\n{\n    long r = 0;\n    {\n        r = x;\n    }\n    ;\n    ASSERT(r == x);\n    return r;\n}\n",
        {"assert": ["proved"]},
    ),
    "callee-defined-after-its-caller": (
        "long g(long n);\n\nlong f(long k)\n{\n    long r = g(k);\n    ASSERT(r == 1);\n    return r;\n}\n\n"
        "long g(long n)\n{\n    ENSURES(RESULT == 1);\n    return 1;\n}\n",
        {"assert": ["proved"]},
    ),
}


@pytest.mark.parametrize("name", sorted(WP_PROGRAMS))
def test_vc_generator_verdicts_on_constructs_outside_the_corpus(name, tmp_path, target):
    """wp-026..040, 048..055, 239, 260, 303, 344, 360, 383: each mutant changed one of these verdicts unseen."""
    text, expected = WP_PROGRAMS[name]
    proof = smt.check(_program(tmp_path, "#include <stdbool.h>\n\n" + text), target)
    for kind, statuses in expected.items():
        observed = sorted(
            (r.obligation.location.line, r.obligation.location.column, r.status)
            for r in proof.results
            if r.obligation.kind == kind
        )
        assert [status for _, _, status in observed] == statuses, (kind, observed)


CALL_LENGTHS = {"0": True, "-1": False}  # an empty region is a valid argument; a negative length is not


@pytest.mark.parametrize("length", sorted(CALL_LENGTHS))
def test_array_argument_length_must_lie_between_zero_and_the_capacity(length, tmp_path, target):
    """wp-225/226/227: the capacity call-precondition `0 <= length <= capacity` was shifted by one unseen."""
    program = _program(
        tmp_path,
        "long g(const long a[], long a_len)\n{\n    return 0;\n}\n\n"
        f"long f(const long a[], long a_len)\n{{\n    REQUIRES(a_len >= 0);\n    long r = g(a, {length});\n    return r;\n}}\n",
    )
    assert smt.check(program, target).required_passed is CALL_LENGTHS[length]


def test_modified_callee_parameter_does_not_leak_into_the_caller(tmp_path, target):
    """wp-244: the callee's assigned parameter was not havocked, so ENSURES(n == 0) was read as a fact about k."""
    program = _program(
        tmp_path,
        "long dec(long n, long m)\n{\n    REQUIRES(n >= 0);\n    ENSURES(RESULT == m);\n    while (n > 0) {\n"
        "        INVARIANT(n >= 0);\n        n = n - 1;\n    }\n    return m;\n}\n\n"
        "long f(long k)\n{\n    REQUIRES(k >= 0);\n    long r = dec(k, 7);\n    ASSERT(r == 7);\n    ASSERT(k == 0);\n"
        "    return r;\n}\n",
    )
    proof = smt.check(program, target)
    asserts = [r.status for r in proof.results if r.obligation.kind == "assert"]
    assert asserts == ["proved", "failed"]


def test_mixed_type_separated_call_precondition_fails_for_possibly_negative_lengths(tmp_path, target):
    """wp-050/053/057/058: SEPARATED over long and bool arrays was always true at the call site."""
    program = _program(
        tmp_path,
        f"#include <stdbool.h>\n\nlong g({MIXED})\n{{\n    REQUIRES(SEPARATED(a, a_len, b, b_len));\n    return 0;\n}}\n\n"
        f"long f({MIXED})\n{{\n    REQUIRES(a_len >= -1 && b_len >= -1);\n    long r = g(a, a_len, b, b_len);\n"
        "    return r;\n}\n",
    )
    proof = smt.check(program, target)
    assert proof.required_passed is False
    # Three call preconditions, all false for some admitted input: both capacities (length -1) and SEPARATED.
    assert [r.status for r in proof.results if r.obligation.kind == "call-precondition"] == ["failed"] * 3


def test_adjudication_with_two_prover_campaigns_does_not_add_their_proofs():
    """E26: two adjudication campaigns listed together doubled the WP count (matvec showed 2 WP-only goals)."""
    from pfc_eval import tables

    cells = [{"function": "f", "kind": "bounds", "both": 2, "pfc_only": 1, "wp_only": 0, "neither": 0}]

    def goals(campaign, statuses):
        return [
            {
                "tool": "wp",
                "kind": "property",
                "kernel": "f",
                "identifier": f"f_assert_bounds_{i}",
                "status": status,
                "budget_s": 60,
                "evidence_dir": campaign,
            }
            for i, status in enumerate(statuses)
        ]

    rows = goals("first", ["valid", "valid", "timeout"]) + goals("second", ["valid", "valid", "valid"])
    (row,) = tables.oracle_adjudication(cells, rows)
    # pfc proves 3; the better campaign proves 3 of the same 3 goals: all shared, nothing WP-only.
    assert (row["both_adjudicated"], row["pfc_only_adjudicated"], row["wp_only_adjudicated"]) == (3, 0, 0)
    assert row["adjudicated"] is True
