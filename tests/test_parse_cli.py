import json
from pathlib import Path

import pytest

from pfc.cli import main

KERNEL = Path(__file__).resolve().parents[1] / "kernels/sum/kernel.c"


def test_parse_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse", "--help"]) == 0
    assert "Example:" in capsys.readouterr().out


@pytest.mark.integration
def test_parse_sum_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse", str(KERNEL)]) == 0
    captured = capsys.readouterr()
    assert "1 REQUIRES" in captured.out and "1 INVARIANT" in captured.out
    assert "1 index site" in captured.out and "0 divisions" in captured.out
    assert "kernel.c:12:" in captured.out
    assert "no verification" in captured.out
    assert not captured.err


@pytest.mark.integration
def test_parse_sum_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse", str(KERNEL), "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["functions"] == ["sum"]
    assert [item["kind"] for item in report["contracts"]] == ["REQUIRES", "INVARIANT"]
    assert report["sites"][0]["location"]["line"] == 12
    assert report["verification"] == "not-run"
    assert not captured.err


def test_parse_failure_json_is_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse", str(tmp_path / "missing.c"), "--json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"]["rule"] == "source-read"


def test_missing_parse_argument_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse"]) == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.integration
def test_parse_syntax_error_is_concise(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.c"
    path.write_text("long f(void) { return 0 private_payload; }")
    assert main(["parse", str(path)]) == 1
    captured = capsys.readouterr()
    assert "parse-syntax" in captured.err and "bad.c:1" in captured.err
    assert "private_payload" not in captured.err and "Traceback" not in captured.err
    assert not captured.out
