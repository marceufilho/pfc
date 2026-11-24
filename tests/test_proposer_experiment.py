import json
import os
from pathlib import Path

from pfc.cli import main

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    "sum": ["0<=i && i<=n"],
    "dot": ["0<=i && i<=n"],
    "saxpy": ["0<=i && i<=n"],
    "prefix_sum": ["1<=i && i<=n"],
    "matvec": ["0<=i && i<=rows", "0<=j && j<=cols"],
    "binary_search": ["0<=lo && lo<=hi+1 && hi<n"],
}


def test_three_run_experiment_replay_and_report_do_not_fabricate_g2(tmp_path, capsys):
    from pfc.propose.experiment import run
    from pfc.propose.llm import Exchange

    sent = []

    def send(request):
        sent.append(request.sample_id)
        name = request.sample_id.split("/")[-2]
        return Exchange(
            json.dumps({"invariants": CANDIDATES[name]}), request.settings.model, f"test-{len(sent)}", 10, 10, "test"
        )

    output = tmp_path / "experiment"
    result = run((ROOT / "kernels",), output, compiler=os.environ.get("PFC_CLANG", "clang-19"), live=True, send=send)
    assert result.successes == (6, 6, 6) and result.variance == 0
    assert len(sent) == len(set(sent)) == 18
    assert result.mode == "test" and not result.g2_passed and result.criterion_met
    assert all(row.wrong_rejected for row in result.rows)
    assert main(["report", str(output)]) == 0
    table = capsys.readouterr().out
    assert "proposed" in table and "rejected" in table and "G2: not-run" in table
    replay = run(
        (ROOT / "kernels",), tmp_path / "replay", compiler=os.environ.get("PFC_CLANG", "clang-19"), replay=output
    )
    assert replay.mode == "replay" and replay.successes == (6, 6, 6) and not replay.g2_passed
    assert len(sent) == 18


def test_propose_is_private_by_default_and_preview_needs_no_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    output = tmp_path / "new"
    arguments = ["propose", str(ROOT / "kernels"), "--output", str(output)]
    assert main(arguments + ["--dry-run", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["max_requests"] == 90 and data["runs"] == 3 and not data["key_configured"]
    assert main(arguments) == 2
    assert not output.exists()
    assert main(arguments + ["--live", "--json"]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().err and not output.exists()


def test_wrong_source_profile_is_rejected_before_service_use(tmp_path):
    from pfc.propose.experiment import run

    source = tmp_path / "source"
    source.mkdir()
    (source / "kernel.c").write_text("long sum(long n){return n;}")
    calls = []
    import pytest

    with pytest.raises(Exception, match="profile"):
        run(
            (source,),
            tmp_path / "output",
            compiler=os.environ.get("PFC_CLANG", "clang-19"),
            live=True,
            send=lambda request: calls.append(request),
        )
    assert not calls and not (tmp_path / "output").exists()


def test_all_service_failures_use_at_most_ninety_requests_and_remain_visible(tmp_path):
    from pfc.propose.experiment import load, run

    calls = []

    def unavailable(request):
        calls.append(request.sample_id)
        raise ValueError("OpenAI request failed (test outage)")

    output = tmp_path / "outage"
    result = run(
        (ROOT / "kernels",), output, compiler=os.environ.get("PFC_CLANG", "clang-19"), live=True, send=unavailable
    )
    assert len(calls) == len(set(calls)) == 90
    assert result.successes == (0, 0, 0) and not result.g2_passed
    assert all(r.errors == 5 and not r.accepted and not r.rejected for r in result.rows)
    assert load(output) == result


def test_report_rejects_malformed_experiment_without_traceback(tmp_path, capsys):
    import pytest

    from pfc import report
    from pfc.propose.experiment import load

    directory = tmp_path / "report"
    directory.mkdir()
    (directory / "experiment.json").write_text(json.dumps({"schema_version": 1, "mode": []}))
    summary = report.Summary("propose", (report.row("sum", None, passed=False),), False)
    report.seal(directory, summary)
    with pytest.raises(ValueError):
        load(directory)
    assert main(["report", str(directory), "--json"]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_replayed_exchanges_can_never_count_as_independent_sampling(tmp_path, monkeypatch):
    from pfc.propose import experiment
    from pfc.propose.llm import Exchange

    def replayed(request):
        name = request.sample_id.split("/")[-2]
        return Exchange(
            json.dumps({"invariants": CANDIDATES[name]}), request.settings.model, "old-response", 0, 0, "replay"
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(experiment, "send_request", replayed)
    result = experiment.run(
        (ROOT / "kernels",),
        tmp_path / "replayed",
        compiler=os.environ.get("PFC_CLANG", "clang-19"),
        live=True,
        send=replayed,
    )
    assert result.criterion_met and not result.g2_passed and result.mode == "replay"
