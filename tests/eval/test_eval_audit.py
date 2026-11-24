"""Audit record and offline replay of a `pfc build` directory (phase 2C): replayable solver evidence, no authority."""

import json
from pathlib import Path

import pytest

from pfc.cli import main as pfc_main
from pfc_eval import audit, evidence
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]


def _build(tmp_path, *kernels):
    output = tmp_path / "build"
    assert (
        pfc_main(
            ["build", *[str(ROOT / "kernels" / k) for k in kernels], "--mode", "verified", "--output", str(output)]
        )
        == 0
    )
    return output


def test_site_map_reads_the_three_trap_shapes_independently_of_the_emitter():
    c = (
        "// entry check 1\n_Bool pfc_entry_1 = (0 <= n);\nif (!pfc_entry_1) __builtin_trap();\n"
        "if (!(0 <= i && i < a_len)) __builtin_trap();\n"
        "// proved: obligation overflow:abc\nlong t = (i + 1);\n"
        "if (__builtin_add_overflow(s, x, &u)) __builtin_trap();\n"
        "// proved: obligation bounds:def\nlong v = a[j];\n"
    )
    found = audit.site_map(c)
    assert found == audit.SiteMap(guards=1, overflow_traps=1, entry_traps=1, citations=("overflow:abc", "bounds:def"))


def test_problems_flag_a_removed_site_without_citation_and_a_count_mismatch():
    decisions = [
        {
            "kind": "bounds",
            "removed": True,
            "obligation_ids": ["bounds:def"],
            "location": {"line": 1, "column": 1, "file": "k.c"},
        },
        {
            "kind": "bounds",
            "removed": False,
            "obligation_ids": ["bounds:xyz"],
            "location": {"line": 2, "column": 1, "file": "k.c"},
        },
    ]
    overflow = [
        {
            "kind": "overflow",
            "removed": True,
            "obligation_ids": ["overflow:abc"],
            "location": {"line": 3, "column": 1, "file": "k.c"},
        }
    ]
    found = audit.SiteMap(guards=1, overflow_traps=0, entry_traps=0, citations=("overflow:abc",))
    problems = audit.problems(found, decisions, overflow, rendered=0)
    assert any("bounds:def" in p and "cit" in p for p in problems) and len(problems) == 1
    clean = audit.problems(audit.SiteMap(1, 0, 0, ("overflow:abc", "bounds:def")), decisions, overflow, rendered=0)
    assert clean == ()
    assert audit.problems(audit.SiteMap(0, 0, 0, ("overflow:abc", "bounds:def")), decisions, overflow, rendered=0)


@pytest.mark.integration
def test_audit_accepts_a_fresh_build_and_refuses_a_tampered_one(tmp_path, monkeypatch):
    build = _build(tmp_path, "sum", "matvec")  # matvec has two overflow operators at one location (ambiguous site_id)
    monkeypatch.chdir(ROOT)
    output = tmp_path / "audit"
    assert main(["audit", str(build), "--output", str(output)]) == 0
    assert evidence.status(output).complete
    rows = evidence.load(output).observations
    assert {r["kernel"] for r in rows} == {"sum", "matvec"} and len(rows) == 3 + 9  # sum: 1 bounds + 2 overflow
    assert all(r["status_agreement"] is True and r["cited_in_c"] is True and r["c_identical"] is True for r in rows)
    assert all(r["emitter"].startswith("emitter-v5") and r["replay_solver_version"] for r in rows)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert (
        summary["passed"] is True
        and summary["status_flips"] == 0
        and "certificate" not in summary["statement"].lower().replace("not a certificate", "")
    )
    kernel = build / "sum" / "kernel.c"
    kernel.write_text(kernel.read_text(encoding="utf-8").replace("__builtin_trap();", "(void)0;", 1), encoding="utf-8")
    assert main(["audit", str(build), "--output", str(tmp_path / "audit2")]) == 1
    assert not (tmp_path / "audit2").exists()


@pytest.mark.integration
def test_audit_reports_a_status_flip_without_granting_or_removing_authority(tmp_path, monkeypatch):
    build = _build(tmp_path, "sum")
    monkeypatch.chdir(ROOT)
    original = audit.smt.check

    def flipped(program, target, **kwargs):
        report = original(program, target, **kwargs)
        results = tuple(
            r.__class__(**{**r.__dict__, "status": "unknown", "reason": "simulated"}) if r.status == "proved" else r
            for r in report.results
        )
        return report.__class__(**{**report.__dict__, "results": results, "required_passed": False})

    monkeypatch.setattr(audit.smt, "check", flipped)
    output = tmp_path / "audit"
    assert main(["audit", str(build), "--output", str(output)]) == 1
    rows = evidence.load(output).observations
    assert any(r["status_agreement"] is False for r in rows)
    assert all(r["recorded_removed"] is True for r in rows if r["kind"] == "bounds")  # recorded, never rewritten
    assert any(r["recorded_removed"] is True and r["replayed_removed"] is False for r in rows)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["status_flips"] >= 1 and summary["passed"] is False
