import json
from pathlib import Path

import pytest

from pfc.cli import main

KERNEL = Path(__file__).resolve().parents[1] / "kernels/sum/kernel.c"


def test_emit_help(capsys):
    assert main(["emit", "--help"]) == 0
    output = capsys.readouterr()
    assert "--mode {plain,checked,verified}" in output.out and "Example:" in output.out


@pytest.mark.parametrize("mode", ["checked", "verified"])
def test_emit_stdout_and_json(mode, capsys):
    before = KERNEL.read_bytes()
    assert main(["emit", str(KERNEL), "--mode", mode]) == 0
    output = capsys.readouterr()
    assert "long sum(" in output.out and not output.err
    assert ("// proved: obligation" in output.out) == (mode == "verified")
    assert main(["emit", str(KERNEL), "--mode", mode, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == mode and len(data["decisions"]) == 1
    assert data["decisions"][0]["removed"] == (mode == "verified")
    assert (data["proof"] is None) == (mode == "checked")
    assert KERNEL.read_bytes() == before


def test_checked_does_not_call_solver(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("checked emission must not invoke Z3")

    monkeypatch.setattr("pfc.cli.check", unexpected)
    assert main(["emit", str(KERNEL), "--mode", "checked"]) == 0
    assert "__builtin_trap()" in capsys.readouterr().out


def test_verified_failure_still_outputs_guarded_c(tmp_path, capsys):
    source = tmp_path / "bad.c"
    source.write_text("long f(const long a[], long a_len, long n) { return a[n]; }")
    assert main(["emit", str(source), "--mode", "verified", "--json"]) == 1
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert "__builtin_trap()" in data["c"] and not data["decisions"][0]["removed"]
    assert not output.err


@pytest.mark.parametrize("arguments", [[], ["--mode", "invalid"], ["--mode", "verified", "--timeout-ms", "0"]])
def test_emit_requires_valid_explicit_mode(arguments, capsys):
    assert main(["emit", str(KERNEL), *arguments]) == 2
    output = capsys.readouterr()
    assert not output.out and "error:" in output.err


def test_emit_target_error_json(capsys):
    assert main(["emit", str(KERNEL), "--mode", "checked", "--clang", "/missing/clang", "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["rule"] == "target-read" and not output.out


def test_plain_and_fact_ablation_are_public(capsys, monkeypatch):
    assert main(["emit", str(KERNEL), "--mode", "verified", "--facts", "", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["facts"] == [] and not data["decisions"][0]["removed"]
    assert main(["emit", str(KERNEL), "--mode", "verified", "--facts", "assume", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "__builtin_assume(" in data["c"] and "__builtin_trap" in data["c"]
    monkeypatch.setattr("pfc.cli.check", lambda *a, **k: pytest.fail("plain cannot invoke solver"))
    assert main(["emit", str(KERNEL), "--mode", "plain", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["proof"] is None and data["decisions"] == []


@pytest.mark.parametrize("facts", ["unknown", "delete,delete", "delete,,assume", "DELETE"])
def test_invalid_facts_are_usage_errors(facts, capsys):
    assert main(["emit", str(KERNEL), "--mode", "verified", "--facts", facts]) == 2
    assert not capsys.readouterr().out


def test_disabling_facts_cannot_hide_failed_verification(tmp_path, capsys):
    source = tmp_path / "bad.c"
    source.write_text("long f(long a[],long a_len,long i) { return a[i]; }")
    assert main(["emit", str(source), "--mode", "verified", "--facts", ""]) == 1
    assert "__builtin_trap" in capsys.readouterr().out
