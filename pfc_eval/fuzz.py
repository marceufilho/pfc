"""Small-domain fuzz inputs shared by the witness search (E4) and the refutation search (E5).

Short arrays, small values, scalars around the length range and occasional aliasing of every array to
one buffer. The domain is deliberately tiny: these searches look for *existence* of a discriminating
state near the boundaries, not for coverage. The constants are recorded in every campaign's configuration.
"""

from random import Random

from pfc import ir
from pfc.parse import SpecKind
from pfc_eval import expressions, interpret

MAX_LENGTH = 6
VALUE = 5
SCALAR = (-4, 8)
ALIAS_PROBABILITY = 0.3
EQUALITIES = "REQUIRES conjuncts `p == literal` and `p == q` are assigned after sampling (p a scalar parameter)"


def domain() -> dict[str, object]:
    return {
        "max_length": MAX_LENGTH,
        "value": VALUE,
        "scalar": list(SCALAR),
        "alias_probability": ALIAS_PROBABILITY,
        "equalities": EQUALITIES,
    }


def equalities(function: ir.Function) -> tuple[tuple[str, int | str], ...]:
    """REQUIRES conjuncts `p == literal` or `p == q` whose assigned side is a scalar, non-length parameter.

    Rejection sampling over the small domain satisfies one equality in about one draw in thirteen and five of
    them almost never (the composed Zilu programs); assigning them keeps every other parameter random. A
    length parameter is never assigned, since it is bound to its array; it may appear on the right side.
    """
    parameters = {p.symbol.name for p in function.parameters if not isinstance(p.symbol.type, ir.ArrayType)}
    lengths = {p.length.name for p in function.parameters if p.length is not None}
    found: list[tuple[str, int | str]] = []
    for contract in function.contracts:
        if contract.kind is not SpecKind.REQUIRES:
            continue
        for part in expressions.flatten(contract.condition):
            inner = expressions.strip_convert(part)
            if not isinstance(inner, ir.Binary) or inner.operator != "==":
                continue
            left, right = expressions.strip_convert(inner.left), expressions.strip_convert(inner.right)
            if isinstance(left, ir.Literal) and isinstance(right, ir.Variable):
                left, right = right, left
            if not isinstance(left, ir.Variable) or left.symbol.name not in parameters - lengths:
                continue
            if isinstance(right, ir.Literal):
                found.append((left.symbol.name, right.value))
            elif isinstance(right, ir.Variable) and right.symbol.name in parameters:
                found.append((left.symbol.name, right.symbol.name))
    return tuple(found)


def draw_inputs(function: ir.Function, rng: Random) -> interpret.Inputs:
    """One fuzz input for `function`; `<array>_len` scalars always equal their buffer length."""
    arrays: dict[str, list[int]] = {}
    array_parameters = [p for p in function.parameters if p.length is not None]
    shared: list[int] | None = None
    if len(array_parameters) >= 2 and rng.random() < ALIAS_PROBABILITY:
        shared = [rng.randint(-VALUE, VALUE) for _ in range(rng.randint(0, MAX_LENGTH))]
    for parameter in array_parameters:
        symbol_type = parameter.symbol.type
        if isinstance(symbol_type, ir.ArrayType) and symbol_type.element is ir.ScalarType.BOOL:
            # `_Bool` elements are 0 or 1 at entry (the interpreter rejects anything else); never shared with a long
            # buffer. Programs without `_Bool` arrays draw exactly the same random stream as before.
            arrays[parameter.symbol.name] = [rng.randint(0, 1) for _ in range(rng.randint(0, MAX_LENGTH))]
        elif shared is not None:
            arrays[parameter.symbol.name] = shared
        else:
            length = rng.randint(0, MAX_LENGTH)
            arrays[parameter.symbol.name] = [rng.randint(-VALUE, VALUE) for _ in range(length)]
    scalars: dict[str, int] = {}
    lengths = {p.length.name: p.symbol.name for p in array_parameters if p.length is not None}
    for parameter in function.parameters:
        name = parameter.symbol.name
        if parameter.length is not None:
            continue
        if name in lengths:
            scalars[name] = len(arrays[lengths[name]])
        elif parameter.symbol.type is ir.ScalarType.BOOL:
            scalars[name] = rng.randint(0, 1)
        else:
            scalars[name] = rng.randint(*SCALAR)
    for _ in range(2):  # Two passes resolve chains written in either order (`k == n`, `j == k`).
        for name, value in equalities(function):
            scalars[name] = value if isinstance(value, int) else scalars[value]
    return interpret.Inputs(scalars, arrays)
