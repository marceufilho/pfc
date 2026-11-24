"""`doctor`: the E14 preflight; every check is a pure function over injected probes (E14b)."""

import json
from pathlib import Path

from pfc_eval import doctor
from pfc_eval.cli import main


def _probes(**overrides):
    base = dict(
        governor=lambda: "performance",
        which=lambda name: f"/usr/bin/{name}",
        image=lambda reference: True,
        environment={"OPENAI_API_KEY": "sk-not-printed"},
        tags=lambda: ("freeze-v4", "eval-v1"),
        inventories=lambda: {"protected": True, "baseline": True},
        heldout=lambda path: (True, "sealed and verified"),
        solver=lambda: "4.13.0",
        sealing=lambda path: {
            "accepted": ["a"],
            "array_kernels": ["a"],
            "profiles": {"a": "0" * 64},
            "drivers": ["a"],
            "guard_sites": {"a": {"bounds": 1, "division": 0, "sites": ["bounds@a.c:1:1"]}},
            "guard_rule_fraction": 1.0,
        },
    )
    base.update(overrides)
    return base


def test_every_check_passes_on_a_prepared_machine_and_the_key_is_never_shown():
    report = doctor.checks(Path("heldout"), **_probes())
    assert all(check.ok for check in report) and len(report) >= 9
    text = doctor.render(report)
    assert "sk-not-printed" not in text and "OPENAI_API_KEY: set" in text
    assert "sk-not-printed" not in json.dumps(doctor.as_json(report))


def test_each_failing_probe_fails_exactly_its_check():
    report = doctor.checks(
        Path("heldout"),
        **_probes(
            governor=lambda: "powersave",
            which=lambda name: None,
            image=lambda reference: False,
            environment={},
            tags=lambda: ("freeze-v4",),
            inventories=lambda: {"protected": True, "baseline": False},
            heldout=lambda path: (False, "HELDOUT.sha256 missing"),
        ),
    )
    failed = {check.name for check in report if not check.ok}
    assert failed == {
        "governor",
        "instruction counter",
        "taskset",
        "frama-c image",
        "OPENAI_API_KEY",
        "tag eval-v1",
        "baseline inventory",
        "held-out set",
        "docker",
    }
    assert doctor.exit_code(report) == 1 and doctor.exit_code([c for c in report if c.ok]) == 0


def test_cli_doctor_runs_without_writing_and_reports_this_machine(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main(["doctor", "--heldout", "heldout", "--json"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert code in (0, 1) and {c["name"] for c in report} >= {"governor", "held-out set", "tag eval-v1"}
    assert list(tmp_path.iterdir()) == []


def test_held_out_rows_read_the_sealing_record(tmp_path):
    record = {
        "accepted": ["a", "b"],
        "profiles": {"a": "0" * 64},
        "drivers": ["a", "b"],
        "guard_sites": {
            "a": {"bounds": 1, "division": 0, "sites": ["bounds@a.c:3:4"]},
            "b": {"bounds": 0, "division": 0, "sites": []},
        },
        "guard_rule_fraction": 0.5,
        "array_kernels": ["a"],
    }
    report = doctor.checks(
        Path("heldout"), **_probes(heldout=lambda path: (True, "sealed"), sealing=lambda path: record)
    )
    by = {c.name: c for c in report}
    assert by["held-out profiles"].ok and by["held-out drivers"].ok and by["held-out guard sites"].ok
    assert "1/2" in by["held-out guard sites"].detail
    short = {**record, "profiles": {}, "drivers": ["a"], "guard_rule_fraction": 0.0}
    report = doctor.checks(
        Path("heldout"), **_probes(heldout=lambda path: (True, "sealed"), sealing=lambda path: short)
    )
    by = {c.name: c for c in report}
    assert not by["held-out profiles"].ok and not by["held-out drivers"].ok and not by["held-out guard sites"].ok
