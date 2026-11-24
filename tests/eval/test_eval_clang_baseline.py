"""What clang removes from the checked build on its own, with no proof (second review, W7)."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[2]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clang_baseline", ROOT / "tools/clang_baseline.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_guard_gets_its_own_marker_and_other_traps_are_left_alone() -> None:
    text = (
        "    if (!(0 <= i && i < a_len)) __builtin_trap();\n"
        "    if (__builtin_add_overflow(s, t, &u)) __builtin_trap();\n"
        "    if (!(j != 0)) __builtin_trap();\n"
    )
    marked, count = _module().mark(text)
    assert count == 2
    assert "if (!(0 <= i && i < a_len)) pfc_guard_1();" in marked and "if (!(j != 0)) pfc_guard_2();" in marked
    assert "if (__builtin_add_overflow(s, t, &u)) __builtin_trap();" in marked
    assert marked.startswith("_Noreturn void pfc_guard_1(void);\n_Noreturn void pfc_guard_2(void);\n")


@pytest.mark.integration
def test_clang_keeps_the_guard_of_sum_because_it_cannot_know_the_contract() -> None:
    """Nothing in the C text relates n to a_len, so `i < a_len` is not implied by `i < n` (hand-derived)."""
    module = _module()
    row = module.measure("sum", lower(parse_source(ROOT / "kernels/sum/kernel.c")), read_target("clang-19"), "clang-19")
    assert row["guards_total"] == 1 and row["survive_o2"] == 1 and row["survive_bench"] == 1


@pytest.mark.integration
def test_clang_removes_a_guard_that_the_enclosing_branch_already_implies(tmp_path: Path) -> None:
    """`a[0]` under `if (0 < a_len)`: the guard `0 <= 0 && 0 < a_len` repeats the branch condition."""
    source = (
        '#include "spec.h"\n\nlong first(const long a[], long a_len)\n{\n    REQUIRES(0 <= a_len);\n'
        "    long s = 0;\n    if (0 < a_len) {\n        s = a[0];\n    }\n    return s;\n}\n"
    )
    path = tmp_path / "kernel.c"
    path.write_text(source, encoding="utf-8")
    row = _module().measure("first", lower(parse_source(path)), read_target("clang-19"), "clang-19")
    assert row["guards_total"] == 1 and row["survive_o2"] == 0
