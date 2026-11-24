"""Reference interpreter: hand-derived expectations per statement kind and rejection (roadmap E2)."""

from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import interpret

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)
T32 = Target("clang", "i686-pc-linux-gnu", 32, 32)
LONG_MAX = 2**63 - 1
LONG_MIN = -(2**63)


def program(tmp_path, text):
    path = tmp_path / "kernel.c"
    path.write_text('#include "spec.h"\n' + text, encoding="utf-8")
    return lower(parse_source(path))


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def run(p, scalars, arrays=None, *, target=T64, function=None, fuel=interpret.DEFAULT_FUEL):
    return interpret.run(p, interpret.Inputs(scalars, arrays or {}), target=target, function=function, fuel=fuel)


def test_sum_reads_without_mutating_inputs():
    buffer = [1, 2, 3, 40]
    observation = run(kernel("sum"), {"a_len": 4, "n": 3}, {"a": buffer})
    assert isinstance(observation, interpret.Observation)
    assert observation.result == 6 and observation.arrays == {"a": (1, 2, 3, 40)}
    assert observation.annotation_violations == () and buffer == [1, 2, 3, 40]


def test_prefix_sum_writes_only_the_prefix():
    observation = run(kernel("prefix_sum"), {"a_len": 4, "n": 3}, {"a": [1, 2, 3, 4]})
    assert observation == interpret.Observation(3, {"a": (1, 3, 6, 4)}, observation.steps, ())
    assert run(kernel("prefix_sum"), {"a_len": 0, "n": 0}, {"a": []}).result == 0


def test_for_if_and_calls(tmp_path):
    count = lower(parse_source(ROOT / "bench/external/acsl/count/kernel.c"))
    assert run(count, {"a_len": 5, "n": 5, "v": 7}, {"a": [7, 1, 7, 7, 2]}).result == 3
    calls = lower(parse_source(ROOT / "examples/06_calls/kernel.c"))
    assert run(calls, {"x": 3}, function="increment_twice").result == 5
    assert run(calls, {"x": 3}, function="increment").result == 4


def test_return_inside_a_loop_and_search_semantics():
    search = kernel("binary_search")
    assert run(search, {"a_len": 5, "n": 5, "needle": 8}, {"a": [1, 3, 8, 9, 12]}).result == 2
    assert run(search, {"a_len": 5, "n": 5, "needle": 4}, {"a": [1, 3, 8, 9, 12]}).result == -1
    assert run(search, {"a_len": 3, "n": 0, "needle": 4}, {"a": [1, 3, 8]}).result == -1


def test_precondition_violation_is_a_typed_rejection():
    rejection = run(kernel("sum"), {"a_len": 3, "n": 5}, {"a": [1, 2, 3]})
    assert isinstance(rejection, interpret.Rejection)
    assert rejection.kind == "PreconditionViolated" and rejection.location.line == 6


def test_out_of_bounds_read_and_write(tmp_path):
    p = program(tmp_path, "long first(const long a[], long a_len)\n{\n    return a[0];\n}\n")
    rejection = run(p, {"a_len": 0}, {"a": []})
    assert rejection.kind == "OutOfBounds" and rejection.location.line == 4
    w = program(tmp_path, "long w(long a[], long a_len, long k)\n{\n    a[k] = 1;\n    return 0;\n}\n")
    assert run(w, {"a_len": 2, "k": 2}, {"a": [0, 0]}).kind == "OutOfBounds"
    assert run(w, {"a_len": 2, "k": -1}, {"a": [0, 0]}).kind == "OutOfBounds"
    assert run(w, {"a_len": 2, "k": 1}, {"a": [0, 0]}).arrays == {"a": (0, 1)}


def test_division_truncates_toward_zero_and_rejects_undefined_cases(tmp_path):
    p = program(tmp_path, "long q(long a, long b)\n{\n    return a / b;\n}\n")
    assert [run(p, {"a": a, "b": b}).result for a, b in ((7, 2), (-7, 2), (7, -2), (-7, -2))] == [3, -3, -3, 3]
    assert run(p, {"a": 1, "b": 0}).kind == "DivisionByZero"
    assert run(p, {"a": LONG_MIN, "b": -1}).kind == "Overflow"
    assert run(p, {"a": LONG_MIN, "b": 1}).result == LONG_MIN


