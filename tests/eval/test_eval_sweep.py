"""Solver-budget sweep: budgets, fresh-process pfc rows, WP campaign runner over the pinned image (roadmap E8)."""

from pathlib import Path

import pytest

from pfc_eval import sweep

ROOT = Path(__file__).resolve().parents[2]


def test_budget_parsing_requires_distinct_positive_seconds():
    assert sweep.parse_budgets("1,2,5,10,30,60") == (1, 2, 5, 10, 30, 60)
    assert sweep.DEFAULT_BUDGETS == (1, 2, 5, 10, 30, 60)
    for bad in ("0,1", "5,5", "a", "", "61"):
        with pytest.raises(ValueError):
            sweep.parse_budgets(bad)


def test_pfc_rows_keep_query_identity_status_and_time():
    report = {
        "function": "sum",
        "timeout_ms": 2000,
        "required_passed": True,
        "results": [
            {
                "obligation": {"id": "entry:aaa", "kind": "entry", "category": "required"},
                "query_id": "q-entry-2000",
                "status": "proved",
                "elapsed_ms": 1.5,
                "dependencies_satisfied": True,
            },
            {
                "obligation": {"id": "overflow:bbb", "kind": "overflow", "category": "reported"},
                "query_id": "q-overflow-2000",
                "status": "timeout",
                "elapsed_ms": 2000.0,
                "dependencies_satisfied": False,
            },
        ],
        "entry_results": [{"query_id": "q-sat", "status": "sat", "elapsed_ms": 0.4}],
    }
    rows = sweep.pfc_rows("sum", 2, report)
    assert [r["identifier"] for r in rows] == ["entry:aaa", "overflow:bbb"]
    assert rows[0] == {
        "tool": "pfc",
        "kernel": "sum",
        "budget_s": 2,
        "unit": "obligation",
        "identifier": "entry:aaa",
        "kind": "entry",
        "category": "required",
        "status": "proved",
        "solver_ms": 1.5,
        "query_id": "q-entry-2000",
        "smoke": None,
    }
    assert rows[1]["status"] == "timeout" and rows[1]["category"] == "reported"
    with pytest.raises(ValueError):
        sweep.pfc_rows("sum", 2, {"timeout_ms": 1000, "results": []})  # budget mismatch is a protocol error


def test_pfc_check_command_runs_a_fresh_process_per_budget():
    argv = sweep.pfc_command(ROOT / "kernels/sum/kernel.c", 5, compiler="clang-19", cpp="cpp")
    assert argv[:2] == ["pfc", "check"] and "--timeout-ms" in argv and argv[argv.index("--timeout-ms") + 1] == "5000"
    assert "--json" in argv and "--clang" in argv and "--cpp" in argv


def test_wp_rows_use_the_frozen_goal_parser_and_classify_goals():
    payload = [
        {"goal": "sum_requires", "verdict": "unknown", "smoke": True, "passed": True},
        {"goal": "sum_loop_invariant_preserved", "verdict": "valid", "smoke": False, "passed": True},
        {"goal": "sum_assert_bounds_a", "verdict": "valid", "smoke": False, "passed": True},
        {"goal": "sum_assert_rte_mem_access", "verdict": "timeout", "smoke": False, "passed": False},
        {"goal": "sum_assert_rte_signed_overflow", "verdict": "unknown", "smoke": False, "passed": False},
    ]
    rows = sweep.wp_rows("sum", 5, payload, expected=("bounds_a",))
    assert [(r["kind"], r["status"]) for r in rows] == [
        ("smoke", "unknown"),
        ("support", "valid"),
        ("property", "valid"),
        ("rte", "timeout"),
        ("overflow", "unknown"),
    ]
    assert all(r["tool"] == "wp" and r["budget_s"] == 5 and r["unit"] == "goal" and r["query_id"] is None for r in rows)
    assert rows[0]["smoke"] is True and rows[1]["smoke"] is False
    assessment = sweep.wp_assessment("sum", 5, payload, expected=("bounds_a",), returncode=0)
    assert assessment["status"] == "inconclusive" and assessment["compared"] == {"bounds_a": ["valid"]}


def test_wp_command_pins_the_image_isolates_the_container_and_sets_only_the_budget(tmp_path):
    profile = sweep.wp_profile()
    assert profile.image.startswith("framac/frama-c@sha256:") and len(profile.cases) == 14
    case = next(c for c in profile.cases if c.name == "sum")
    argv = sweep.wp_command(profile, case, 30, tmp_path, container="pfc-sweep-test")
    assert argv[:3] == ["docker", "run", "--rm"] and "--pull=never" in argv and "--network=none" in argv
    assert profile.image in argv and "frama-c" in argv and "/input/sources/sum.c" in argv
    assert argv[argv.index("-wp-timeout") + 1] == "30" and argv[argv.index("-wp-smoke-timeout") + 1] == "2"
    assert argv.count("-wp-timeout") == 1 and "-wp-report-json" in argv
    relative = sweep.wp_command(profile, case, 1, Path("relative/out"), container="c")
    mount = next(a for a in relative if a.startswith("type=bind,src=") and a.endswith("dst=/output"))
    assert Path(mount.split("src=")[1].split(",")[0]).is_absolute()  # Docker rejects relative bind paths
    assert sweep.wp_outer_timeout(30) >= 30 * 10


