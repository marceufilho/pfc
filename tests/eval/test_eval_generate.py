"""REQUIRES-driven rejection sampling with versioned, hashed profiles (roadmap E3, architecture D2)."""

import json
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import generate, interpret

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")
PORTS = ("accumulate", "copy", "count", "fill", "find", "replace", "reverse", "swap_ranges")


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def small_sum_profile(**overrides):
    base = dict(
        kernel="sum",
        function="sum",
        program_sha256=generate.program_digest(kernel("sum")),
        cases=12,
        arrays={"a": generate.ArraySpec(1, 16, -100, 100)},
        scalars={"n": generate.ScalarSpec("prefix", of=("a",))},
        boundary=(
            generate.Boundary({"a": ()}, {"n": 0}),
            generate.Boundary({"a": (5, 6, 7)}, {"n": 3}),
            generate.Boundary({"a": (5, 6, 7)}, {"n": 2}),
        ),
        stress=0.0,
    )
    base.update(overrides)
    return generate.Profile(**base)


@pytest.mark.parametrize("name", KERNELS + PORTS)
def test_committed_profiles_bind_to_their_programs(name):
    path = ROOT / "pfc_eval" / "profiles" / f"{name}.json"
    profile = generate.load_profile(path)
    assert generate.PROFILE_DIGESTS[name] == generate.file_digest(path)
    source = ROOT / ("kernels" if name in KERNELS else "bench/external/acsl") / name / "kernel.c"
    program = lower(parse_source(source))
    generate.bind(profile, program)  # raises on any mismatch
    assert profile.cases == 1000 and len(profile.boundary) == 10
    assert all(spec.min_length == 1 and spec.max_length == 65536 for spec in profile.arrays.values())


def test_changed_profile_or_wrong_program_is_detected(tmp_path):
    path = ROOT / "pfc_eval" / "profiles" / "sum.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cases"] = 999
    altered = tmp_path / "sum.json"
    altered.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        generate.committed_profile("sum", altered)
    profile = generate.load_profile(altered)
    with pytest.raises(ValueError, match="program"):
        generate.bind(profile, kernel("dot"))
    generate.committed_profile("sum", path)


def test_generation_is_seeded_and_boundary_cases_come_first():
    profile = small_sum_profile()
    program = kernel("sum")
    first = generate.generate(profile, program, T64, seed=3)
    again = generate.generate(profile, program, T64, seed=3)
    other = generate.generate(profile, program, T64, seed=4)
    assert first == again and first.cases != other.cases
    assert len(first.cases) == 12 and first.drawn >= 9
    assert [c.boundary for c in first.cases] == [True] * 3 + [False] * 9
    assert [c.identifier for c in first.cases[:4]] == ["0000", "0001", "0002", "0003"]
    assert first.cases[1].expected == interpret.Observation(18, {"a": (5, 6, 7)}, first.cases[1].expected.steps, ())
    for case in first.cases[3:]:
        a = case.inputs.arrays["a"]
        assert 1 <= len(a) <= 16 and all(-100 <= v <= 100 for v in a)
        assert case.inputs.scalars["a_len"] == len(a) and 0 <= case.inputs.scalars["n"] <= len(a)
        assert isinstance(case.expected, interpret.Observation)


def test_rejections_are_counted_by_reason():
    profile = small_sum_profile(scalars={"n": generate.ScalarSpec("range", low=0, high=40)})
    generated = generate.generate(profile, kernel("sum"), T64, seed=0)
    assert len(generated.cases) == 12
    assert generated.rejections.get("PreconditionViolated", 0) > 0
    assert generated.drawn == 3 + 9 + sum(generated.rejections.values())


def test_stress_stratum_exercises_rejection_on_real_shaped_profiles():
    profile = small_sum_profile(stress=0.5)
    generated = generate.generate(profile, kernel("sum"), T64, seed=0)
    assert len(generated.cases) == 12
    assert generated.rejections.get("PreconditionViolated", 0) > 0
    assert generate.generate(small_sum_profile(stress=0.0), kernel("sum"), T64, seed=0).rejections == {}
    with pytest.raises(ValueError, match="stress"):
        generate.Profile(**{**small_sum_profile().__dict__, "stress": 1.5})


def test_committed_profiles_carry_the_stress_stratum():
    for name in KERNELS + PORTS:
        profile = generate.load_profile(ROOT / "pfc_eval" / "profiles" / f"{name}.json")
        assert profile.stress == 0.1


def test_overflowing_values_are_rejected_not_counted():
    profile = small_sum_profile(arrays={"a": generate.ArraySpec(2, 4, 2**62, 2**62 + 5)})
    generated = generate.generate(profile, kernel("sum"), T64, seed=0)
    assert generated.rejections.get("Overflow", 0) > 0
    assert all(isinstance(c.expected, interpret.Observation) for c in generated.cases)