def test_overflow_follows_the_target_long_width(tmp_path):
    p = program(tmp_path, "long add(long a, long b)\n{\n    return a + b;\n}\n")
    assert run(p, {"a": LONG_MAX, "b": 1}).kind == "Overflow"
    assert run(p, {"a": 2**31 - 1, "b": 1}).result == 2**31
    assert run(p, {"a": 2**31 - 1, "b": 1}, target=T32).kind == "Overflow"
    assert run(p, {"a": -5, "b": -3}).result == -8


def test_short_circuit_skips_the_unexecuted_operand(tmp_path):
    p = program(
        tmp_path,
        "long f(long b)\n{\n    if (b != 0 && 10 / b > 1) {\n        return 1;\n    }\n    return 0;\n}\n",
    )
    assert run(p, {"b": 0}).result == 0
    assert run(p, {"b": 2}).result == 1
    assert run(p, {"b": 20}).result == 0


def test_annotations_are_mathematical_and_violations_are_recorded_not_executed(tmp_path):
    p = program(tmp_path, "long big(long x)\n{\n    REQUIRES(x * x >= 0);\n    return x;\n}\n")
    assert run(p, {"x": 2**40}).result == 2**40
    q = program(
        tmp_path,
        "long wrong(long x)\n{\n    ENSURES(RESULT == x + 1);\n    long i = 0;\n"
        "    while (i < 2) {\n        INVARIANT(i == 0);\n        ASSERT(i < 1);\n        i = i + 1;\n    }\n"
        "    return x;\n}\n",
    )
    observation = run(q, {"x": 1})
    assert observation.result == 1
    assert observation.annotation_violations == ("INVARIANT at 7:9", "ASSERT at 8:9", "ENSURES at 4:5")


def test_division_by_zero_inside_an_annotation_is_not_silently_totalized(tmp_path):
    p = program(tmp_path, "long g(long x, long y)\n{\n    REQUIRES(x / y == 0);\n    return x;\n}\n")
    assert run(p, {"x": 0, "y": 0}).kind == "AnnotationUndefined"
    assert run(p, {"x": 0, "y": 3}).result == 0


def test_callee_precondition_violation_is_rejected_at_the_call(tmp_path):
    p = program(
        tmp_path,
        "long inc(long x)\n{\n    REQUIRES(x < 5);\n    return x + 1;\n}\n\n"
        "long twice(long x)\n{\n    REQUIRES(x < 100);\n    long y = inc(x);\n    return inc(y);\n}\n",
    )
    assert run(p, {"x": 2}, function="twice").result == 4
    rejection = run(p, {"x": 4}, function="twice")
    assert rejection.kind == "PreconditionViolated" and rejection.location.line == 12


def test_separated_is_object_identity_and_arrays_alias_through_calls(tmp_path):
    saxpy = kernel("saxpy")
    shared = [1, 2]
    rejection = run(saxpy, {"alpha": 2, "x_len": 2, "y_len": 2, "n": 2}, {"x": shared, "y": shared})
    assert rejection.kind == "PreconditionViolated"
    observation = run(saxpy, {"alpha": 2, "x_len": 2, "y_len": 2, "n": 2}, {"x": [1, 2], "y": [10, 20]})
    assert observation.arrays == {"x": (1, 2), "y": (12, 24)} and observation.result == 2
    assert run(saxpy, {"alpha": 2, "x_len": 0, "y_len": 0, "n": 0}, {"x": [], "y": []}).result == 0
    p = program(
        tmp_path,
        "long zero(long a[], long a_len, long n)\n{\n    REQUIRES(0 <= n && n <= a_len);\n"
        "    long i = 0;\n    while (i < n) {\n        a[i] = 0;\n        i = i + 1;\n    }\n    return n;\n}\n\n"
        "long outer(long a[], long a_len, long n)\n{\n    REQUIRES(0 <= n && n <= a_len);\n"
        "    long k = zero(a, a_len, n);\n    return k;\n}\n",
    )
    assert run(p, {"a_len": 3, "n": 2}, {"a": [5, 5, 5]}, function="outer").arrays == {"a": (0, 0, 5)}


def test_fuel_exhaustion_is_inconclusive(tmp_path):
    p = program(
        tmp_path,
        "long loop(long n)\n{\n    long i = 0;\n    while (i < n) {\n        i = i + 1;\n    }\n    return i;\n}\n",
    )
    assert run(p, {"n": 100}, fuel=10).kind == "Inconclusive"
    assert run(p, {"n": 100}).result == 100


