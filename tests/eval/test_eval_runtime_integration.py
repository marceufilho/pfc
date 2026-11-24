"""Real builds and timed runs on one kernel; interleaving verified from the recorded timestamps (roadmap E11)."""

from pathlib import Path

import pytest

from pfc_eval import evidence, runtime
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


def test_one_kernel_small_schedule_records_interleaved_runs_with_timestamps(tmp_path):
    output = tmp_path / "runtime"
    passed = runtime.run(
        [ROOT / "kernels/sum"],
        output,
        compiler="clang-19",
        elements=4096,
        repetitions=2,
        blocks=2,
        runs=2,
        warmups=1,
        progress=lambda text: None,
    )
    assert passed and evidence.status(output).complete
    loaded = evidence.load(output)
    rows = sorted(loaded.observations, key=lambda r: r["started"])
    timed = [r for r in rows if not r["warmup"]]
    warm = [r for r in rows if r["warmup"]]
    assert len(timed) == 2 * 2 * len(runtime.VARIANTS) and len(warm) == len(runtime.VARIANTS)
    assert all(r["exit_code"] == 0 and r["wall_ms"] > 0 for r in rows)
    assert max(r["started"] for r in warm) < min(r["started"] for r in timed)  # warm-ups come first
    labels = [v.label for v in runtime.VARIANTS]
    for index in range(0, len(timed), len(labels)):
        cycle = timed[index : index + len(labels)]
        assert [r["variant"] for r in cycle] == labels  # A B C … inside every run, in time order
        assert len({(r["block"], r["run"]) for r in cycle}) == 1
    assert {r["counter_tool"] for r in rows} <= {"perf", "cachegrind", "unavailable"}
    assert loaded.configuration["settings"]["governor"] and loaded.configuration["settings"]["label"] in (
        "controlled",
        "exploratory",
    )
    assert (output / "builds" / "sum" / "verified" / "vectorization.txt").is_file()
    assert not list(output.rglob("program"))  # no binary enters the evidence
    summary = (output / "summary.json").read_text()
    assert '"verified/plain"' in summary and '"medians_ms"' in summary


def test_cli_dry_run_lists_the_full_schedule_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main(["runtime", str(ROOT / "kernels"), "--output", "out", "--dry-run", "--blocks", "1", "--runs", "2"])
    assert code == 0
    out = capsys.readouterr().out
    assert "block 1 run 2: sum without-assume" in out and "governor" in out and "No files were written." in out
    assert out.count("block 1 run 1: sum ") == len(runtime.VARIANTS) and not (tmp_path / "out").exists()
