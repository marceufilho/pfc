"""The approved check command exposes solver results without emitting C."""

import json
import os
from pathlib import Path

import pytest

from pfc.cli import main
from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG

KERNEL = Path(__file__).resolve().parents[1] / "kernels/sum/kernel.c"


def test_check_help(capsys):
    assert main(["check", "--help"]) == 0
    output = capsys.readouterr()
    assert "Example:" in output.out and "--timeout-ms" in output.out and "--clang" in output.out
    assert not output.err


@pytest.mark.integration
def test_check_sum_text(capsys):
    assert main(["check", str(KERNEL)]) == 0
    output = capsys.readouterr()
    assert "3/3 required formulas proved" in output.out
    assert "required dependencies passed" in output.out
    assert "reported overflow: failed" in output.out
    assert "No check removal authorized" in output.out
    assert not output.err


@pytest.mark.integration
def test_check_json_target_timeout_and_no_writes(tmp_path, capsys, monkeypatch):
    compiler = os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
    monkeypatch.setenv(CLANG_ENV_VAR, "/missing/environment-clang")
    source = tmp_path / "kernel.c"
    source.write_text(KERNEL.read_text().replace('#include "kernel.h"', ""))
    before = source.read_bytes()
    assert main(["check", str(source), "--json", "--clang", compiler, "--timeout-ms", "5000"]) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["required_passed"] and not data["check_removal_authorized"]
    assert data["timeout_ms"] == 5000 and "clang version" in data["target"]["compiler"]
    assert data["target"]["long_bits"] == 64 and len(data["results"]) == 5
    assert data["results"][0]["status"] == "proved"
    assert source.read_bytes() == before and list(tmp_path.iterdir()) == [source]
    assert not output.err


@pytest.mark.integration
def test_check_false_invariant_returns_failure_and_model(tmp_path, capsys):
    source = tmp_path / "bad.c"
    source.write_text(KERNEL.read_text().replace('#include "kernel.h"', "").replace("i <= n", "i < n"))
    assert main(["check", str(source), "--json"]) == 1
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert not data["required_passed"]
    entry = data["results"][0]
    assert entry["status"] == "failed"
    values = {item["name"]: item["value"] for item in entry["counterexample"]["scalars"]}
    assert values["n"] == 0 and values["i"] == 0
    assert not data["results"][3]["dependencies_satisfied"]
    assert not output.err


@pytest.mark.parametrize("value", ["0", "-1", "60001", "abc"])
def test_check_invalid_timeout_is_usage_error(value, capsys):
    assert main(["check", str(KERNEL), "--timeout-ms", value]) == 2
    output = capsys.readouterr()
    assert "--timeout-ms" in output.err and "1..60000" in output.err
    assert not output.out


@pytest.mark.integration
def test_check_target_error_is_json(capsys):
    assert main(["check", str(KERNEL), "--clang", "/missing/clang", "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["rule"] == "target-read"
    assert not output.out and "Traceback" not in output.err


def test_check_missing_source_is_json(tmp_path, capsys):
    assert main(["check", str(tmp_path / "missing.c"), "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["rule"] == "source-read"
    assert not output.out
