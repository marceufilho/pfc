"""Differential CLI output, exit codes and delegation to the existing harness."""

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from pfc.cli import main
from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG, MAX_DIFF_SEED
from pfc.difftest import run_sum
from pfc.lower import lower
from pfc.parse import parse_source

KERNEL = Path(__file__).resolve().parents[1] / "kernels/sum/kernel.c"


@pytest.fixture(scope="module")
def passed_report():
    return run_sum(lower(parse_source(KERNEL)), compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))


def test_difftest_help(capsys):
    assert main(["difftest", "--help"]) == 0
    output = capsys.readouterr()
    for text in ("1000", "sum", "--seed", "--clang", "--timeout-ms", "--json", "Example:"):
        assert text in output.out
    assert not output.err


@pytest.mark.integration
def test_difftest_real_text(capsys):
    assert main(["difftest", str(KERNEL)]) == 0
    output = capsys.readouterr()
    assert "1000/1000 agreed; 1000 planned; passed; seed=0" in output.out
    assert "array element" in output.out and not output.err


@pytest.mark.integration
def test_difftest_real_json_preserves_input_and_leaves_no_artifacts(tmp_path, capsys):
    source = tmp_path / "kernel.c"
    source.write_text(KERNEL.read_text().replace('#include "kernel.h"', ""))
    before = source.read_bytes()
    assert main(["difftest", str(source), "--seed", "17", "--json"]) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["status"] == "passed" and data["agreed"] == data["compared"] == data["cases"] == 1000
    assert data["seed"] == 17 and data["decisions"][0]["removed"]
    assert data["dataset_id"] == "2e65859bf0aaefdc5807369aa5bd2f36a689486eeec4383e4bf219ea12d38a4a"
    assert data["environment"]["clang"] == data["target"]["compiler"]
    assert source.read_bytes() == before and list(tmp_path.iterdir()) == [source]
    assert not output.err


@pytest.mark.integration
@pytest.mark.parametrize(
    "status", ["mismatch", "proof-failed", "compile-error", "run-error", "timeout", "protocol-error"]
)
def test_difftest_unsuccessful_results_return_one_with_json(status, passed_report, monkeypatch, capsys):
    result = replace(passed_report, status=status, compared=0, agreed=0, problem="injected failure")
    monkeypatch.setattr("pfc.cli.run_sum", lambda *args, **kwargs: result)
    assert main(["difftest", str(KERNEL), "--json"]) == 1
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["status"] == status and data["compared"] == 0 and data["seed"] == 0
    assert not output.err


@pytest.mark.integration
def test_difftest_arguments_override_environment_and_reach_adapter(passed_report, monkeypatch, capsys):
    monkeypatch.setenv(CLANG_ENV_VAR, "/missing/environment-clang")
    seen = {}

    def run(program, **kwargs):
        seen.update(kwargs)
        assert program.functions[0].name == "sum"
        return passed_report

    monkeypatch.setattr("pfc.cli.run_sum", run)
    assert (
        main(
            ["difftest", str(KERNEL), "--clang", "selected-clang", "--seed", str(MAX_DIFF_SEED), "--timeout-ms", "5000"]
        )
        == 0
    )
    assert seen == {"compiler": "selected-clang", "seed": MAX_DIFF_SEED, "timeout_ms": 5000}
    assert not capsys.readouterr().err


@pytest.mark.parametrize("seed", ["-1", str(MAX_DIFF_SEED + 1), "abc"])
def test_difftest_invalid_seed_is_usage_error(seed, capsys):
    assert main(["difftest", str(KERNEL), "--seed", seed]) == 2
    output = capsys.readouterr()
    assert f"0..{MAX_DIFF_SEED}" in output.err and not output.out


@pytest.mark.parametrize("args", [[], [str(KERNEL), "--count", "1"], [str(KERNEL), "--timeout-ms", "0"]])
def test_difftest_usage_errors(args, capsys):
    assert main(["difftest", *args]) == 2
    output = capsys.readouterr()
    assert "error:" in output.err and not output.out


@pytest.mark.parametrize(
    "options,rule",
    [(["--clang", "/missing/clang"], "difftest-toolchain"), (["--cpp", "/missing/cpp"], "preprocess-missing")],
)
def test_difftest_tool_errors_are_json(options, rule, capsys):
    assert main(["difftest", str(KERNEL), *options, "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["rule"] == rule and not output.out


def test_difftest_rejects_other_programs_and_missing_sources(tmp_path, capsys):
    source = tmp_path / "other.c"
    assert main(["difftest", str(source), "--json"]) == 1
    assert json.loads(capsys.readouterr().err)["error"]["rule"] == "source-read"
    source.write_text("long f(long n) { return n; }")
    assert main(["difftest", str(source), "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["rule"] == "difftest-profile" and not output.out
