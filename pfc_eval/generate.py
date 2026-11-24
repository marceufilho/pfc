"""REQUIRES-driven input generation by rejection sampling (architecture D2, roadmap E3).

A versioned JSON profile per kernel fixes the array length range (log-uniform), per-array value
bounds, how each scalar parameter is drawn and ten explicit boundary cases. Sampling never consults
the contracts directly: a candidate is kept only when the reference interpreter accepts it, so every
accepted case satisfies `REQUIRES` and executes without undefined behavior. Rejections are counted per
reason and reported; they are never silently dropped. Profiles bind to the exact program text by
SHA-256 and the committed profile files are themselves hashed in `PROFILE_DIGESTS`.
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Literal

from pfc import ir
from pfc.digest import sha256_text
from pfc.target import Target
from pfc_eval import interpret

PROFILE_VERSION = "pfc-eval-inputs-v1"
PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
DRAW_BUDGET_FACTOR = 50
ScalarKind = Literal["range", "prefix", "quotient", "element_or_range"]
SCALAR_KINDS: tuple[ScalarKind, ...] = ("range", "prefix", "quotient", "element_or_range")

# SHA-256 of each committed profile file; a changed profile is detected before any sampling.
PROFILE_DIGESTS: dict[str, str] = {
    "accumulate": "1983a3dd84f3bfd43489b7f90591b829c6682d26f699d39f2eb311737af52f43",
    "binary_search": "8dac0ceea103d1e8354d74eef6db1f9353458cfb106f601edac8a462df6b7634",
    "copy": "e93679d52205243f7e6573052c081bf8d676c6924e767fa7103c8228fa44a082",
    "count": "13aff0a61ffe67c5f4e053b3916e0a86397870810563e69d33f6ee42bed1c001",
    "dot": "b8cc8549c3817e1aa7e77c77ae182a8435936fbba19f71a034932d9a5c4fc869",
    "fill": "d2d2851371df09f71cef8b67ec2716b10daee239926f40dbd3bd103534fb0353",
    "find": "1b3f0e92d76a6847a2c61d98e96c8fdf77fed68c9571e53fb411cfeb3324c0e1",
    "matvec": "e1858de0d91bb8444b75dae885b1e9f30cb3c129f5d2b0a13890113ef12db374",
    "prefix_sum": "c493562c1edb7effdc4ff317cbd051af7aeebc037fd4d376127c4a2d4e4b6a05",
    "replace": "30144af6772569f8bd81e948c07de0fc8f7baa92c6aa23b4c8da4e1765b2864b",
    "reverse": "89201ebb7763d2657cd87f1eadc3359893c1067c816c1e6caa94311c65a77cd1",
    "saxpy": "eccf39e44421cc2e9463b2bfde14b4d6cbb94e6c2fe47dc262a6f51c71c61e77",
    "sum": "21d9865b2435fefc6ab2901be53b5329cb7eaab58e4fc61831755425898326db",
    "swap_ranges": "28241131bf4cf4e0c4665217d531590235f0fa67fc8634db1bba79d3d5a26fae",
}


class ProfileError(ValueError):
    """The profile cannot produce a valid campaign for this program."""


@dataclass(frozen=True)
class ArraySpec:
    min_length: int
    max_length: int
    low: int
    high: int
    sorted: bool = False


@dataclass(frozen=True)
class ScalarSpec:
    kind: ScalarKind
    low: int = 0
    high: int = 0
    of: tuple[str, ...] = ()
    by: str | None = None
    cap: tuple[str, ...] = ()


@dataclass(frozen=True)
class Boundary:
    arrays: dict[str, tuple[int, ...]]
    scalars: dict[str, int]


@dataclass(frozen=True)
class Profile:
    kernel: str
    function: str
    program_sha256: str
    cases: int
    arrays: dict[str, ArraySpec]
    scalars: dict[str, ScalarSpec]
    boundary: tuple[Boundary, ...]
    stress: float = 0.0  # Fraction of seeded draws whose derived scalars are perturbed past their natural bounds.

    def __post_init__(self) -> None:
        if type(self.stress) is not float or not 0.0 <= self.stress <= 1.0:
            raise ValueError("invalid profile: stress must be a float in [0, 1]")


@dataclass(frozen=True)
class Case:
    identifier: str
    boundary: bool
    inputs: interpret.Inputs
    expected: interpret.Observation


@dataclass(frozen=True)
class Rejected:
    identifier: str
    total_length: int
    kind: str
    message: str


@dataclass(frozen=True)
class Generated:
    cases: tuple[Case, ...]
    rejected: tuple[Rejected, ...]
    drawn: int
    rejections: dict[str, int] = field(default_factory=dict)


def program_digest(program: ir.Program) -> str:
    return sha256_text(ir.print_c(program))


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"invalid profile: {message}")


def _int(value: object, message: str) -> int:
    _require(type(value) is int, message)
    assert isinstance(value, int)
    return value


def _pair(value: object, message: str) -> tuple[int, int]:
    _require(isinstance(value, list) and len(value) == 2, message)
    assert isinstance(value, list)
    return _int(value[0], message), _int(value[1], message)


def _names(value: object, message: str) -> tuple[str, ...]:
    _require(isinstance(value, list) and all(isinstance(v, str) for v in value), message)
    assert isinstance(value, list)
    return tuple(value)


def load_profile(path: Path) -> Profile:
    """Parse and validate one profile file; parameter names are checked against the program by `bind`."""
    return parse_profile(json.loads(path.read_text(encoding="utf-8")))


def parse_profile(data: object) -> Profile:
    """Validate in-memory profile data (the JSON object of a profile file) into a `Profile`."""
    _require(isinstance(data, dict), "object expected")
    assert isinstance(data, dict)
    _require(data.get("profile") == PROFILE_VERSION, f"profile version must be {PROFILE_VERSION}")
    expected = {"profile", "kernel", "function", "program_sha256", "cases", "arrays", "scalars", "boundary", "stress"}
    _require(set(data) == expected, f"keys must be exactly {sorted(expected)}")
    _require(type(data["stress"]) is float and 0.0 <= data["stress"] <= 1.0, "stress must be a float in [0, 1]")
    _require(all(isinstance(data[k], str) and data[k] for k in ("kernel", "function", "program_sha256")), "names")
    cases = _int(data["cases"], "cases must be an integer")
    _require(cases >= 1, "cases must be positive")
    arrays: dict[str, ArraySpec] = {}
    _require(isinstance(data["arrays"], dict), "arrays must be an object")
    for name, spec in data["arrays"].items():
        _require(isinstance(spec, dict) and set(spec) <= {"length", "values", "sorted"}, f"array {name} spec")
        min_length, max_length = _pair(spec.get("length"), f"array {name} length range")
        _require(1 <= min_length <= max_length, f"array {name} length range must satisfy 1 <= min <= max")
        low, high = _pair(spec.get("values"), f"array {name} value range")
        _require(low <= high, f"array {name} value range")
        is_sorted = spec.get("sorted", False)
        _require(isinstance(is_sorted, bool), f"array {name} sorted flag")
        arrays[name] = ArraySpec(min_length, max_length, low, high, is_sorted)
    scalars: dict[str, ScalarSpec] = {}
    _require(isinstance(data["scalars"], dict), "scalars must be an object")
    for name, spec in data["scalars"].items():
        _require(isinstance(spec, dict) and spec.get("kind") in SCALAR_KINDS, f"scalar {name} kind")
        _require(set(spec) <= {"kind", "low", "high", "of", "by", "cap"}, f"scalar {name} spec")
        kind: ScalarKind = spec["kind"]
        low, high = _int(spec.get("low", 0), f"scalar {name} low"), _int(spec.get("high", 0), f"scalar {name} high")
        of = _names(spec.get("of", []), f"scalar {name} of")
        cap = _names(spec.get("cap", []), f"scalar {name} cap")
        by = spec.get("by")
        _require(by is None or isinstance(by, str), f"scalar {name} by")
        if kind == "range":
            _require(low <= high and not of and by is None and not cap, f"scalar {name} range spec")
        elif kind == "prefix":
            _require(bool(of) and by is None and not cap and high == 0, f"scalar {name} prefix spec")
        elif kind == "quotient":
            _require(
                len(of) >= 1 and len(set(of)) == len(of) and isinstance(by, str) and low == 0 and high == 0,
                f"scalar {name} quotient spec (one or more distinct arrays, a divisor scalar, no range)",
            )
        else:
            _require(len(of) == 1 and low <= high and by is None and not cap, f"scalar {name} element_or_range spec")
        for referenced in (*of, *cap):
            _require(referenced in arrays, f"scalar {name} refers to unknown array {referenced}")
        scalars[name] = ScalarSpec(kind, low, high, of, by, cap)
    boundary = []
    _require(isinstance(data["boundary"], list), "boundary must be a list")
    for index, item in enumerate(data["boundary"]):
        _require(isinstance(item, dict) and set(item) == {"arrays", "scalars"}, f"boundary case {index}")
        _require(set(item["arrays"]) == set(arrays) and set(item["scalars"]) == set(scalars), f"boundary case {index}")
        values = {name: tuple(_int(v, "boundary value") for v in item["arrays"][name]) for name in arrays}
        boundary.append(Boundary(values, {name: _int(item["scalars"][name], "boundary scalar") for name in scalars}))
    return Profile(
        data["kernel"],
        data["function"],
        data["program_sha256"],
        cases,
        arrays,
        scalars,
        tuple(boundary),
        data["stress"],
    )


def dump_profile(profile: Profile) -> str:
    """Serialize preserving the declaration order of arrays and scalars (it is the sampling order)."""
    arrays = {
        name: {"length": [s.min_length, s.max_length], "values": [s.low, s.high], "sorted": s.sorted}
        for name, s in profile.arrays.items()
    }
    scalars: dict[str, dict[str, object]] = {}
    for name, s in profile.scalars.items():
        spec: dict[str, object] = {"kind": s.kind}
        if s.kind in ("range", "element_or_range"):
            spec.update(low=s.low, high=s.high)
        if s.kind == "prefix" and s.low:
            spec["low"] = s.low
        if s.of:
            spec["of"] = list(s.of)
        if s.by is not None:
            spec["by"] = s.by
        if s.cap:
            spec["cap"] = list(s.cap)
        scalars[name] = spec
    data = {
        "profile": PROFILE_VERSION,
        "kernel": profile.kernel,
        "function": profile.function,
        "program_sha256": profile.program_sha256,
        "cases": profile.cases,
        "arrays": arrays,
        "scalars": scalars,
        "boundary": [
            {"arrays": {k: list(v) for k, v in b.arrays.items()}, "scalars": dict(b.scalars)} for b in profile.boundary
        ],
        "stress": profile.stress,
    }
    return json.dumps(data, indent=2) + "\n"


def committed_profile(name: str, path: Path | None = None) -> Profile:
    """Load a reviewed profile only if its bytes match the recorded digest."""
    path = path if path is not None else PROFILES_DIR / f"{name}.json"
    if name not in PROFILE_DIGESTS:
        raise ValueError(f"no committed profile digest for {name!r}")
    if file_digest(path) != PROFILE_DIGESTS[name]:
        raise ValueError(f"profile digest mismatch for {name}: review the change and record the new digest")
    return load_profile(path)


def _function(profile: Profile, program: ir.Program) -> ir.Function:
    for function in program.functions:
        if function.name == profile.function:
            return function
    raise ValueError(f"invalid profile: function {profile.function!r} is not defined by the program")


def bind(profile: Profile, program: ir.Program) -> ir.Function:
    """Check that the profile describes exactly this program and its parameter names."""
    if profile.program_sha256 != program_digest(program):
        raise ValueError("profile is bound to a different program text (program_sha256 mismatch)")
    function = _function(profile, program)
    arrays = {p.symbol.name for p in function.parameters if p.length is not None}
    lengths = {p.length.name for p in function.parameters if p.length is not None}
    scalars = {p.symbol.name for p in function.parameters if p.length is None} - lengths
    if set(profile.arrays) != arrays or set(profile.scalars) != scalars:
        raise ValueError(
            f"profile parameter names differ from the function: arrays {sorted(arrays)}, scalars {sorted(scalars)}"
        )
    for parameter in function.parameters:
        if parameter.length is None and parameter.symbol.name in profile.scalars:
            if parameter.symbol.type is ir.ScalarType.BOOL:
                spec = profile.scalars[parameter.symbol.name]
                if spec.kind != "range" or not 0 <= spec.low <= spec.high <= 1:
                    raise ValueError(f"invalid profile: bool parameter {parameter.symbol.name} needs range 0..1")
    return function


def _log_uniform(random: Random, low: int, high: int) -> int:
    value = int(math.exp(random.uniform(math.log(low), math.log(high + 1))))
    return min(max(value, low), high)


STRESS_MARGIN = 2


def _draw_scalar(
    spec: ScalarSpec, arrays: Mapping[str, Sequence[int]], scalars: Mapping[str, int], rng: Random, *, stress: bool
) -> int:
    """Draw one scalar; under stress, derived bounds are widened by the margin so REQUIRES can fail."""
    margin = STRESS_MARGIN if stress else 0
    if spec.kind == "range":
        return rng.randint(spec.low, spec.high)
    if spec.kind == "prefix":
        high = min(len(arrays[name]) for name in spec.of)
        low = spec.low - margin
        return rng.randint(low, high + margin) if low <= high + margin else low
    if spec.kind == "quotient":
        assert spec.by is not None
        divisor = scalars[spec.by]
        high = min(len(arrays[name]) // divisor for name in spec.of) if divisor else 0
        for name in spec.cap:
            high = min(high, len(arrays[name]))
        return rng.randint(-margin, max(high, 0) + margin)
    values = arrays[spec.of[0]]
    if values and rng.random() < 0.5:
        return rng.choice(list(values))
    return rng.randint(spec.low, spec.high)


def _draw(profile: Profile, rng: Random) -> interpret.Inputs:
    arrays: dict[str, tuple[int, ...]] = {}
    for name, spec in profile.arrays.items():
        length = _log_uniform(rng, spec.min_length, spec.max_length)
        values = [rng.randint(spec.low, spec.high) for _ in range(length)]
        arrays[name] = tuple(sorted(values)) if spec.sorted else tuple(values)
    stress = rng.random() < profile.stress
    scalars: dict[str, int] = {}
    for name, scalar_spec in profile.scalars.items():
        scalars[name] = _draw_scalar(scalar_spec, arrays, scalars, rng, stress=stress)
    return _inputs(arrays, scalars)


def draw(profile: Profile, rng: Random) -> interpret.Inputs:
    """One input from a profile, for callers outside this module (B2 state collection)."""
    return _draw(profile, rng)


def _inputs(arrays: Mapping[str, Sequence[int]], scalars: Mapping[str, int]) -> interpret.Inputs:
    lengths = {f"{name}_len": len(values) for name, values in arrays.items()}
    return interpret.Inputs({**scalars, **lengths}, {name: tuple(values) for name, values in arrays.items()})


def _total_length(inputs: interpret.Inputs) -> int:
    return sum(len(values) for values in inputs.arrays.values())


def generate(
    profile: Profile, program: ir.Program, target: Target, *, seed: int, fuel: int = interpret.DEFAULT_FUEL
) -> Generated:
    """Boundary cases first, then seeded draws until `profile.cases` candidates are accepted."""
    function = bind(profile, program)
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    accepted: list[Case] = []
    rejected: list[Rejected] = []
    counts: dict[str, int] = {}
    drawn = 0
    for index, boundary in enumerate(profile.boundary):
        drawn += 1
        inputs = _inputs(boundary.arrays, boundary.scalars)
        outcome = interpret.run(program, inputs, target=target, function=function.name, fuel=fuel)
        if isinstance(outcome, interpret.Rejection):
            raise ProfileError(f"boundary case {index} rejected: {outcome.kind}: {outcome.message}")
        accepted.append(Case(f"{len(accepted):04d}", True, inputs, outcome))
    rng = Random(seed)
    budget = len(profile.boundary) + DRAW_BUDGET_FACTOR * profile.cases
    while len(accepted) < profile.cases:
        if drawn >= budget:
            raise ProfileError(
                f"draw budget of {budget} exhausted with {len(accepted)} accepted cases; rejections {counts}"
            )
        drawn += 1
        inputs = _draw(profile, rng)
        outcome = interpret.run(program, inputs, target=target, function=function.name, fuel=fuel)
        if isinstance(outcome, interpret.Rejection):
            counts[outcome.kind] = counts.get(outcome.kind, 0) + 1
            rejected.append(Rejected(f"r{len(rejected):05d}", _total_length(inputs), outcome.kind, outcome.message))
            continue
        accepted.append(Case(f"{len(accepted):04d}", False, inputs, outcome))
    return Generated(tuple(accepted), tuple(rejected), drawn, counts)
