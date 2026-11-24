"""E7 integration with Z3: verdict/decision/fact equality across variants of the six kernels, and the CLI."""

import json
import os
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import evidence, robustness
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")


@pytest.fixture(scope="module")
def target():
    return read_target(COMPILER)


@pytest.mark.integration
@pytest.mark.parametrize("name", KERNELS)
def test_variants_keep_verdicts_decisions_and_facts(name, target):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    result = robustness.run_kernel(program, target, timeout_ms=10000, seed=0, count=10, cases=100)
    assert len(result.variants) == 10
    for item in result.variants:
        assert item.verdicts_identical and item.decisions_identical and item.facts_identical, item.variant.detail
        assert item.behavior.divergent == 0 and item.behavior.cases == 100
        if item.variant.kind == "rename":
            assert item.c_identical is True
        else:
            assert item.c_identical is None
    assert result.brittle == 0 and result.divergent == 0


@pytest.mark.integration
def test_cli_robustness_seals_variant_rows_and_sources(tmp_path, capsys):
    output = tmp_path / "robustness"
    assert main(["robustness", str(ROOT / "kernels" / "sum"), "--output", str(output), "--dry-run", "--seed", "0"]) == 0
    assert not output.exists() and "no files" in capsys.readouterr().out.lower()
    assert (
        main(
            [
                "robustness",
                str(ROOT / "kernels" / "sum"),
                "--output",
                str(output),
                "--seed",
                "0",
                "--variants",
                "4",
                "--clang",
                COMPILER,
            ]
        )
        == 0
    )
    assert evidence.status(output).complete
    rows = evidence.load(output).observations
    assert len(rows) == 4 and {r["kernel"] for r in rows} == {"sum"}
    assert all(r["brittle"] is False and r["divergent"] is False for r in rows)
    assert (output / "sources" / "sum-v00.c").exists() and (output / "sources" / "sum-v00-verified.c").exists()
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["kernels"]["sum"]["variants"] == 4 and summary["kernels"]["sum"]["brittle"] == 0
