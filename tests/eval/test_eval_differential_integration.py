"""E3 integration with real clang and Z3: six kernels, sealed evidence, deterministic reruns."""

import json
import os
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import differential, evidence, generate
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")


def small_profiles(directory: Path, cases: int = 40) -> Path:
    """Committed profiles shrunk for the suite: same structure, lengths <= 64, fewer cases."""
    directory.mkdir()
    for name in KERNELS:
        profile = generate.load_profile(ROOT / "pfc_eval" / "profiles" / f"{name}.json")
        arrays = {
            k: generate.ArraySpec(1, 64, max(v.low, -100), min(v.high, 100), v.sorted)
            for k, v in profile.arrays.items()
        }
        small = generate.Profile(
            profile.kernel,
            profile.function,
            profile.program_sha256,
            cases,
            arrays,
            profile.scalars,
            profile.boundary,
            profile.stress,
        )
        (directory / f"{name}.json").write_text(generate.dump_profile(small), encoding="utf-8")
    return directory


@pytest.mark.integration
@pytest.mark.parametrize("name", KERNELS)
def test_three_builds_agree_with_the_interpreter_on_small_profiles(name, tmp_path):
    target = read_target(COMPILER)
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    profiles = small_profiles(tmp_path / "profiles")
    profile = generate.load_profile(profiles / f"{name}.json")
    result = differential.run_kernel(program, profile, compiler=COMPILER, target=target, seed=5, timeout_ms=10000)
    assert result.passed, result.problem
    assert result.failures == 0 and len(result.cases) == 40
    assert all(
        b.compiled.status == "ok" and b.executed is not None and b.executed.status == "ok" for b in result.builds
    )
    again = differential.run_kernel(program, profile, compiler=COMPILER, target=target, seed=5, timeout_ms=10000)
    assert again.generated == result.generated


@pytest.mark.integration
def test_cli_differential_seals_evidence_and_reruns_identically(tmp_path, monkeypatch):
    profiles = small_profiles(tmp_path / "profiles", cases=12)
    outputs = []
    for run in ("first", "second"):
        output = tmp_path / run
        code = main(
            [
                "differential",
                str(ROOT / "kernels" / "sum"),
                str(ROOT / "kernels" / "prefix_sum"),
                "--output",
                str(output),
                "--seed",
                "7",
                "--profile",
                str(profiles),
                "--clang",
                COMPILER,
            ]
        )
        assert code == 0
        assert evidence.status(output).complete
        outputs.append(evidence.load(output))
    rows = [o.observations for o in outputs]
    volatile = {"timestamp", "evidence_dir"}
    assert [{k: v for k, v in r.items() if k not in volatile} for r in rows[0]] == [
        {k: v for k, v in r.items() if k not in volatile} for r in rows[1]
    ]
    assert {r["kernel"] for r in rows[0]} == {"sum", "prefix_sum"}
    assert all(r["accepted"] is True for r in rows[0] if r["agreement"] is True)
    summary = json.loads((tmp_path / "first" / "summary.json").read_text(encoding="utf-8"))
    assert summary["kernels"]["sum"]["accepted"] == 12 and summary["kernels"]["sum"]["failures"] == 0
    assert (tmp_path / "first" / "tables.md").exists() and (tmp_path / "first" / "sources" / "sum-verified.c").exists()
    configuration = json.loads((tmp_path / "first" / "configuration.json").read_text(encoding="utf-8"))
    assert configuration["settings"]["seed"] == 7 and "sum" in configuration["settings"]["profile_digests"]
    environment = json.loads((tmp_path / "first" / "environment.json").read_text(encoding="utf-8"))
    assert environment["inventories"]["protected"]["verified"] is True


@pytest.mark.integration
def test_cli_dry_run_writes_nothing_and_existing_output_is_refused(tmp_path, capsys):
    profiles = small_profiles(tmp_path / "profiles", cases=3)
    output = tmp_path / "preview"
    assert (
        main(
            [
                "differential",
                str(ROOT / "kernels" / "sum"),
                "--output",
                str(output),
                "--dry-run",
                "--profile",
                str(profiles),
            ]
        )
        == 0
    )
    assert not output.exists()
    out = capsys.readouterr().out
    assert "sum" in out and "3" in out and "no files" in out.lower()
    output.mkdir()
    assert (
        main(["differential", str(ROOT / "kernels" / "sum"), "--output", str(output), "--profile", str(profiles)]) == 1
    )
    assert "exists" in capsys.readouterr().err
