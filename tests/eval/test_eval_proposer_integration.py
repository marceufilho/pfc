"""Zero-request replay of the archived curated transcripts, a test-mode campaign and the CLI preview (roadmap E10)."""

import json
from pathlib import Path

import pytest

from pfc_eval import adapters, evidence, proposer
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "bench/proposals/g2-astra-20261105"
pytestmark = pytest.mark.integration


def test_test_mode_campaign_records_full_transcripts_and_the_base_url(tmp_path):
    replies = iter(['{"invariants":["i < n"]}', '{"invariants":["0 <= i && i <= n"]}'])

    def fake(request):
        return adapters.Exchange(next(replies), request.settings.model, "resp", 20, 10, 2, 5.0, "test")

    output = tmp_path / "l3"
    passed = proposer.run(
        [ROOT / "kernels/sum"],
        output,
        compiler="clang-19",
        runs=1,
        live=True,
        condition="L3",
        base_url="http://localhost:8000/v1",
        model="local-model",
        effort=None,
        temperature=0.7,
        send=fake,
        progress=lambda text: None,
    )
    assert passed
    loaded = evidence.load(output)
    assert [r["rejection_class"] for r in loaded.observations] == ["not_inductive", "success"]
    assert all(r["base_url"] == "http://localhost:8000/v1" and r["condition"] == "L3" for r in loaded.observations)
    configuration = loaded.configuration["settings"]
    assert configuration["service"]["base_url"] == "http://localhost:8000/v1" and "key" not in json.dumps(configuration)
    lines = (output / "logs" / "proposals.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["accepted"] is True
    assert json.loads((output / "summary.json").read_text())["requests"] == 2


def test_cli_dry_run_previews_tasks_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "dry-run-only")
    code = main(["proposer", str(ROOT / "kernels"), "--output", "out", "--condition", "L2", "--live", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "sum: 1 loop(s)" in out and "L2" in out and "No files were written." in out
    assert not (tmp_path / "out").exists()
    monkeypatch.delenv("OPENAI_API_KEY")
    assert (
        main(["proposer", str(ROOT / "kernels"), "--output", "out2", "--condition", "L1", "--live", "--dry-run"]) == 1
    )
    assert (
        main(
            [
                "proposer",
                str(ROOT / "kernels"),
                "--output",
                "o3",
                "--condition",
                "L1",
                "--live",
                "--base-url",
                "http://x/v1",
            ]
        )
        == 1
    )
