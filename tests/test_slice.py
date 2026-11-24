"""Gate aggregation, real trapping binaries, mutation controls and exclusive artifacts."""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from pfc import slice as gate
from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.difftest import ProcessResult
from pfc.lower import lower
from pfc.parse import parse_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def program():
    return lower(parse_source(ROOT / "kernels/sum/kernel.c"))


@pytest.fixture(scope="module")
def report(program):
    return gate.run(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))


@pytest.mark.integration
def test_real_gate_has_breakit_differential_mutation_and_both_traps(report):
    assert report.passed and report.breakit.passed
    assert report.differential.status == "passed"
    assert report.differential.compared == report.differential.agreed == 1000
    assert report.mutation.baseline_retained and report.mutation.mutant_detected
    assert report.mutation.original_c != report.mutation.mutant_c
    assert {r.name for r in report.negative} == {"original-checked", "without-requires-verified"}
    assert all(r.passed and r.compilation.status == "ok" and r.execution.returncode < 0 for r in report.negative)
    assert report.breakit.original.proof.context_id == report.differential.context_id


@pytest.mark.parametrize("damage", ["baseline", "mutant", "negative", "differential", "breakit", "context", "code"])
def test_each_gate_evidence_is_required(report, damage):
    altered = report
    if damage == "baseline":
        altered = replace(report, mutation=replace(report.mutation, baseline_retained=False))
    elif damage == "mutant":
        altered = replace(report, mutation=replace(report.mutation, mutant_detected=False))
    elif damage == "negative":
        altered = replace(report, negative=())
    elif damage == "differential":
        altered = replace(report, differential=replace(report.differential, agreed=999))
    elif damage == "breakit":
        altered = replace(report, breakit=replace(report.breakit, passed=False))
    elif damage == "context":
        altered = replace(report, differential=replace(report.differential, context_id="stale"))
    else:
        altered = replace(report, differential=replace(report.differential, verified_c_id="stale"))
    assert not altered.passed


@pytest.mark.parametrize(
    "phase,outcome",
    [
        ("compile", ProcessResult("error", "", 1, "compile failed")),
        ("compile", ProcessResult("timeout", "", None, "timeout")),
        ("run", ProcessResult("ok", "", 0, "")),
        ("run", ProcessResult("error", "", 2, "driver setup failed")),
        ("run", ProcessResult("error", "", -11, "segmentation fault")),
        ("run", ProcessResult("timeout", "", None, "timeout")),
    ],
)
def test_negative_probe_never_accepts_unrelated_failure(report, phase, outcome):
    directories = []

    def runner(args, cwd):
        directories.append(cwd)
        return outcome if ("-o" in args) == (phase == "compile") else ProcessResult("ok", "", 0, "")

    result = gate.probe_trap("test", report.breakit.altered.verified.c, compiler="clang", runner=runner)
    assert not result.passed
    assert all(not d.exists() for d in directories)


def test_missing_guard_skips_negative_execution(report):
    def never(*args):
        pytest.fail("must not execute C without the expected restored guard")

    result = gate.probe_trap("test", report.breakit.original.verified.c, compiler="clang", runner=never)
    assert not result.passed and result.compilation is result.execution is None


def test_artifacts_are_complete_hashed_and_do_not_overwrite(report, tmp_path):
    output = tmp_path / "evidence"
    gate.save(report, output)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["gate"] == "G0" and manifest["passed"] is True
    assert {
        "original.json",
        "without-requires.json",
        "differential.json",
        "mutation.json",
        "negative.json",
        "original.c",
        "without-requires.c",
        "original-checked.c",
        "original-verified.c",
        "without-requires-verified.c",
        "no-proof.c",
        "delete-mutant.c",
        "negative-driver.c",
    } <= set(manifest["files"])
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        gate.save(report, output)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
    original = json.loads((output / "original.json").read_text())
    altered = json.loads((output / "without-requires.json").read_text())
    assert original["program"]["functions"][0]["contracts"] and not altered["program"]["functions"][0]["contracts"]
    assert original["source_id"] == hashlib.sha256((output / "original.c").read_bytes()).hexdigest()


def test_failure_is_saved_as_failure_not_success(report, tmp_path):
    failed = replace(report, negative=())
    gate.save(failed, tmp_path / "failed")
    assert json.loads((tmp_path / "failed" / "manifest.json").read_text())["passed"] is False


def test_existing_file_or_symlink_is_preserved(report, tmp_path):
    file = tmp_path / "file"
    file.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "absent")
    for output in (file, link):
        with pytest.raises(FileExistsError):
            gate.save(report, output)
    assert file.read_text() == "keep" and link.is_symlink() and not link.exists()


def test_trap_evidence_must_match_emitted_sources(report):
    altered = replace(report, negative=(replace(report.negative[0], source_id="stale"), report.negative[1]))
    assert not altered.passed


def test_partial_write_never_has_completion_manifest(report, tmp_path, monkeypatch):
    original = Path.open

    def fail(path, *args, **kwargs):
        if path.name == "without-requires.json":
            raise OSError("simulated write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    output = tmp_path / "partial"
    with pytest.raises(OSError, match="simulated"):
        gate.save(report, output)
    assert (output / "original.json").exists()
    assert not (output / "manifest.json").exists()


@pytest.mark.integration
def test_real_gate_detects_emitter_deletion_without_trusting_metadata(program, monkeypatch):
    from pfc import emit as emission

    original = emission._Emitter.guard

    def mutant(self, *args, **kwargs):
        start = len(self.lines)
        original(self, *args, **kwargs)
        del self.lines[start:]

    monkeypatch.setattr(emission._Emitter, "guard", mutant)
    report = gate.run(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    assert not report.passed and not report.mutation.baseline_retained
    assert all(r.execution is None for r in report.negative)
