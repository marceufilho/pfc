"""B2 integration with Z3 and clang: an out-of-family loop admitted from inferred candidates; sealed CLI campaign."""

import json
import os
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import daikon, evidence, houdini
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")


@pytest.fixture(scope="module")
def target():
    return read_target(COMPILER)


@pytest.mark.integration
def test_b2_admits_a_stride_two_loop_whose_invariant_is_outside_b1(tmp_path, target):
    # The hand invariant needs `i == 2 * k`: an equality with a non-unit constant, which B1 cannot express.
    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n\nlong evens(const long a[], long a_len, long n)\n{\n'
        "    REQUIRES(0 <= n && 2 * n <= a_len);\n    long s = 0;\n    long i = 0;\n    long k = 0;\n"
        "    while (k < n) {\n        INVARIANT(0 <= k && k <= n && i == 2 * k && 0 <= i);\n"
        "        s = s + a[i];\n        i = i + 2;\n        k = k + 1;\n    }\n    return s;\n}\n",
        encoding="utf-8",
    )
    program = lower(parse_source(path))
    b1 = houdini.run_kernel(program, target, timeout_ms=10000)
    assert b1.accepted is False  # the template family has no `i == 2 * k`
    inferred = daikon.family(program, target, seed=0)
    assert inferred.states_observed[0] >= daikon.MIN_STATES and "(i == (2 * k))" in inferred.candidates[0]
    b2 = houdini.run_kernel(program, target, timeout_ms=10000, candidates=inferred.candidates)
    assert b2.accepted is True, b2.rejection_class
    assert "(i == (2 * k))" in b2.remaining[0]


@pytest.mark.integration
def test_cli_houdini_b2_seals_rows_with_the_family_and_state_columns(tmp_path, capsys):
    output = tmp_path / "b2"
    kernels = [str(ROOT / "kernels" / "sum"), str(ROOT / "kernels" / "binary_search")]
    assert main(["houdini", *kernels, "--family", "b2", "--output", str(output), "--dry-run", "--clang", COMPILER]) == 0
    assert "B2 candidates come from sampled states" in capsys.readouterr().out and not output.exists()
    code = main(["houdini", *kernels, "--family", "b2", "--seed", "0", "--output", str(output), "--clang", COMPILER])
    loaded = evidence.load(output)
    rows = list(loaded.observations)
    assert rows and all(r["family"] == "b2" and r["states_observed"] >= daikon.MIN_STATES for r in rows)
    assert loaded.configuration["settings"]["family_label"] == "b2"
    assert loaded.configuration["settings"]["b2"]["samples_fuzz"] == daikon.SAMPLES_FUZZ
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["family_label"] == "b2" and summary["family"] == daikon.FAMILY_B2_TEXT
    assert code in (0, 1) and (code == 0) == all(r["task_accepted"] for r in rows)
    assert (output / "sources" / "sum-b2-candidate.c").is_file()
