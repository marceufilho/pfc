"""Canonical text and conjunction structure of IR expressions, shared by the harness experiments.

The rendering mirrors the frozen printer so that conjunct texts, candidate invariants and emitted C
agree character for character, without importing private helpers of the frozen package.
"""

from pfc import ir


class _Renderer:
    def __init__(self, swap: tuple[int, str] | None) -> None:
        self.swap = swap
        self.seen = 0

    def render(self, expression: ir.Expr) -> str:
        if isinstance(expression, ir.Convert):
            return self.render(expression.operand)
        if isinstance(expression, ir.Literal):
            return expression.spelling
        if isinstance(expression, ir.Result):
            return "RESULT"
        if isinstance(expression, ir.Variable):
            index = self.seen
            self.seen += 1
            if self.swap is not None and self.swap[0] == index:
                return self.swap[1]
            return expression.symbol.name
        if isinstance(expression, ir.Index):
            return f"{expression.array.name}[{self.render(expression.index)}]"
        if isinstance(expression, ir.Unary):
            return f"({expression.operator}{self.render(expression.operand)})"
        if isinstance(expression, ir.Binary):
            return f"({self.render(expression.left)} {expression.operator} {self.render(expression.right)})"
        if isinstance(expression, (ir.Call, ir.Separated)):
            name = expression.function if isinstance(expression, ir.Call) else "SEPARATED"
            return f"{name}({', '.join(self.render(argument) for argument in expression.arguments)})"
        raise ValueError(f"unsupported expression {type(expression).__name__}")


def render(expression: ir.Expr, swap: tuple[int, str] | None = None) -> str:
    """Canonical text; `swap` replaces the k-th variable occurrence (in evaluation order) by a name."""
    return _Renderer(swap).render(expression)


def variables(expression: ir.Expr) -> int:
    """Number of variable occurrences, the index space of `render(..., swap=...)`."""
    renderer = _Renderer(None)
    renderer.render(expression)
    return renderer.seen


def strip_convert(expression: ir.Expr) -> ir.Expr:
    while isinstance(expression, ir.Convert):
        expression = expression.operand
    return expression


def flatten(expression: ir.Expr) -> tuple[ir.Expr, ...]:
    """Top-level conjuncts of a `&&` tree, in source order, looking through _Bool conversions."""
    expression = strip_convert(expression)
    if isinstance(expression, ir.Binary) and expression.operator == "&&":
        return flatten(expression.left) + flatten(expression.right)
    return (expression,)
