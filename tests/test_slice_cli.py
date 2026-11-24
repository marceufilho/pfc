"""Approved G0 CLI: explicit output, dry-run, failure status and no overwrites."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pfc.cli import main

ROOT = Path(__file__).resolve().parents[1]
KERNEL = str(ROOT / "kernels/sum/kernel.c")


def test_slice_help(capsys):
    assert main(["slice", "--help"]) == 0
    output = capsys.readouterr()
    assert all(text in output.out for text in ("G0", "--output", "--dry-run", "--seed", "--json", "Example:"))
    assert not output.err


@pytest.mark.parametrize(
    "args", [[], [KERNEL], [KERNEL, "--dry-run", "--seed", "-1"], [KERNEL, "--dry-run", "--timeout-ms", "0"]]
)
def test_usage_errors(args, capsys):
    assert main(["slice", *args]) == 2
    assert capsys.readouterr().err


@pytest.mark.integration
def test_real_slice_dry_run_and_save_leave_source_unchanged(tmp_path, capsys):
    output = tmp_path / "gate"
    before = Path(KERNEL).read_bytes()
    args = ["slice", KERNEL, "--output", str(output), "--seed", "17", "--json"]
    assert main([*args, "--dry-run"]) == 0
    assert not output.exists()
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] and data["seed"] == 17 and data["agreed"] == 1000
    assert main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["passed"] and "written" in captured.err
    assert json.loads((output / "manifest.json").read_text())["passed"]
    assert Path(KERNEL).read_bytes() == before
    assert main(args) == 1
    error = capsys.readouterr()
    assert not error.out and json.loads(error.err)["error"]["rule"] == "slice-output"
    assert main([*args, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"]


@pytest.mark.integration
def test_gate_failure_exits_one_and_preserves_failure_report(tmp_path, monkeypatch, capsys):
    from pfc import slice as gate

    original = gate.run

    def fail(*args, **kwargs):
        return replace(original(*args, **kwargs), negative=())

    monkeypatch.setattr(gate, "run", fail)
    output = tmp_path / "failed"
    assert main(["slice", KERNEL, "--output", str(output), "--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["passed"]
    assert not json.loads((output / "manifest.json").read_text())["passed"]


def test_existing_output_rejected_before_compilation(tmp_path, monkeypatch, capsys):
    from pfc import slice as gate

    monkeypatch.setattr(gate, "run", lambda *a, **k: pytest.fail("must reject output first"))
    assert main(["slice", KERNEL, "--output", str(tmp_path)]) == 1
    assert "new directory" in capsys.readouterr().err


def test_slice_frontend_errors_are_structured(tmp_path, capsys):
    assert main(["slice", str(tmp_path / "missing.c"), "--dry-run", "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["rule"] == "source-read" and not output.out


def test_make_slice_wraps_approved_cli():
    import subprocess

    result = subprocess.run(
        ["make", "-n", "slice", "SLICE_OUTPUT=demo/example", "SLICE_ARGS=--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert 'uv run --locked pfc slice kernels/sum/kernel.c --output "demo/example" --dry-run' in result.stdout


def test_invalid_output_path_is_a_structured_error(capsys):
    assert main(["slice", KERNEL, "--output", "bad\x00path", "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["rule"] == "slice-output" and not output.out
