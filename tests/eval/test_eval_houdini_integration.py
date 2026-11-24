"""E6 integration with Z3: B1 on the six development kernels, and the sealed CLI campaign."""

import json
import os
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import evidence, houdini
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")


@pytest.fixture(scope="module")
def target():
    return read_target(COMPILER)


@pytest.mark.integration
@pytest.mark.parametrize("name", KERNELS)
def test_b1_at_the_preregistered_bound_solves_five_kernels_and_hits_the_limit_on_matvec(name, target):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    result = houdini.run_kernel(program, target, timeout_ms=10000)
    assert result.queries >= len(result.rounds) + 1 and 1 <= len(result.rounds) <= houdini.MAX_ROUNDS
    assert all(not r.timeouts and not r.unknown and not r.errors for r in result.rounds)
    if name == "matvec":
        # Observed on 2026-11-05: candidates are still being removed in round 5 (nested loops lengthen the
        # dependency chains), so the pre-registered bound stops before a fixpoint and admission fails.
        assert result.status == "round-limit" and result.accepted is False
        assert result.rejection_class == "not-inductive" and result.rounds[-1].removed
    else:
        assert result.accepted is True, result.rejection_class
        assert result.status == "fixpoint"
    for loop_index, texts in enumerate(result.remaining):
        assert texts, loop_index
    assert result.hand_invariants == tuple(
        houdini.render(loop.invariant.condition) for loop in houdini.loops(program.functions[0])
    )


@pytest.mark.integration
def test_binary_search_keeps_the_three_hand_conjuncts(target):
    program = lower(parse_source(ROOT / "kernels" / "binary_search" / "kernel.c"))
    result = houdini.run_kernel(program, target, timeout_ms=10000)
    remaining = set(result.remaining[0])
    assert {"(0 <= lo)", "(lo <= (hi + 1))", "(hi <= (n - 1))"} <= remaining


@pytest.mark.integration
def test_cli_houdini_seals_per_loop_rows(tmp_path, capsys):
    output = tmp_path / "houdini"
    assert (
        main(["houdini", str(ROOT / "kernels" / "sum"), "--output", str(output), "--dry-run", "--clang", COMPILER]) == 0
    )
    assert not output.exists() and "no files" in capsys.readouterr().out.lower()
    assert (
        main(
            [
                "houdini",
                str(ROOT / "kernels" / "sum"),
                str(ROOT / "kernels" / "matvec"),
                "--output",
                str(output),
                "--clang",
                COMPILER,
            ]
        )
        == 1  # matvec is not admitted at the pre-registered bound, so the campaign exit code is 1
    )
    assert evidence.status(output).complete
    rows = evidence.load(output).observations
    assert [(r["kernel"], r["loop_index"]) for r in rows] == [("matvec", 0), ("matvec", 1), ("sum", 0)]
    by_kernel = {r["kernel"]: r for r in rows}
    assert by_kernel["sum"]["task_accepted"] is True and by_kernel["sum"]["status"] == "fixpoint"
    assert by_kernel["matvec"]["task_accepted"] is False and by_kernel["matvec"]["status"] == "round-limit"
    assert all(r["hand_in_family"] is True and r["candidates_initial"] > r["candidates_remaining"] > 0 for r in rows)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["kernels"]["sum"]["accepted"] is True and summary["kernels"]["sum"]["queries"] >= 2
    assert summary["success_at_1"] == 1 and summary["tasks"] == 2
    configuration = json.loads((output / "configuration.json").read_text(encoding="utf-8"))
    assert configuration["settings"]["family"] == houdini.FAMILY_TEXT and configuration["settings"]["max_rounds"] == 5
    assert configuration["settings"]["preregistered_max_rounds"] == 5
    assert (output / "tables.md").exists() and (output / "sources" / "sum-b1-verified.c").exists()
