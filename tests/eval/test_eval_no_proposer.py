"""The no-proposer control (advisor review, W6): what the compiler removes with a trivial invariant at every loop."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[2]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("no_proposer", ROOT / "tools/no_proposer.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
def test_sum_keeps_its_only_guard_without_an_invariant_and_loses_it_with_the_written_one() -> None:
    """`a[i]` under `i < n` and `n <= a_len` needs `0 <= i`, which only an invariant supplies (hand-derived)."""
    module = _module()
    row = module.measure("sum", lower(parse_source(ROOT / "kernels/sum/kernel.c")), read_target("clang-19"))
    assert row["loops"] == 1 and row["guards_total"] == 1
    assert row["trivial_accepted"] is False and row["trivial_removed"] == 0
    assert row["written_accepted"] is True and row["written_removed"] == 1


@pytest.mark.integration
def test_a_guard_outside_every_loop_falls_with_no_invariant_at_all(tmp_path: Path) -> None:
    """The control must be able to say "removed": `a[0]` under `1 <= a_len` is provable from the contract alone."""
    source = (
        '#include "spec.h"\n\nlong first(const long a[], long a_len, long n)\n{\n'
        "    REQUIRES(0 <= n && n <= a_len && 1 <= a_len);\n    long s = a[0];\n    long i = 0;\n"
        "    while (i < n) {\n        INVARIANT(0 <= i && i <= n);\n        s = a[i];\n        i = i + 1;\n    }\n"
        "    return s;\n}\n"
    )
    path = tmp_path / "kernel.c"
    path.write_text(source, encoding="utf-8")
    row = _module().measure("first", lower(parse_source(path)), read_target("clang-19"))
    assert row["guards_total"] == 2
    assert row["trivial_removed"] == 1 and row["trivial_accepted"] is False
    assert row["written_removed"] == 2


def test_the_summary_counts_sites_and_programs_separately() -> None:
    module = _module()
    rows = [
        {"guards_total": 2, "trivial_removed": 1, "written_removed": 2, "trivial_accepted": False},
        {"guards_total": 3, "trivial_removed": 3, "written_removed": 3, "trivial_accepted": True},
    ]
    summary = module.summarize(rows)
    assert summary["programs"] == 2 and summary["guards_total"] == 5
    assert summary["trivial_removed"] == 4 and summary["written_removed"] == 5
    assert summary["trivial_programs_all_removed"] == 1 and summary["written_programs_all_removed"] == 2
    assert summary["additional_sites_written_over_trivial"] == 1


def _program(tmp_path: Path, name: str, parameters: str, requires: str, body: str):  # type: ignore[no-untyped-def]
    source = f'#include "spec.h"\n\nlong {name}({parameters})\n{{\n    REQUIRES({requires});\n{body}}}\n'
    path = tmp_path / "kernel.c"
    path.write_text(source, encoding="utf-8")
    return lower(parse_source(path))


def test_the_counter_rule_bounds_an_increasing_counter_by_its_start_and_its_limit(tmp_path: Path) -> None:
    """`i = 0; while (i < n) i = i + 1` gives `0 <= i && i <= n` (hand-derived from the rule text)."""
    body = (
        "    long s = 0;\n    long i = 0;\n    while (i < n) {\n        INVARIANT(1);\n"
        "        s = s + 2;\n        i = i + 1;\n    }\n    return s;\n"
    )
    program = _program(tmp_path, "up", "long n", "0 <= n", body)
    assert _module().counter_invariants(program) == ["0 <= i && i <= n"]


def test_the_counter_rule_reads_a_mirrored_condition_and_a_non_strict_limit(tmp_path: Path) -> None:
    """`n - 1 >= i` is `i <= n - 1`; with step 1 the head bound is the limit plus one; with step 2 only the start."""
    body = (
        "    long i = 0;\n    long j = 3;\n    while (n - 1 >= i) {\n        INVARIANT(1);\n        i = i + 1;\n    }\n"
        "    while (j <= n) {\n        INVARIANT(1);\n        j = j + 2;\n    }\n    return i;\n"
    )
    program = _program(tmp_path, "upto", "long n", "0 <= n", body)
    assert _module().counter_invariants(program) == ["0 <= i && i <= (n - 1) + 1", "3 <= j"]


def test_the_counter_rule_bounds_a_decreasing_counter_symmetrically(tmp_path: Path) -> None:
    """`i = n; while (i > 0) i = i - 1` gives `0 <= i && i <= n`; with `k >= 0`, `0 - 1 <= k && k <= n`."""
    body = (
        "    long i = n;\n    long k = n;\n    while (i > 0) {\n        INVARIANT(1);\n        i = i - 1;\n    }\n"
        "    while (k >= 0) {\n        INVARIANT(1);\n        k = k - 1;\n    }\n    return i;\n"
    )
    program = _program(tmp_path, "down", "long n", "0 <= n", body)
    assert _module().counter_invariants(program) == ["0 <= i && i <= n", "0 - 1 <= k && k <= n"]


def test_the_counter_rule_leaves_a_loop_it_does_not_match_with_the_trivial_invariant(tmp_path: Path) -> None:
    """No initial value in the block (a parameter), a limit written in the body, two updates, a conjunction."""
    body = (
        "    long j = 0;\n    long k = 0;\n    long m = 0;\n"
        "    while (i < n) {\n        INVARIANT(1);\n        i = i + 1;\n    }\n"
        "    while (j < n) {\n        INVARIANT(1);\n        n = n - 1;\n        j = j + 1;\n    }\n"
        "    while (k < 10) {\n        INVARIANT(1);\n        k = k + 1;\n        k = k + 1;\n    }\n"
        "    while (m < 10 && i < 20) {\n        INVARIANT(1);\n        m = m + 1;\n    }\n"
        "    return i;\n"
    )
    program = _program(tmp_path, "none", "long i, long n", "0 <= n", body)
    assert _module().counter_invariants(program) == ["1", "1", "1", "1"]


def test_the_counter_rule_follows_the_preorder_of_nested_loops(tmp_path: Path) -> None:
    body = (
        "    long i = 0;\n    long j = 0;\n    while (i < n) {\n        INVARIANT(1);\n        j = 0;\n"
        "        while (j < i) {\n            INVARIANT(1);\n            j = j + 1;\n        }\n"
        "        i = i + 1;\n    }\n    return j;\n"
    )
    program = _program(tmp_path, "nest", "long n", "0 <= n", body)
    assert _module().counter_invariants(program) == ["0 <= i && i <= n", "0 <= j && j <= i"]


@pytest.mark.integration
def test_the_counter_rule_gets_sum_admitted_and_adds_its_columns_only_on_request() -> None:
    """The rule's `0 <= i && i <= n` is the hand invariant of sum; without the flag the row is the sealed one."""
    module = _module()
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    target = read_target("clang-19")
    plain = module.measure("sum", program, target)
    assert not any(key.startswith("counter_") for key in plain)
    row = module.measure("sum", program, target, rule="counter")
    assert {key: value for key, value in row.items() if not key.startswith("counter_")} == plain
    assert row["counter_accepted"] is True and row["counter_matched_loops"] == 1
    assert row["counter_problem"] is None and row["counter_invariants"] == "0 <= i && i <= n"
    assert set(row) == {column.name for column in module.COUNTER_SCHEMA.columns}


def test_the_summary_reports_counter_columns_only_when_the_rows_carry_them() -> None:
    module = _module()
    base = {"guards_total": 0, "trivial_removed": 0, "written_removed": 0, "trivial_accepted": True, "loops": 2}
    assert not any(key.startswith("counter_") for key in module.summarize([base]))
    rows = [
        {**base, "counter_accepted": True, "counter_matched_loops": 2},
        {**base, "trivial_accepted": False, "counter_accepted": False, "counter_matched_loops": 1},
    ]
    summary = module.summarize(rows)
    assert summary["trivial_programs_accepted"] == 1 and summary["counter_programs_accepted"] == 1
    assert summary["counter_loops_matched"] == 3 and summary["loops_total"] == 4
    assert summary["counter_rule"] == module.COUNTER_RULE
