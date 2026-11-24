"""The installed CLI has predictable output and exit status."""

import json
from importlib.metadata import version

import pytest

from pfc.cli import main


def test_help(capsys):
    assert main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "usage: pfc" in captured.out
    assert "Example:" in captured.out
    assert not captured.err


def test_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"pfc {version('pfc')}\n"


@pytest.mark.parametrize("arguments", [[], ["not-a-command", "kernel.c"], ["--invalid"]])
def test_usage_errors(arguments, capsys):
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err
    assert not captured.out
    assert "Traceback" not in captured.err


def test_env_help(capsys):
    assert main(["env", "--help"]) == 0
    assert "Example:" in capsys.readouterr().out


def test_force_requires_output(capsys):
    assert main(["env", "--force"]) == 2
    assert "--force requires --output" in capsys.readouterr().err


def test_empty_compiler_configuration(monkeypatch, capsys):
    monkeypatch.setenv("PFC_CLANG", " ")
    assert main(["env"]) == 1
    assert "cannot be empty" in capsys.readouterr().err


def test_debug_exposes_operational_error():
    with pytest.raises(RuntimeError, match="clang"):
        main(["--debug", "env", "--clang", "/missing/pfc-clang"])


def test_env_missing_compiler(capsys):
    assert main(["env", "--clang", "/missing/pfc-clang"]) == 1
    captured = capsys.readouterr()
    assert "clang" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


def test_env_json(monkeypatch, capsys, environment_record):
    monkeypatch.setattr("pfc.cli.collect_environment", lambda compiler: environment_record)
    assert main(["env", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["python"] == "3.12.3"
    assert data["c_flags"] == ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]


def test_env_file_and_overwrite_policy(monkeypatch, tmp_path, capsys, environment_record):
    monkeypatch.setattr("pfc.cli.collect_environment", lambda compiler: environment_record)
    output = tmp_path / "bench" / "ENV.md"
    args = ["env", "--output", str(output)]
    assert main(args + ["--dry-run"]) == 0
    assert not output.exists()
    assert main(args) == 0
    assert "clang version 19.1.1" in output.read_text()
    output.write_text("preserve me")
    assert main(args) == 1
    assert output.read_text() == "preserve me"
    assert main(args + ["--force", "--dry-run"]) == 0
    assert output.read_text() == "preserve me"
    assert main(args + ["--force"]) == 0
    assert output.read_text().startswith("# Environment")
    assert "--force" in capsys.readouterr().err


def test_env_write_error(monkeypatch, tmp_path, capsys, environment_record):
    monkeypatch.setattr("pfc.cli.collect_environment", lambda compiler: environment_record)
    parent = tmp_path / "file"
    parent.write_text("not a directory")
    assert main(["env", "--output", str(parent / "ENV.md")]) == 1
    assert "Traceback" not in capsys.readouterr().err