@pytest.mark.parametrize(
    "scalars, arrays, message",
    [
        ({"a_len": 3}, {"a": [1, 2, 3]}, "n"),
        ({"a_len": 3, "n": 1, "extra": 0}, {"a": [1, 2, 3]}, "extra"),
        ({"a_len": 4, "n": 1}, {"a": [1, 2, 3]}, "shorter"),
        ({"a_len": 3, "n": 2**63}, {"a": [1, 2, 3]}, "range"),
        ({"a_len": 3, "n": 1}, {"a": [1, 2**63, 3]}, "range"),
        ({"a_len": -1, "n": 0}, {"a": []}, "negative"),
    ],
)
def test_invalid_inputs_are_rejected_before_execution(scalars, arrays, message):
    rejection = run(kernel("sum"), scalars, arrays)
    assert rejection.kind == "InvalidInput" and message in rejection.message


def test_bool_parameters_accept_only_zero_and_one(tmp_path):
    p = program(
        tmp_path, "long pick(_Bool flag, long x)\n{\n    if (flag) {\n        return x;\n    }\n    return 0;\n}\n"
    )
    assert run(p, {"flag": 1, "x": 9}).result == 9
    assert run(p, {"flag": 0, "x": 9}).result == 0
    assert run(p, {"flag": 2, "x": 9}).kind == "InvalidInput"


def test_unknown_function_and_missing_program_are_errors(tmp_path):
    with pytest.raises(ValueError):
        run(kernel("sum"), {"a_len": 0, "n": 0}, {"a": []}, function="nope")


def test_interpreter_does_not_import_compiler_evaluators():
    import ast

    source = (ROOT / "pfc_eval" / "interpret.py").read_text(encoding="utf-8")
    modules = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    forbidden = {"pfc.vc", "pfc.wp", "pfc.smt", "pfc.emit", "pfc.replay", "pfc.authorization"}
    assert not modules & forbidden, modules


def test_a_rejection_keeps_the_annotation_violations_observed_before_it(tmp_path):
    # The invariant is false at the loop head on entry; the loop then runs past the step budget. The head
    # violation was observed before the budget ran out and must survive in the Inconclusive rejection.
    p = program(
        tmp_path,
        "long f(long i, long k)\n{\n    REQUIRES(i == 0);\n    while (i <= k) {\n        INVARIANT(k == 0);\n"
        "        i = i + 1;\n    }\n    return i;\n}\n",
    )
    outcome = run(p, {"i": 0, "k": 10**12}, fuel=1000)
    assert isinstance(outcome, interpret.Rejection) and outcome.kind == "Inconclusive"
    assert outcome.annotation_violations == ("INVARIANT at 6:9",)
    complete = run(p, {"i": 0, "k": 3}, fuel=1000)
    assert isinstance(complete, interpret.Observation) and complete.annotation_violations == ("INVARIANT at 6:9",)


