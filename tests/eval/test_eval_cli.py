"""The standalone evaluation entry point: approved subcommands, help, exit codes."""

import subprocess
import sys

import pytest

from pfc_eval.cli import SUBCOMMANDS, main

APPROVED = (
    "differential",
    "conjuncts",
    "inject",
    "houdini",
    "proposer",
    "sweep",
    "robustness",
    "mutate",
    "runtime",
    "heldout",
    "tables",
    "doctor",
    "crosscheck",
    "negative",
    "audit",
)


def test_subcommand_list_matches_the_approved_set():
    assert tuple(SUBCOMMANDS) == APPROVED


def test_help(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "usage: python -m pfc_eval" in out and "Example:" in out
    for name in APPROVED:
        assert name in out


@pytest.mark.parametrize("name", APPROVED)
def test_every_subcommand_has_help_with_an_example(name, capsys):
    assert main([name, "--help"]) == 0
    out = capsys.readouterr().out
    assert "Example:" in out and name in out


@pytest.mark.parametrize("arguments", [[], ["unknown"], ["differential"], ["heldout"], ["heldout", "burn"]])
def test_usage_errors_exit_2(arguments, capsys):
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err and not captured.out


def test_every_subcommand_has_a_handler_and_tables_refuses_unsealed_evidence(capsys, tmp_path, monkeypatch):
    from pfc_eval.cli import HANDLERS, SUBCOMMANDS

    assert set(HANDLERS) == set(SUBCOMMANDS)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "unsealed").mkdir()
    assert main(["tables", "unsealed"]) == 1
    captured = capsys.readouterr()
    assert "unsealed" in captured.out and list(tmp_path.iterdir()) == [tmp_path / "unsealed"]


def test_proposer_requires_exactly_one_of_live_or_replay(capsys):
    assert main(["proposer", "kernels", "--output", "o", "--condition", "L1"]) == 2
    assert main(["proposer", "kernels", "--output", "o", "--condition", "L1", "--live", "--replay", "d"]) == 2


def test_module_entry_point_runs_in_a_subprocess():
    result = subprocess.run(
        [sys.executable, "-m", "pfc_eval", "--help"], capture_output=True, text=True, check=False, timeout=60
    )
    assert result.returncode == 0 and "usage: python -m pfc_eval" in result.stdout
