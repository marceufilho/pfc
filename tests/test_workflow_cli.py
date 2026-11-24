import json
import shutil
from pathlib import Path

import pytest

from pfc.cli import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def four(tmp_path):
    root = tmp_path / "kernels"
    for name in ("sum", "dot", "saxpy", "prefix_sum"):
        shutil.copytree(ROOT / "kernels" / name, root / name)
    return root


@pytest.mark.parametrize("command", ["verify", "build", "test", "report"])
def test_workflow_help(command, capsys):
    assert main([command, "--help"]) == 0
    assert "Example:" in capsys.readouterr().out


def test_verify_aggregates_all_inputs_and_failed_obligations(four, capsys):
    assert main(["verify", str(four), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["rows"]) == 4 and data["passed"]
    assert all(row["required_passed"] for row in data["rows"])
    (four / "dot/kernel.c").write_text("long dot(long a[],long a_len){return a[0];}")
    assert main(["verify", str(four), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert len(data["rows"]) == 4 and not data["passed"]


def test_empty_missing_and_invalid_directories_fail(tmp_path, capsys):
    for source in (tmp_path, tmp_path / "missing"):
        assert main(["verify", str(source), "--json"]) == 1
        output = capsys.readouterr()
        assert not output.out and json.loads(output.err)["error"]["rule"] == "workflow-input"


@pytest.mark.integration
def test_build_preview_save_and_report_are_reproducible(four, tmp_path, capsys):
    output = tmp_path / "built"
    args = [
        "build",
        str(four),
        "--mode",
        "verified",
        "--facts",
        "delete,restrict,assume",
        "--output",
        str(output),
        "--json",
    ]
    assert main([*args, "--dry-run"]) == 0 and not output.exists()
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(summary["rows"]) == 4 and output.is_dir()
    assert main(["report", str(output), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == summary
    assert main(args) == 1
    assert not capsys.readouterr().out
    (output / "report.json").write_text("{}")
    assert main(["report", str(output), "--json"]) == 1
    assert not capsys.readouterr().out


@pytest.mark.integration
def test_test_command_passes_with_the_approved_dependent_consequence_criterion(four, capsys):
    assert main(["test", str(four), "--seed", "13", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(summary["rows"]) == 4
    assert all(row["compared"] == row["agreed"] == 1000 for row in summary["rows"])
    assert all(row["breakit_passed"] for row in summary["rows"])
    assert summary["passed"]


def test_workflow_toolchain_failures_are_json_diagnostics(four, capsys):
    assert main(["verify", str(four), "--clang", "/missing/clang", "--json"]) == 1
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["error"]["rule"] == "workflow-operation"
