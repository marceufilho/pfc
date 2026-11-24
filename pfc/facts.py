"""Conservative compiler facts derived from reviewed contracts, never allocation guesses."""

from dataclasses import dataclass
from typing import Literal

from pfc import ir, vc
from pfc.diagnostics import SourceLocation
from pfc.parse import SpecKind

FactName = Literal["delete", "restrict", "assume"]
DEFAULT_FACTS: frozenset[FactName] = frozenset({"delete"})
ALL_FACTS: frozenset[FactName] = frozenset({"delete", "restrict", "assume"})


@dataclass(frozen=True)
class Fact:
    kind: Literal["restrict", "assume"]
    location: SourceLocation
    obligation_ids: tuple[str, ...]
    contracts: tuple[SourceLocation, ...] = ()


def _conjuncts(expression: ir.Expr) -> tuple[ir.Expr, ...]:
    if isinstance(expression, ir.Convert):
        return _conjuncts(expression.operand)
    if isinstance(expression, ir.Binary) and expression.operator == "&&":
        return _conjuncts(expression.left) + _conjuncts(expression.right)
    return (expression,)


def restricted_parameters(function: ir.Function, conditions: vc.VCReport) -> dict[str, Fact]:
    """Full entry regions, unconditional positive REQUIRES conjuncts, every other array.

    Caller must already have authorized this exact report including all bounds and
    arithmetic dependencies. Region separation does not imply separate allocations.
    """
    arrays = {p.symbol.id: p for p in function.parameters if isinstance(p.symbol.type, ir.ArrayType)}
    pairs: dict[frozenset[str], SourceLocation] = {}
    for contract in function.contracts:
        if contract.kind is not SpecKind.REQUIRES:
            continue
        for predicate in _conjuncts(contract.condition):
            if not isinstance(predicate, ir.Separated):
                continue
            left, n, right, m = predicate.arguments
            if not all(isinstance(item, ir.Variable) for item in (left, n, right, m)):
                continue
            assert isinstance(left, ir.Variable) and isinstance(n, ir.Variable)
            assert isinstance(right, ir.Variable) and isinstance(m, ir.Variable)
            if (
                left.symbol.id in arrays
                and right.symbol.id in arrays
                and arrays[left.symbol.id].length == n.symbol
                and arrays[right.symbol.id].length == m.symbol
            ):
                pairs[frozenset({left.symbol.id, right.symbol.id})] = contract.location
    result = {}
    for key, parameter in arrays.items():
        others = [other for other in arrays if other != key]
        if others and all(frozenset({key, other}) in pairs for other in others):
            contracts = tuple(dict.fromkeys(pairs[frozenset({key, other})] for other in others))
            result[key] = Fact(
                "restrict",
                parameter.symbol.location,
                tuple(o.id for o in conditions.obligations if o.category == "required"),
                contracts,
            )
    return result
