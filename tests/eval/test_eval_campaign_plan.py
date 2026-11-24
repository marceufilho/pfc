"""E14 campaign plan: one command per experiment on the sealed held-out set, pre-registered settings, run once."""

from pathlib import Path

import pytest

from pfc_eval import campaign_plan

ROOT = Path(__file__).resolve().parents[2]


def test_plan_covers_every_held_out_experiment_once_with_pre_registered_settings():
    plan = campaign_plan.plan(
        Path("heldout"), date="20270101", acsl=Path("heldout-acsl"), conditions=("L1", "L2", "L3")
    )
    names = [step.name for step in plan]
    assert names == [
        "differential",
        "conjuncts",
        "inject",
        "houdini",
        "houdini-b2",
        "robustness",
        "sweep",
        "proposer-L1",
        "proposer-L2",
        "proposer-L3",
        "runtime",
    ]
    by_name = {step.name: step for step in plan}
    assert list(by_name["differential"].argv[:6]) == ["uv", "run", "--locked", "python", "-m", "pfc_eval"]
    argv = by_name["differential"].argv
    assert argv[argv.index("--seed") + 1] == "0" and argv[-1] == "bench/eval/differential-heldout-20270101"
    assert (
        argv[argv.index("--profile") + 1] == campaign_plan.PROFILES_DIR
    )  # held-out profiles, never the development ones
    assert by_name["houdini"].argv.count("--max-rounds") == 0  # the pre-registered five rounds, never another value
    b2 = by_name["houdini-b2"].argv
    assert b2[b2.index("--family") + 1] == "b2" and b2[b2.index("--profile") + 1] == campaign_plan.PROFILES_DIR
    assert b2[b2.index("--seed") + 1] == "0" and b2[-1] == "bench/eval/houdini-b2-heldout-20270101"
    assert by_name["sweep"].argv[by_name["sweep"].argv.index("--budgets") + 1] == "1,2,5,10,30,60"
    proposer = by_name["proposer-L1"].argv
    assert proposer[proposer.index("--runs") + 1] == "5" and "--live" in proposer and "--replay" not in proposer
    assert "--env-file" in proposer and "OPENAI_API_KEY" not in " ".join(proposer)
    assert by_name["proposer-L3"].argv[by_name["proposer-L3"].argv.index("--base-url") + 1] == campaign_plan.L3_BASE_URL
    assert all(step.output.startswith("bench/eval/") and step.output.endswith("-heldout-20270101") for step in plan)
    assert len({step.output for step in plan}) == len(plan)


def test_plan_without_l3_and_dry_run_text_lists_every_command(capsys):
    plan = campaign_plan.plan(Path("heldout"), date="20270101", acsl=None, conditions=("L1", "L2"))
    assert [s.name for s in plan if s.name.startswith("proposer")] == ["proposer-L1", "proposer-L2"]
    text = campaign_plan.render(plan)
    assert text.count("\n") >= len(plan) and "python -m pfc_eval differential heldout" in text


def test_execute_refuses_an_unsealed_set_and_existing_outputs(tmp_path):
    (tmp_path / "heldout").mkdir()
    with pytest.raises(ValueError, match="sealed"):
        campaign_plan.execute(
            campaign_plan.plan(tmp_path / "heldout", date="x", acsl=None), root=tmp_path, run=lambda argv: 0
        )


def test_execute_runs_each_step_once_in_order_and_stops_at_the_first_failure(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(campaign_plan, "_sealed", lambda root, directory: True)
    plan = campaign_plan.plan(Path("heldout"), date="x", acsl=None, conditions=("L1",))

    def run(argv):
        calls.append(argv)
        return 1 if "inject" in argv else 0

    log = campaign_plan.execute(plan, root=tmp_path, run=run)
    assert [c[6] for c in calls] == ["differential", "conjuncts", "inject"]
    assert log[-1]["exit_code"] == 1 and len(log) == 3 and all("started" in entry for entry in log)
    assert (tmp_path / "bench" / "eval" / "CAMPAIGN-heldout-x.json").is_file()


def test_plan_adds_the_cross_check_only_when_a_cvc5_binary_is_given():
    without = campaign_plan.plan(Path("heldout"), date="20270101", acsl=None, conditions=("L1",))
    assert all(step.name != "crosscheck" for step in without)
    with_cvc5 = campaign_plan.plan(
        Path("heldout"), date="20270101", acsl=None, conditions=("L1",), cvc5=Path("/opt/cvc5")
    )
    step = next(s for s in with_cvc5 if s.name == "crosscheck")
    assert (
        step.argv[step.argv.index("--cvc5") + 1] == "/opt/cvc5"
        and step.output == "bench/eval/crosscheck-heldout-20270101"
    )
    assert [s.name for s in with_cvc5].index("crosscheck") < [s.name for s in with_cvc5].index("proposer-L1")


def test_proposer_env_file_is_an_expanded_path_because_the_plan_runs_without_a_shell(tmp_path, monkeypatch):
    # Observed on 2026-11-10: `uv run --env-file ~/...` exits 2 when no shell expands the tilde.
    monkeypatch.setenv("HOME", str(tmp_path))
    plan = campaign_plan.plan(Path("heldout"), date="20270101", acsl=None, conditions=("L1",))
    proposer = next(step.argv for step in plan if step.name == "proposer-L1")
    env_file = proposer[proposer.index("--env-file") + 1]
    assert env_file == str(tmp_path / ".config" / "pfc" / "openai.env") and "~" not in env_file
