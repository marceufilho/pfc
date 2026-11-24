"""Reviewed six-kernel input profiles and independent concrete arithmetic oracles."""

from dataclasses import dataclass
from random import Random

from pfc import ir
from pfc.config import DIFF_CASES, MAX_DIFF_LENGTH, MAX_DIFF_SEED
from pfc.diagnostics import FrontendError
from pfc.difftest import SUM_PROFILE, Observation, generate_inputs
from pfc.digest import sha256_text
from pfc.target import Target

PROFILES = {
    "binary_search": "68b4548061cf465635eb45e1a1bd5f90c33aa7caba452c7f2be79642ff581ce4",
    "matvec": "998dbc08da0d9b9e11ab75fce75002ade92c21cfd5b4ca16c83912e2c1a3390f",
    "sum": SUM_PROFILE,
    "dot": "b065a53bd693aac1875da0ac760cd6fcfa21735aba1a7226cfa0ffe097ff53ca",
    "saxpy": "471faf3bd8365dc80035ba8ac0f6f385e638be93d8d1f91d0b0e1d4169734db7",
    "prefix_sum": "786f9a5acb59b625d1bb64ca554ec4cbaa0dc2f344242c5fe006fdc8b8ac445b",
}
GENERATOR = "six-kernel-inputs-v2: explicit boundaries plus seeded bounded integers; full array observations"
# Plausible but non-inductive candidates, one per loop in preorder. Each fails initiation
# (or preservation) with a real model; rejecting them exercises more than the literal `0`.
NEAR_MISS_INVARIANTS: dict[str, tuple[str, ...]] = {
    "sum": ("0 <= i && i < n",),
    "dot": ("0 <= i && i < n",),
    "saxpy": ("0 <= i && i < n",),
    "prefix_sum": ("1 <= i && i < n",),
    "matvec": ("0 <= i && i < rows", "0 <= j && j <= cols"),
    "binary_search": ("0 <= lo && lo <= hi && hi < n",),
}


@dataclass(frozen=True)
class Case:
    arrays: tuple[tuple[int, ...], ...]
    n: int
    alpha: int = 0
    cols: int = 1
    needle: int = 0

    @property
    def values(self) -> tuple[int, ...]:
        return tuple(value for array in self.arrays for value in array)


def profile(program: ir.Program) -> str:
    for name, identity in PROFILES.items():
        if sha256_text(ir.print_c(program)) == identity:
            return name
    raise FrontendError(
        "difftest-profile", "no reviewed input/oracle profile for this exact program", program.functions[0].location
    )


def _compute(name: str, case: Case) -> tuple[Observation, tuple[int, ...]]:
    arrays = [list(array) for array in case.arrays]
    intermediates: list[int] = []
    result = 0
    if name in {"sum", "dot"}:
        for i in range(case.n):
            term = arrays[0][i] if name == "sum" else arrays[0][i] * arrays[1][i]
            result += term
            intermediates.extend((term, result))
    elif name == "saxpy":
        for i in range(case.n):
            product = case.alpha * arrays[0][i]
            arrays[1][i] += product
            intermediates.extend((product, arrays[1][i]))
        result = case.n
    elif name == "prefix_sum":
        for i in range(1, case.n):
            arrays[0][i] += arrays[0][i - 1]
            intermediates.append(arrays[0][i])
        result = case.n
    elif name == "matvec":
        for i in range(case.n):
            total = 0
            for j in range(case.cols):
                product = arrays[0][i * case.cols + j] * arrays[1][j]
                total += product
                intermediates.extend((product, total))
            arrays[2][i] = total
        result = case.n
    elif name == "binary_search":
        result = next((i for i, value in enumerate(arrays[0][: case.n]) if value == case.needle), -1)
    else:
        raise ValueError("unknown reviewed kernel")
    return Observation(result, tuple(value for array in arrays for value in array)), tuple(intermediates)


def oracle(name: str, case: Case) -> Observation:
    """Independent algorithm-level expectation, including untouched suffixes."""
    return _compute(name, case)[0]


def matches(name: str, case: Case, observation: Observation) -> bool:
    """Search permits any matching index; the cross-mode comparison remains exact."""
    if name != "binary_search":
        return observation == oracle(name, case)
    if observation.values != case.values:
        return False
    index = observation.value
    return (
        case.needle not in case.arrays[0][: case.n]
        if index == -1
        else 0 <= index < case.n and case.arrays[0][index] == case.needle
    )


def validate(name: str, case: Case, target: Target) -> None:
    """Check the concrete entry domain and every intermediate signed operation."""
    count = 3 if name == "matvec" else 2 if name in {"dot", "saxpy"} else 1
    if name not in PROFILES or len(case.arrays) != count or not 32 <= target.long_bits <= 64:
        raise ValueError("unsupported kernel input layout or target")
    if any(type(v) is not int for v in (case.n, case.alpha, case.cols, case.needle)) or case.n < 0:
        raise ValueError("invalid scalar input")
    if any(not isinstance(a, tuple) or len(a) > MAX_DIFF_LENGTH for a in case.arrays):
        raise ValueError("array length violates corpus limit")
    if name == "matvec":
        valid = (
            case.cols > 0
            and case.n * case.cols <= len(case.arrays[0])
            and case.cols <= len(case.arrays[1])
            and case.n <= len(case.arrays[2])
        )
    else:
        valid = all(case.n <= len(a) for a in case.arrays)
    if not valid:
        raise ValueError("array length violates entry contract")
    if name == "binary_search" and tuple(sorted(case.arrays[0][: case.n])) != case.arrays[0][: case.n]:
        raise ValueError("search prefix must be sorted")
    low, high = target.long_range()
    if any(
        type(v) is not int or not low <= v <= high for v in (*case.values, case.n, case.alpha, case.cols, case.needle)
    ):
        raise ValueError("input is outside C long range")
    _, intermediates = _compute(name, case)
    if any(not low <= value <= high for value in intermediates):
        raise ValueError("executed arithmetic would overflow")


