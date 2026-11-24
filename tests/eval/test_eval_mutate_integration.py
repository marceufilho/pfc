"""Real isolated copies, real layers and the sealed CLI preview for the mutation runner (roadmap E9)."""

import hashlib
import sys
from pathlib import Path

import pytest

from pfc_eval import mutate
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_isolated_copy_of_the_repository_leaves_every_trusted_file_untouched(tmp_path):
    before = {name: _digest(ROOT / name) for name in mutate.COMPILER_TARGETS + mutate.HARNESS_TARGETS}
    mutant = mutate.generate(ROOT, ("pfc/wp.py",), seed=0, per_file=1)[0]
    copy = mutate.isolated_copy(ROOT, tmp_path / "copy", mutant)
    assert (copy / "pfc" / "wp.py").read_text(encoding="utf-8") == mutant.mutated_source
    assert (copy / "include" / "spec.h").is_file() and (copy / "tools" / "fake_libc" / "stdbool.h").is_file()
    assert (copy / "kernels" / "sum" / "kernel.c").is_file() and (copy / "tests" / "golden").is_dir()
    assert not (copy / ".venv").exists()
    assert (copy / "heldout").is_symlink()  # read-only inputs are linked, never copied
    assert {name: _digest(ROOT / name) for name in before} == before


def test_golden_layer_kills_the_off_by_one_hand_mutant_in_an_isolated_copy(tmp_path):
    source = (ROOT / "pfc/wp.py").read_text(encoding="utf-8")
    original = 'op("<", index_value, state.lengths[expression.array.id])'
    line = next(i for i, text in enumerate(source.splitlines(), 1) if original in text)
    site = next(
        s
        for s in mutate.sites(source)
        if s.operator == "relational_boundary" and s.line == line and s.replacement.startswith('"<="')
    )
    mutant = mutate.Mutant(
        "wp-hand",
        "pfc/wp.py",
        site.operator,
        site.line,
        f"{site.original} -> {site.replacement}",
        mutate.diff("pfc/wp.py", source, mutate.apply(source, site)),
        mutate.apply(source, site),
    )
    copy = mutate.isolated_copy(ROOT, tmp_path / "copy", mutant)
    result = mutate.run_layer(copy, mutate.COMPILER_LAYERS[1], python=sys.executable, timeout_s=300)
    assert result.status == "killed" and result.seconds > 0 and "failed" in result.detail


def test_cli_dry_run_lists_mutants_and_layers_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["mutate", "--output", "out", "--dry-run", "--seed", "0"]) == 0
    out = capsys.readouterr().out
    assert "pfc/wp.py" in out and "pfc_eval/stats.py" in out and "unit -> golden -> differential" in out
    assert "No files were written." in out and not (tmp_path / "out").exists()
