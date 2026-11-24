"""Independent observations for the small, documented language examples."""

import os
from pathlib import Path

import pytest

from pfc.build import build_source
from pfc.difftest import invoke

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = {
    "01_first": ("7\n", 1),
    "02_clamp": ("0 42 100\n", 0),
    "03_divide": ("7\n", 1),
    "04_count": ("5\n", 0),
    "05_zero": ("3: 0 0 0 4\n", 1),
    "06_calls": ("5\n", 0),
}


@pytest.mark.integration
@pytest.mark.parametrize("name", EXAMPLES)
@pytest.mark.parametrize("mode", ["plain", "checked", "verified"])
def test_example_compiles_and_matches_independent_observation(name, mode, tmp_path):
    directory = ROOT / "examples" / name
    expected, sites = EXAMPLES[name]
    result = build_source(
        directory / "kernel.c",
        tmp_path / "build",
        compiler=os.environ.get("PFC_CLANG", "clang-19"),
        mode=mode,
        driver=directory / "main.c",
    )
    assert result.passed
    execution = invoke((str(tmp_path / "build/program"),), tmp_path)
    assert execution.status == "ok" and execution.stdout == expected
    assert len(result.emission.decisions) == (0 if mode == "plain" else sites)
    assert all(d.removed == (mode == "verified") for d in result.emission.decisions)
    if mode == "verified":
        assert result.emission.proof.required_passed
        assert all(r.status == "proved" for r in result.emission.proof.results)