def generate(name: str, target: Target, *, seed: int) -> tuple[Case, ...]:
    if type(seed) is not int or not 0 <= seed <= MAX_DIFF_SEED or name not in PROFILES:
        raise ValueError("invalid seed or kernel profile")
    if name == "sum":
        return tuple(Case((case.values,), case.n) for case in generate_inputs(target, seed=seed))
    count = 2 if name in {"dot", "saxpy"} else 1
    cases = [
        Case(tuple(() for _ in range(count)), 0),
        Case(tuple((7, -3, 9) for _ in range(count)), 0, -2),
        Case(tuple((7, -3, 9) for _ in range(count)), 1, 0),
        Case(tuple((7, -3, 9) for _ in range(count)), 2, 2),
        Case(tuple((0,) * MAX_DIFF_LENGTH for _ in range(count)), MAX_DIFF_LENGTH, -1),
    ]
    if name == "matvec":
        cases = [Case(((), (1,), ()), 0), Case(((1, 2, 3, 4, 99), (2, 3), (8, 9, 77)), 2, cols=2)]
    elif name == "binary_search":
        cases = [Case(((),), 0), Case(((1, 1, 1),), 3, needle=1), Case(((2, 4, 8, 99),), 3, needle=99)]
    random = Random(seed)
    while len(cases) < DIFF_CASES:
        if name == "matvec":
            rows, cols = random.randint(0, 8), random.randint(1, 8)
            matrix_lengths = (random.randint(rows * cols, 64), cols + random.randint(0, 2), rows + random.randint(0, 2))
            arrays = tuple(tuple(random.randint(-100, 100) for _ in range(n)) for n in matrix_lengths)
            cases.append(Case(arrays, rows, cols=cols))
            continue
        if name == "binary_search":
            values = tuple(sorted(random.randint(-100, 100) for _ in range(random.randint(0, 64))))
            needle = random.choice(values) if values and random.randrange(2) else random.randint(-200, 200)
            cases.append(Case((values,), random.randint(0, len(values)), needle=needle))
            continue
        lengths = tuple(random.randrange(MAX_DIFF_LENGTH + 1) for _ in range(count))
        arrays = tuple(tuple(random.randint(-100, 100) for _ in range(length)) for length in lengths)
        cases.append(Case(arrays, random.randint(0, min(lengths)), random.randint(-10, 10)))
    for case in cases:
        validate(name, case, target)
    return tuple(cases)


def driver_source(program: ir.Program, cases: tuple[Case, ...], target: Target) -> str:
    """Fresh distinct physical buffers satisfy separation; print every final cell."""
    name = profile(program)
    for case in cases:
        validate(name, case, target)
    function = program.functions[0]
    parameters = ", ".join(ir._declaration(p.symbol) for p in function.parameters)

    def array_literal(values: tuple[int, ...]) -> str:
        return "{" + ",".join(target.long_literal(v) for v in values) + "}" if values else "{0}"

    rows = ",\n".join("{" + ",".join(array_literal(a) for a in case.arrays) + "}" for case in cases)
    lengths = ",".join("{" + ",".join(str(len(a)) for a in case.arrays) + "}" for case in cases)
    prefixes = ",".join(str(case.n) for case in cases)
    alphas = ",".join(target.long_literal(case.alpha) for case in cases)
    count = len(cases[0].arrays)
    arguments = ["a[0]", "lengths[row][0]"]
    for k in range(1, count):
        arguments.extend([f"a[{k}]", f"lengths[row][{k}]"])
    arguments.append("prefixes[row]")
    if name == "saxpy":
        arguments.insert(0, "alphas[row]")
    extra_definition = ""
    if name in {"matvec", "binary_search"}:
        values = ",".join(target.long_literal(c.cols if name == "matvec" else c.needle) for c in cases)
        extra_definition = f"static const long extras[] = {{{values}}};"
        arguments.append("extras[row]")
    alpha_definition = f"static const long alphas[] = {{{alphas}}};" if name == "saxpy" else ""
    return f"""#include <stdio.h>
#include <sys/resource.h>
{function.return_type} {function.name}({parameters});
static const long inputs[{len(cases)}][{count}][{MAX_DIFF_LENGTH}] = {{
{rows}
}};
static const long lengths[{len(cases)}][{count}] = {{{lengths}}};
static const long prefixes[] = {{{prefixes}}};
{alpha_definition}
{extra_definition}
int main(void) {{
 struct rlimit limit = {{0,0}};
 if (setrlimit(RLIMIT_CORE,&limit)!=0) return 2;
 for(long row=0;row<{len(cases)};row++) {{
  long a[{count}][{MAX_DIFF_LENGTH}] = {{0}};
  long total=0;
  for(long k=0;k<{count};k++) {{
   total+=lengths[row][k];
   for(long j=0;j<lengths[row][k];j++) a[k][j]=inputs[row][k][j];
  }}
  long result={name}({', '.join(arguments)});
  printf("%ld %ld %ld",row,result,total);
  for(long k=0;k<{count};k++)
   for(long j=0;j<lengths[row][k];j++) printf(" %ld",a[k][j]);
  putchar('\\n');
 }}
 return ferror(stdout)?2:0;
}}
"""
