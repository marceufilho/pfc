"""E4 integration with Z3 and clang: two-conjunct contract, six kernels, validation-v4 consistency, CLI."""

import json
import os
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import conjuncts, evidence
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")


@pytest.fixture(scope="module")
def target():
    return read_target(COMPILER)


@pytest.mark.integration
def test_two_conjunct_contract_dependent_and_independent(tmp_path, target):
    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n\nlong pick(const long a[], long a_len, long k)\n{\n'
        "    REQUIRES(0 <= k && k < a_len && a_len <= 100);\n    return a[k];\n}\n",
        encoding="utf-8",
    )
    result = conjuncts.run_kernel(lower(parse_source(path)), target=target, timeout_ms=10000, seed=0)
    by_text = {a.conjunct.text: a for a in result.ablations}
    assert set(by_text) == {"(0 <= k)", "(k < a_len)", "(a_len <= 100)"}
    for text in ("(0 <= k)", "(k < a_len)"):
        ablation = by_text[text]
        assert ablation.expected == "guard" and ablation.search.witness.location.line == 6
        assert ablation.proof_lost and ablation.guard_restored and ablation.witness_site_restored
        assert not ablation.c_identical and ablation.as_expected
    independent = by_text["(a_len <= 100)"]
    assert independent.expected == "none" and independent.search.witness is None
    assert independent.verdicts_identical and independent.c_identical and not independent.guard_restored
    assert independent.as_expected
    assert result.sensitivity == (2, 2) and result.specificity == (1, 1) and result.separated == (0, 0)
    assert result.anomalies == 0


@pytest.mark.integration
@pytest.mark.parametrize("name", KERNELS)
def test_six_kernels_outcomes_match_their_witness_classes(name, target):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    result = conjuncts.run_kernel(program, target=target, timeout_ms=10000, seed=0)
    assert result.original_required_passed and result.anomalies == 0
    for ablation in result.ablations:
        assert ablation.as_expected, (ablation.conjunct.text, ablation.expected)
        if ablation.expected == "restrict":
            assert ablation.restrict_withdrawn and not ablation.guard_restored and not ablation.proof_lost
    assert result.sensitivity[0] == result.sensitivity[1]
    assert result.specificity[0] == result.specificity[1]


@pytest.mark.integration
def test_cli_conjuncts_seals_table_rows_and_dry_run_writes_nothing(tmp_path, capsys):
    output = tmp_path / "conjuncts"
    assert (
        main(["conjuncts", str(ROOT / "kernels" / "sum"), "--output", str(output), "--dry-run", "--clang", COMPILER])
        == 0
    )
    assert not output.exists() and "no files" in capsys.readouterr().out.lower()
    assert (
        main(
            [
                "conjuncts",
                str(ROOT / "kernels" / "sum"),
                str(ROOT / "kernels" / "saxpy"),
                "--output",
                str(output),
                "--clang",
                COMPILER,
            ]
        )
        == 0
    )
    assert evidence.status(output).complete
    rows = evidence.load(output).observations
    assert {(r["kernel"], r["conjunct_text"]) for r in rows} == {
        ("sum", "(0 <= n)"),
        ("sum", "(n <= a_len)"),
        ("saxpy", "(0 <= n)"),
        ("saxpy", "(n <= x_len)"),
        ("saxpy", "(n <= y_len)"),
        ("saxpy", "SEPARATED(x, x_len, y, y_len)"),
    }
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["kernels"]["saxpy"]["separated"] == {"withdrawn": 1, "total": 1}
    assert (output / "tables.md").exists() and (output / "sources" / "sum-0-1-verified.c").exists()
