import json
from pathlib import Path

import pytest

from pfc.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_ir_help(capsys):
    assert main(["ir", "--help"]) == 0
    output = capsys.readouterr()
    assert "Example:" in output.out and "unverified" in output.out
    assert not output.err


@pytest.mark.integration
def test_ir_prints_c_and_leaves_source_unchanged(capsys):
    source = ROOT / "kernels/sum/kernel.c"
    before = source.read_bytes()
    assert main(["ir", str(source)]) == 0
    output = capsys.readouterr()
    assert output.out == (ROOT / "tests/golden/sum-ir.c").read_text()
    assert not output.err and source.read_bytes() == before


@pytest.mark.integration
def test_ir_json_has_node_tags_types_lengths_and_no_proof(capsys):
    assert main(["ir", str(ROOT / "kernels/sum/kernel.c"), "--json"]) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["verification"] == "not-run"
    function = data["functions"][0]
    array, length, _ = function["parameters"]
    assert array["length"]["id"] == length["symbol"]["id"]
    assert array["symbol"]["type"]["element"] == "long"
    assert function["body"]["statements"][2]["node"] == "While"
    assert not output.err


@pytest.mark.integration
def test_ir_reports_lowering_error_as_json(tmp_path, capsys):
    source = tmp_path / "bad.c"
    source.write_text("long f(void) { return unknown; }")
    assert main(["ir", str(source), "--json"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error"]["rule"] == "ir-name"
    assert "Traceback" not in output.err


def test_ir_missing_file_and_argument(capsys, tmp_path):
    assert main(["ir"]) == 2
    capsys.readouterr()
    assert main(["ir", str(tmp_path / "missing.c")]) == 1
    output = capsys.readouterr()
    assert not output.out and "source-read" in output.err


@pytest.mark.integration
def test_ir_deep_conversions_do_not_escape_as_a_traceback(tmp_path, capsys):
    expression = " < ".join(["n"] * 180)
    source = tmp_path / "deep.c"
    source.write_text(f"long f(long n) {{ return {expression}; }}")
    status = main(["ir", str(source), "--json"])
    output = capsys.readouterr()
    assert status == 0
    assert json.loads(output.out)["verification"] == "not-run"
    assert "Traceback" not in output.err