def test_loop_head_observer_sees_every_head_state_by_name_including_the_exit_check(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    REQUIRES(0 <= n);\n    long i = 0;\n    long s = 0;\n    while (i < n) {\n"
        "        INVARIANT(0 <= i && i <= n);\n        s = s + i;\n        i = i + 1;\n    }\n    return s;\n}\n",
    )
    seen = []
    outcome = interpret.run(p, interpret.Inputs({"n": 2}, {}), target=T64, observer=seen.append)
    assert isinstance(outcome, interpret.Observation) and outcome.result == 1
    assert [h.scalars for h in seen] == [{"n": 2, "i": 0, "s": 0}, {"n": 2, "i": 1, "s": 0}, {"n": 2, "i": 2, "s": 1}]
    assert {h.function for h in seen} == {"f"} and {h.location.line for h in seen} == {8}


def test_observer_absent_leaves_results_steps_and_violations_unchanged(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    long i = 0;\n    while (i < n) {\n        INVARIANT(1 <= i);\n"
        "        i = i + 1;\n    }\n    return i;\n}\n",
    )
    plain = interpret.run(p, interpret.Inputs({"n": 3}, {}), target=T64)
    observed = interpret.run(p, interpret.Inputs({"n": 3}, {}), target=T64, observer=lambda head: None)
    assert plain == observed and plain.annotation_violations == ("INVARIANT at 6:9",) and plain.steps == observed.steps


def test_observer_exception_propagates_and_is_not_swallowed(tmp_path):
    p = program(
        tmp_path,
        "long f(long n)\n{\n    long i = 0;\n    while (i < n) {\n        INVARIANT(0 <= i);\n"
        "        i = i + 1;\n    }\n    return i;\n}\n",
    )

    def boom(head):
        raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="observer failed"):
        interpret.run(p, interpret.Inputs({"n": 1}, {}), target=T64, observer=boom)


def test_evaluate_condition_decides_a_requires_conjunct_on_concrete_inputs_and_returns_none_when_undefined(tmp_path):
    from pfc_eval import conjuncts

    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n\nlong f(const long a[], long a_len, long n, long d)\n{\n'
        "    REQUIRES(0 <= n && n <= a_len && n <= a_len / d);\n    return n;\n}\n",
        encoding="utf-8",
    )
    program = lower(parse_source(path))
    parts = conjuncts.conjuncts(program.functions[0])
    inputs = interpret.Inputs({"a_len": 2, "n": 3, "d": 1}, {"a": (1, 2)})
    truth = [interpret.evaluate_condition(program, inputs, c.expression, target=T64) for c in parts]
    assert truth == [True, False, False]
    undefined = interpret.Inputs({"a_len": 2, "n": 1, "d": 0}, {"a": (1, 2)})
    assert interpret.evaluate_condition(program, undefined, parts[2].expression, target=T64) is None
    invalid = interpret.Inputs({"a_len": 5, "n": 1, "d": 1}, {"a": (1, 2)})  # buffer shorter than a_len
    assert interpret.evaluate_condition(program, invalid, parts[0].expression, target=T64) is None


def test_for_without_condition_and_with_an_empty_body_exhausts_the_step_budget(tmp_path):
    """E26: `for (;;) { }` evaluated nothing per iteration, spent no fuel and never returned."""
    import multiprocessing

    from pfc.lower import lower
    from pfc.parse import parse_source
    from pfc.target import Target
    from pfc_eval import interpret

    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n\nlong spin(long n)\n{\n    for (;;) {\n    }\n    return n;\n}\n', encoding="utf-8"
    )
    program = lower(parse_source(path))
    target = Target("clang", "x86_64-pc-linux-gnu", 32, 64)

    def attempt(queue):
        outcome = interpret.run(program, interpret.Inputs({"n": 1}), target=target, fuel=1000)
        queue.put(getattr(outcome, "kind", "observation"))

    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    worker = context.Process(target=attempt, args=(queue,))
    worker.start()
    worker.join(20)
    hung = worker.is_alive()
    if hung:
        worker.kill()
        worker.join()
    assert not hung, "the interpreter did not terminate"
    assert queue.get(timeout=5) == "Inconclusive"


def test_bool_array_elements_outside_zero_and_one_are_invalid_input(tmp_path):
    """E26: `_bind` checked `_Bool a[]` elements against the long range; 2 surfaced later as an Overflow."""
    from pfc.lower import lower
    from pfc.parse import parse_source
    from pfc.target import Target
    from pfc_eval import interpret

    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n#include <stdbool.h>\n\nlong first(const bool a[], long a_len)\n{\n'
        "    REQUIRES(a_len > 0);\n    bool b = a[0];\n    if (b) {\n        return 1;\n    }\n    return 0;\n}\n",
        encoding="utf-8",
    )
    program = lower(parse_source(path))
    target = Target("clang", "x86_64-pc-linux-gnu", 32, 64)
    for element in (2, -1):
        outcome = interpret.run(program, interpret.Inputs({"a_len": 1}, {"a": [element]}), target=target)
        assert isinstance(outcome, interpret.Rejection) and outcome.kind == "InvalidInput", element
    accepted = interpret.run(program, interpret.Inputs({"a_len": 1}, {"a": [1]}), target=target)
    assert isinstance(accepted, interpret.Observation) and accepted.result == 1


def test_fuzz_sampler_draws_only_zero_and_one_for_bool_arrays(tmp_path):
    """E26: with `_Bool` elements range-checked at entry, the small-domain sampler must not draw -4..8 for them."""
    from random import Random

    from pfc.lower import lower
    from pfc.parse import parse_source
    from pfc.target import Target
    from pfc_eval import fuzz, interpret

    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n#include <stdbool.h>\n\n'
        "long pick(const bool flags[], long flags_len, const long a[], long a_len)\n{\n"
        "    return 0;\n}\n",
        encoding="utf-8",
    )
    program = lower(parse_source(path))
    function = program.functions[0]
    target = Target("clang", "x86_64-pc-linux-gnu", 32, 64)
    rng = Random(0)
    seen = set()
    for _ in range(200):
        inputs = fuzz.draw_inputs(function, rng)
        seen.update(inputs.arrays["flags"])
        assert isinstance(interpret.run(program, inputs, target=target), interpret.Observation)
    assert seen == {0, 1}