def test_image_availability_is_checked_through_an_injected_runner():
    calls = []

    def present(argv):
        calls.append(argv)
        return 0, "sha256:abc\n"

    def missing(argv):
        return 1, ""

    assert sweep.image_available("framac/frama-c@sha256:abc", run=present) is True
    assert calls[0][:3] == ["docker", "image", "inspect"]
    assert sweep.image_available("framac/frama-c@sha256:abc", run=missing) is False


def test_summary_builds_the_proved_fraction_curve_per_tool_and_kernel():
    rows = [
        {
            "tool": "pfc",
            "kernel": "sum",
            "budget_s": 1,
            "unit": "obligation",
            "identifier": "a",
            "kind": "entry",
            "category": "required",
            "status": "proved",
            "solver_ms": 1.0,
            "query_id": "q1",
            "smoke": None,
        },
        {
            "tool": "pfc",
            "kernel": "sum",
            "budget_s": 1,
            "unit": "obligation",
            "identifier": "b",
            "kind": "bounds",
            "category": "required",
            "status": "timeout",
            "solver_ms": 1000.0,
            "query_id": "q2",
            "smoke": None,
        },
        {
            "tool": "pfc",
            "kernel": "sum",
            "budget_s": 2,
            "unit": "obligation",
            "identifier": "b",
            "kind": "bounds",
            "category": "required",
            "status": "proved",
            "solver_ms": 1500.0,
            "query_id": "q3",
            "smoke": None,
        },
        {
            "tool": "pfc",
            "kernel": "sum",
            "budget_s": 1,
            "unit": "obligation",
            "identifier": "c",
            "kind": "overflow",
            "category": "reported",
            "status": "failed",
            "solver_ms": 1.0,
            "query_id": "q4",
            "smoke": None,
        },
        {
            "tool": "wp",
            "kernel": "sum",
            "budget_s": 1,
            "unit": "goal",
            "identifier": "g",
            "kind": "property",
            "category": "property",
            "status": "valid",
            "solver_ms": None,
            "query_id": None,
            "smoke": False,
        },
        {
            "tool": "wp",
            "kernel": "sum",
            "budget_s": 1,
            "unit": "goal",
            "identifier": "s",
            "kind": "smoke",
            "category": "smoke",
            "status": "unknown",
            "solver_ms": None,
            "query_id": None,
            "smoke": True,
        },
    ]
    summary = sweep.summarize(rows, budgets=(1, 2), tools=("pfc", "wp"))
    curve = summary["curve"]
    assert curve["pfc"]["1"] == {"proved": 1, "total": 2} and curve["pfc"]["2"] == {"proved": 1, "total": 1}
    assert curve["wp"]["1"] == {"proved": 1, "total": 1}  # smoke goals are not part of the fraction
    assert summary["kernels"]["sum"]["pfc"]["1"]["proved"] == 1
    table = sweep.render_table(summary)
    assert "budget" in table and "pfc" in table and "wp" in table


def test_wp_profile_rejects_a_tampered_source(tmp_path, monkeypatch):
    profile = sweep.wp_profile()
    case = profile.cases[0]
    tampered = tmp_path / "input"
    (tampered / "sources").mkdir(parents=True)
    (tampered / "sources" / Path(case.source).name).write_text("int x;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        sweep.verify_case_source(case, tampered)
    sweep.verify_case_source(case, sweep.WP_INPUT)


def test_wp_profile_accepts_a_prover_override_and_an_image_id_and_records_both():
    profile = sweep.wp_profile(prover="z3", image="sha256:" + "ab" * 32)
    assert profile.prover == "z3 (override)" and profile.image == "sha256:" + "ab" * 32
    assert profile.options[profile.options.index("-wp-prover") + 1] == "z3"
    assert profile.options.count("-wp-prover") == 1
    described = sweep.describe(profile)
    assert described["prover_override"] == "z3" and described["image_override"] == "sha256:" + "ab" * 32
    assert described["baseline_prover"] == "Alt-Ergo 2.5.4" and described["baseline_image"].startswith(
        "framac/frama-c@sha256:"
    )
    baseline = sweep.wp_profile()
    assert (
        sweep.describe(baseline)["prover_override"] is None
        and baseline.options == sweep.wp_profile(prover="alt-ergo").options
    )


def test_wp_command_passes_the_selected_prover_and_image_and_nothing_else_changes(tmp_path):
    baseline = sweep.wp_profile()
    derived = sweep.wp_profile(prover="z3", image="sha256:" + "cd" * 32)
    case = next(c for c in baseline.cases if c.name == "matvec")
    a = sweep.wp_command(baseline, case, 10, tmp_path, container="c")
    b = sweep.wp_command(derived, case, 10, tmp_path, container="c")
    assert a[a.index("-wp-prover") + 1] == "alt-ergo" and b[b.index("-wp-prover") + 1] == "z3"
    assert baseline.image in a and derived.image in b
    strip = lambda argv: [x for x in argv if x not in (baseline.image, derived.image, "alt-ergo", "z3")]  # noqa: E731
    assert strip(a) == strip(b)


def test_wp_profile_rejects_an_unpinned_image_reference():
    with pytest.raises(ValueError, match="pinned"):
        sweep.wp_profile(image="framac/frama-c:latest")
    with pytest.raises(ValueError, match="prover"):
        sweep.wp_profile(prover="cvc5")
