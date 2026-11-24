"""Read the selected clang target's integer layout without executing user C."""

import re
from dataclasses import dataclass

from pfc.environment import ToolchainError, compiler_output


@dataclass(frozen=True)
class Target:
    compiler: str
    triple: str
    int_bits: int
    long_bits: int

    def __post_init__(self) -> None:
        if (
            not self.compiler.strip()
            or not self.triple.strip()
            or type(self.int_bits) is not int
            or type(self.long_bits) is not int
            or not 16 <= self.int_bits <= self.long_bits <= 128
        ):
            raise ValueError("unsupported target integer layout")

    def long_range(self) -> tuple[int, int]:
        """Representable C long values on this target: two's complement, no padding bits."""
        return -(2 ** (self.long_bits - 1)), 2 ** (self.long_bits - 1) - 1

    def long_literal(self, value: int) -> str:
        """Spell a long constant that compiles without overflow, including LONG_MIN."""
        low, high = self.long_range()
        if not low <= value <= high:
            raise ValueError("value is not representable in the target long")
        return f"(-{high}L - 1L)" if value == low else f"{value}L"


def read_target(compiler: str) -> Target:
    """Probe fixed builtin macros; support clang two's-complement, no padding layouts.

    No arbitrary flags or macros from the input translation unit may redefine
    these limits. The selected compiler's default target is used in this step.
    """
    version = compiler_output(compiler, "--version").splitlines()[0]
    if "clang version" not in version.lower():
        raise ToolchainError("selected executable does not identify itself as clang")
    triple = compiler_output(compiler, "-dumpmachine")
    text = compiler_output(compiler, "-std=c11", "-nostdinc", "-dM", "-E", "-x", "c", "-", source="")
    names = {"__CHAR_BIT__", "__SIZEOF_INT__", "__SIZEOF_LONG__", "__INT_MAX__", "__LONG_MAX__", "__clang__"}
    macros: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in names:
            if len(parts) != 3 or not re.fullmatch(r"[0-9]{1,40}[Ll]?", parts[2]) or parts[1] in macros:
                raise ToolchainError("invalid integer layout macro from clang")
            macros[parts[1]] = int(parts[2].rstrip("lL"))
    if set(macros) != names or macros["__CHAR_BIT__"] != 8 or macros["__clang__"] != 1:
        raise ToolchainError("clang did not provide a supported integer layout")
    try:
        target = Target(version, triple, 8 * macros["__SIZEOF_INT__"], 8 * macros["__SIZEOF_LONG__"])
    except ValueError as error:
        raise ToolchainError(str(error)) from error
    if (
        macros["__INT_MAX__"] != 2 ** (target.int_bits - 1) - 1
        or macros["__LONG_MAX__"] != 2 ** (target.long_bits - 1) - 1
    ):
        raise ToolchainError("clang integer maxima disagree with the reported storage widths")
    return target