def test_sorted_arrays_and_element_or_range_needles():
    search = kernel("binary_search")
    profile = generate.Profile(
        kernel="binary_search",
        function="binary_search",
        program_sha256=generate.program_digest(search),
        cases=20,
        arrays={"a": generate.ArraySpec(1, 32, -50, 50, sorted=True)},
        scalars={
            "n": generate.ScalarSpec("prefix", of=("a",)),
            "needle": generate.ScalarSpec("element_or_range", of=("a",), low=-60, high=60),
        },
        boundary=(generate.Boundary({"a": ()}, {"n": 0, "needle": 1}),),
        stress=0.0,
    )
    generated = generate.generate(profile, search, T64, seed=9)
    assert len(generated.cases) == 20
    for case in generated.cases[1:]:
        a = case.inputs.arrays["a"]
        assert list(a) == sorted(a) and -60 <= case.inputs.scalars["needle"] <= 60
    assert any(c.inputs.scalars["needle"] in c.inputs.arrays["a"] for c in generated.cases[1:])


def test_quotient_scalars_respect_matrix_capacity():
    matvec = kernel("matvec")
    profile = generate.Profile(
        kernel="matvec",
        function="matvec",
        program_sha256=generate.program_digest(matvec),
        cases=15,
        arrays={
            "matrix": generate.ArraySpec(1, 64, -9, 9),
            "x": generate.ArraySpec(1, 8, -9, 9),
            "y": generate.ArraySpec(1, 8, -9, 9),
        },
        scalars={
            "cols": generate.ScalarSpec("prefix", of=("x",), low=1),
            "rows": generate.ScalarSpec("quotient", of=("matrix",), by="cols", cap=("y",)),
        },
        boundary=(generate.Boundary({"matrix": (), "x": (1,), "y": ()}, {"rows": 0, "cols": 1}),),
        stress=0.0,
    )
    generated = generate.generate(profile, matvec, T64, seed=2)
    assert len(generated.cases) == 15 and generated.rejections == {}
    for case in generated.cases[1:]:
        s, a = case.inputs.scalars, case.inputs.arrays
        assert 1 <= s["cols"] <= len(a["x"]) and 0 <= s["rows"] <= min(len(a["matrix"]) // s["cols"], len(a["y"]))


def test_boundary_case_rejected_by_the_interpreter_is_a_profile_error():
    profile = small_sum_profile(boundary=(generate.Boundary({"a": (1,)}, {"n": 2}),))
    with pytest.raises(generate.ProfileError, match="boundary"):
        generate.generate(profile, kernel("sum"), T64, seed=0)


def test_hopeless_profile_stops_after_the_draw_budget():
    profile = small_sum_profile(scalars={"n": generate.ScalarSpec("range", low=100, high=200)}, boundary=())
    with pytest.raises(generate.ProfileError, match="draw budget"):
        generate.generate(profile, kernel("sum"), T64, seed=0)


def test_profile_round_trips_through_json(tmp_path):
    profile = small_sum_profile()
    path = tmp_path / "sum.json"
    path.write_text(generate.dump_profile(profile), encoding="utf-8")
    assert generate.load_profile(path) == profile


def _rename_scalar(data, old, new):
    data["scalars"][new] = data["scalars"].pop(old)
    for case in data["boundary"]:
        case["scalars"][new] = case["scalars"].pop(old)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda d: d.update(profile="other"), "profile version"),
        (lambda d: d["arrays"]["a"].update(length=[0, 4]), "length"),
        (lambda d: _rename_scalar(d, "n", "m"), "parameter"),
        (lambda d: d["scalars"]["n"].update(kind="weird"), "kind"),
        (lambda d: d["boundary"].append({"arrays": {"a": []}, "scalars": {}}), "boundary"),
        (lambda d: d.update(stress=2), "stress"),
    ],
)
def test_malformed_profiles_are_rejected(tmp_path, mutation, message):
    data = json.loads(generate.dump_profile(small_sum_profile()))
    mutation(data)
    path = tmp_path / "p.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        profile = generate.load_profile(path)
        generate.bind(profile, kernel("sum"))


def test_quotient_over_several_arrays_is_bounded_by_the_smallest_capacity():
    rng = __import__("random").Random(1)
    spec = generate.ScalarSpec("quotient", of=("A", "B"), by="n", cap=())
    arrays = {"A": tuple(range(12)), "B": tuple(range(7))}
    values = {generate._draw_scalar(spec, arrays, {"n": 3}, rng, stress=False) for _ in range(200)}
    assert values == {0, 1, 2}  # min(12 // 3, 7 // 3) = 2


def test_parse_profile_accepts_in_memory_data_like_load_profile(tmp_path):
    data = json.loads(generate.dump_profile(small_sum_profile()))
    parsed = generate.parse_profile(data)
    path = tmp_path / "p.json"
    path.write_text(json.dumps(data))
    assert parsed == generate.load_profile(path)
    data["scalars"]["n"] = {"kind": "quotient", "of": ["a", "a"], "by": "n"}
    with pytest.raises(ValueError, match="quotient"):
        generate.parse_profile(data)
