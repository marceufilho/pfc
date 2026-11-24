"""E8 integration: fresh-process pfc budgets with distinct query identities; one WP run on the pinned image; CLI."""

import json
import os
from pathlib import Path

import pytest

from pfc_eval import evidence, sweep
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")


@pytest.mark.integration
def test_pfc_sweep_on_sum_uses_fresh_processes_and_distinct_query_ids():
    rows = sweep.run_pfc(ROOT / "kernels/sum/kernel.c", "sum", (1, 2), compiler=COMPILER, cpp="cpp")
    assert {r["budget_s"] for r in rows} == {1, 2}
    by_budget = {b: {r["identifier"]: r for r in rows if r["budget_s"] == b} for b in (1, 2)}
    assert set(by_budget[1]) == set(by_budget[2]) and len(by_budget[1]) == 5
    for identifier in by_budget[1]:
        assert by_budget[1][identifier]["query_id"] != by_budget[2][identifier]["query_id"]
    assert all(r["status"] == "proved" for r in rows if r["category"] == "required")


@pytest.mark.integration
def test_wp_run_on_sum_at_one_second_parses_goals_from_the_pinned_image(tmp_path):
    profile = sweep.wp_profile()
    if not sweep.image_available(profile.image):
        pytest.skip("pinned Frama-C image not present locally")
    case = next(c for c in profile.cases if c.name == "sum")
    outcome = sweep.run_wp_case(profile, case, 1, tmp_path)
    assert outcome.returncode == 0 and outcome.rows
    assert any(r["kind"] == "property" and r["identifier"].endswith("assert_bounds_a") for r in outcome.rows)
    assert outcome.assessment["status"] in ("conditional-agreement", "inconclusive")
    assert (tmp_path / "wp.json").exists() and (tmp_path / "wp.command.json").exists()


@pytest.mark.integration
def test_cli_sweep_pfc_only_seals_curve_rows(tmp_path, capsys):
    output = tmp_path / "sweep"
    assert (
        main(
            [
                "sweep",
                str(ROOT / "kernels/sum"),
                "--output",
                str(output),
                "--budgets",
                "1,2",
                "--tools",
                "pfc",
                "--dry-run",
            ]
        )
        == 0
    )
    assert not output.exists() and "no files" in capsys.readouterr().out.lower()
    assert (
        main(
            [
                "sweep",
                str(ROOT / "kernels/sum"),
                "--output",
                str(output),
                "--budgets",
                "1,2",
                "--tools",
                "pfc",
                "--clang",
                COMPILER,
            ]
        )
        == 0
    )
    assert evidence.status(output).complete
    rows = evidence.load(output).observations
    assert {r["budget_s"] for r in rows} == {1, 2} and {r["tool"] for r in rows} == {"pfc"}
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["curve"]["pfc"]["1"]["proved"] == summary["curve"]["pfc"]["1"]["total"] == 3
    assert summary["tools"] == ["pfc"] and summary["wp_skipped_reason"] == "not requested"
