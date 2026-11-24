import json
from pathlib import Path

import pytest

from pfc.cli import main

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "kernels/sum/kernel.c"


def test_vc_help(capsys):
    assert main(["vc", "--help"]) == 0
    output = capsys.readouterr()
    assert "Example:" in output.out and "solver" in output.out
    assert not output.err


@pytest.mark.integration
def test_vc_sum_text(capsys):
    assert main(["vc", str(KERNEL)]) == 0
    output = capsys.readouterr()
    assert "3 required, 2 reported (not-run)" in output.out
    assert "No solver was run" in output.out
    assert "kernel.c:12:" in output.out
    assert "goal: true" not in output.out
    assert not output.err


@pytest.mark.integration
def test_vc_sum_json_and_input_unchanged(capsys):
    before = KERNEL.read_bytes()
    assert main(["vc", str(KERNEL), "--json"]) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["verification"] == "not-run"
    assert data["function"] == "sum" and len(data["obligations"]) == 5
    assert sum(item["category"] == "required" for item in data["obligations"]) == 3
    bounds = data["obligations"][2]
    assert bounds["goal"]["node"] == "Operation"
    assert len(bounds["dependencies"]) == 2
    assert all(item["status"] == "not-run" for item in data["obligations"])
    assert KERNEL.read_bytes() == before
    assert not output.err


@pytest.mark.integration
def test_vc_missing_invariant_is_failure_with_json(tmp_path, capsys):
    source = tmp_path / "bad.c"
    source.write_text("long f(long n) { long i=0; while(i<n) { i=i+1; } return i; }")
    assert main(["vc", str(source), "--json"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error"]["rule"] == "vc-invariant"
    assert "Traceback" not in output.err


def test_vc_usage_and_missing_file(capsys, tmp_path):
    assert main(["vc"]) == 2
    capsys.readouterr()
    assert main(["vc", str(tmp_path / "missing.c"), "--json"]) == 1
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["error"]["rule"] == "source-read"
