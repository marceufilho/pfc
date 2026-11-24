"""The defense demo rechecks evidence offline and never claims model sampling."""

import json
import os
from pathlib import Path

import pytest

from pfc import report

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernels/sum/kernel.c"
COMPILER = os.environ.get("PFC_CLANG", "clang-19")


def test_demo_saves_real_gate_and_scripted_rejections_then_replays(tmp_path, monkeypatch):
    from pfc import demo

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = demo.run(SOURCE, tmp_path / "demo", compiler=COMPILER)
    assert result.passed and result.g0_passed and result.replay_passed
    assert result.mode == "scripted" and result.g2 == "not-run"
    assert result.accepted == 1 and result.rejected == 1 and result.attempts == 2
    root = tmp_path / "demo"
    assert report.load(root).passed
    records = [json.loads(line) for line in (root / "logs/proposals.jsonl").read_text().splitlines()]
    assert [record["accepted"] for record in records] == [False, True]
    assert all(record["exchange"]["origin"] == "test" for record in records)
    assert any(r["status"] == "failed" and r["counterexample"] for r in records[0]["proof"]["results"])
    assert "// proved: obligation" in (root / "proposed-verified.c").read_text()
    assert json.loads((root / "g0/manifest.json").read_text())["passed"]
    assert "scripted" in demo.render(result) and "not-run" in demo.render(result)


def test_demo_dry_run_does_not_write_to_requested_output(tmp_path):
    from pfc import demo

    output = tmp_path / "absent"
    result = demo.run(SOURCE, output, compiler=COMPILER, dry_run=True)
    assert result.passed and result.dry_run and not output.exists()


def test_demo_refuses_existing_output_before_running_gate(tmp_path, monkeypatch):
    from pfc import demo

    monkeypatch.setattr(demo.gate, "run", lambda *a, **k: pytest.fail("must refuse before running"))
    with pytest.raises(Exception, match="existing evidence"):
        demo.run(SOURCE, tmp_path, compiler=COMPILER)


def test_demo_gate_failure_is_not_hidden_by_accepted_candidate(tmp_path, monkeypatch):
    from dataclasses import replace

    from pfc import demo

    original = demo.gate.run
    monkeypatch.setattr(demo.gate, "run", lambda *a, **k: replace(original(*a, **k), negative=()))
    result = demo.run(SOURCE, tmp_path / "demo", compiler=COMPILER)
    assert not result.passed and not report.load(tmp_path / "demo").passed
