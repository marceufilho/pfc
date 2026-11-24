"""Reproductions of interface findings in the whole-code architecture review."""

import json
from pathlib import Path

import pytest

from pfc import report
from pfc.cli import main
from pfc.corpus import PROFILES
from pfc.propose.experiment import KernelRun, summarize
from pfc.propose.llm import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def historical(tmp_path):
    directory = tmp_path / "historical"
    directory.mkdir()
    rows = tuple(KernelRun("sum", i, PROFILES["sum"], False, 1, 0, 0, 1, 1, 0, True) for i in (1, 2, 3))
    report.write_json(directory / "experiment.json", summarize("old", "test", rows, settings=Settings.legacy()))
    report.seal(directory, report.Summary("propose", (report.row("sum", None, passed=False),), False))
    return directory


def test_replay_preview_uses_archived_model_and_zero_network_budget(historical, tmp_path, capsys):
    output = tmp_path / "not-created"
    assert (
        main(
            [
                "propose",
                str(ROOT / "kernels/sum"),
                "--replay",
                str(historical),
                "--output",
                str(output),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["settings"]["model"] == "gpt-5.4-2026-03-05"
    assert preview["max_requests"] == 0
    assert not output.exists()


def test_replay_preview_rejects_missing_evidence(tmp_path, capsys):
    assert (
        main(["propose", str(ROOT / "kernels/sum"), "--replay", str(tmp_path / "missing"), "--dry-run", "--json"]) == 1
    )
    assert "report-input" in capsys.readouterr().err


def test_replay_preview_rejects_changed_evidence(historical, capsys):
    with (historical / "experiment.json").open("a") as stream:
        stream.write(" ")
    assert main(["propose", str(ROOT / "kernels/sum"), "--replay", str(historical), "--dry-run", "--json"]) == 1
    assert "report-input" in capsys.readouterr().err


def test_replay_preview_rejects_different_kernel_set(historical, capsys):
    assert main(["propose", str(ROOT / "kernels/dot"), "--replay", str(historical), "--dry-run", "--json"]) == 1
    assert "kernel set" in capsys.readouterr().err
