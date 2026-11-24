"""Shared campaign scaffolding: one place for provenance, toolchain capture, sealing and CLI errors."""

import json
from pathlib import Path

import pytest

from pfc.environment import Environment
from pfc.target import Target
from pfc_eval import campaign, evidence

SCHEMA = evidence.Schema((evidence.Column("kernel", "string"), evidence.Column("ok", "boolean")))
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


@pytest.fixture
def toolchain(monkeypatch):
    record = Environment("3.12", "5.3", "clang version 19", "x86_64", "cpu", "Linux", ("-std=c11",))
    monkeypatch.setattr(campaign, "read_target", lambda compiler: T64)
    monkeypatch.setattr(campaign, "collect_environment", lambda compiler: record)
    monkeypatch.setattr(campaign, "resolve_compiler", lambda compiler: "/usr/bin/" + compiler)
    return record


def test_start_and_finish_produce_a_sealed_directory_with_shared_provenance(tmp_path, toolchain):
    run = campaign.start(
        "demo", tmp_path / "out", compiler="clang-19", cpp="cpp", seed=4, settings={"draws": 3}, schema=SCHEMA
    )
    assert run.target == T64 and run.toolchain is toolchain
    run.evidence.record("row", "sum", "0", {"kernel": "sum", "ok": True})
    campaign.finish(run, {"experiment": "demo", "passed": True}, "| t |\n", lambda records: [r.data for r in records])
    assert evidence.status(tmp_path / "out").complete
    configuration = json.loads((tmp_path / "out" / "configuration.json").read_text(encoding="utf-8"))
    assert configuration["experiment"] == "demo" and configuration["settings"]["draws"] == 3
    assert configuration["settings"]["compiler"] == "/usr/bin/clang-19" and configuration["settings"]["seed"] == 4
    assert configuration["provenance"]["clang"] == "clang version 19" and configuration["provenance"]["seed"] == 4
    environment = json.loads((tmp_path / "out" / "environment.json").read_text(encoding="utf-8"))
    assert set(environment["inventories"]) == {"protected", "baseline"} and environment["commit"]
    rows = evidence.load(tmp_path / "out").observations
    assert rows[0]["kernel"] == "sum" and rows[0]["experiment"] == "demo"
    assert (tmp_path / "out" / "summary.json").exists() and (tmp_path / "out" / "tables.md").exists()


def test_start_refuses_an_existing_destination(tmp_path, toolchain):
    (tmp_path / "taken").mkdir()
    with pytest.raises(FileExistsError):
        campaign.start("demo", tmp_path / "taken", compiler="clang-19", cpp="cpp", seed=0, settings={}, schema=SCHEMA)


def test_command_wrapper_maps_errors_to_exit_codes(capsys):
    def exists(**_: object) -> bool:
        raise FileExistsError("evidence directory x exists")

    def passes(**_: object) -> bool:
        return True

    def fails(**_: object) -> bool:
        return False

    def broken(**_: object) -> bool:
        raise ValueError("bad profile")

    namespace = {"clang": None, "cpp": None, "timeout_ms": None, "output": "o", "dry_run": False, "directories": ["k"]}
    assert campaign.run_command(namespace, exists) == 1 and "exists" in capsys.readouterr().err
    assert campaign.run_command(namespace, passes) == 0
    assert campaign.run_command(namespace, fails) == 1
    assert campaign.run_command(namespace, broken) == 1 and "bad profile" in capsys.readouterr().err


def test_command_wrapper_applies_defaults_and_overrides(monkeypatch):
    seen = {}

    def capture(**kwargs: object) -> bool:
        seen.update(kwargs)
        return True

    monkeypatch.delenv("PFC_CLANG", raising=False)
    namespace = {
        "clang": None,
        "cpp": "mycpp",
        "timeout_ms": 500,
        "output": "o",
        "dry_run": True,
        "directories": ["a", "b"],
        "seed": 9,
    }
    assert campaign.run_command(namespace, capture) == 0
    assert seen["compiler"] == "clang-19" and seen["cpp"] == "mycpp" and seen["timeout_ms"] == 500
    assert seen["dry_run"] is True and seen["directories"] == [Path("a"), Path("b")] and seen["seed"] == 9
    assert seen["output"] == Path("o")


def test_command_wrapper_forwards_the_reconcile_directory_as_a_path():
    seen = {}

    def capture(**kwargs: object) -> bool:
        seen.update(kwargs)
        return True

    namespace = {"clang": "cc", "cpp": None, "timeout_ms": None, "output": "o", "dry_run": False, "directories": ["k"]}
    assert campaign.run_command({**namespace, "reconcile": "bench/eval/sealed"}, capture) == 0
    assert seen["reconcile"] == Path("bench/eval/sealed")
    seen.clear()
    assert campaign.run_command({**namespace, "reconcile": None}, capture) == 0
    assert "reconcile" not in seen
