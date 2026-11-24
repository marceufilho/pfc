"""E5 integration with Z3 and clang: admission of every candidate, reproduction, checked-binary trap, CLI."""

import json
import os
from pathlib import Path

import pytest

from pfc import smt
from pfc.corpus import NEAR_MISS_INVARIANTS
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import evidence, inject
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")


@pytest.fixture(scope="module")
def target():
    return read_target(COMPILER)


@pytest.mark.integration
def test_sum_candidates_fall_into_the_four_classes_and_false_ones_are_rejected(target):
    program = lower(parse_source(ROOT / "kernels" / "sum" / "kernel.c"))
    result = inject.run_kernel(program, "sum", target, compiler=COMPILER, seed=0, timeout_ms=10000)
    by_text = {c.candidate.text: c for c in result.candidates}
    assert by_text["0"].candidate_class == "false" and not by_text["0"].admitted and by_text["0"].model_class == "entry"
    assert by_text["0 <= i && i < n"].candidate_class == "false" and not by_text["0 <= i && i < n"].admitted
    assert by_text["((0 <= i) && (i <= (n + 1)))"].candidate_class == "valid-and-sufficient"
    assert by_text["((0 <= i) && (i <= (n + 1)))"].admitted
    assert by_text["(i <= n)"].candidate_class == "valid-but-insufficient" and not by_text["(i <= n)"].admitted
    assert by_text["((0 <= i) && (i <= (n - 1)))"].candidate_class == "false"
    assert result.false_admitted == 0 and result.false_total >= 5
    assert all(c.reproduction.status == "reproduced" for c in result.candidates if c.model_class == "entry")
    assert result.discrepancies == 0 and result.unreconstructible == 0
    assert result.entry_models >= 1 and result.abstract_models >= 1


@pytest.mark.integration
@pytest.mark.parametrize("name", KERNELS)
def test_six_kernels_reject_every_false_candidate_and_reproduce_entry_models(name, target):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    result = inject.run_kernel(program, name, target, compiler=COMPILER, seed=0, timeout_ms=10000)
    assert result.false_admitted == 0 and result.discrepancies == 0
    controls = [c for c in result.candidates if c.candidate.operator in ("literal_false", "near_miss")]
    # matvec's curated near-miss list repeats the inner hand invariant, so that loop has only the literal-false control.
    assert len(controls) == (3 if name == "matvec" else 2 * len(inject.loops(program.functions[0])))
    assert all(not c.admitted and c.candidate_class == "false" and c.real_model for c in controls)
    assert all(
        c.reproduction.status in ("reproduced", "unreconstructible")
        for c in result.candidates
        if c.model_class == "entry"
    )
    assert result.reproduced + result.unreconstructible == result.entry_models
    if name != "matvec":  # Z3 picks LONG_MAX lengths for matvec's initiation models; recorded, not relabelled.
        assert result.unreconstructible == 0
    assert len(result.candidates) == sum(
        min(20, len(inject.family(program, name, loop, near_miss=NEAR_MISS_INVARIANTS[name])))
        for loop in inject.loops(program.functions[0])
    )
    assert (
        len({c.candidate.text for c in result.candidates}) == len(result.candidates)
        or len(inject.loops(program.functions[0])) > 1
    )


@pytest.mark.integration
def test_entry_state_safety_model_reproduces_through_the_checked_binary_trap(tmp_path, target):
    path = tmp_path / "kernel.c"
    path.write_text(
        '#include "spec.h"\n\nlong pick(const long a[], long a_len, long k)\n{\n    return a[k];\n}\n', encoding="utf-8"
    )
    program = lower(parse_source(path))
    function = program.functions[0]
    proof = smt.check(program, target)
    failed = [r for r in proof.results if r.obligation.kind == "bounds" and r.status == "failed"]
    assert len(failed) == 1 and failed[0].counterexample is not None
    assert inject.model_class(failed[0].counterexample, function, "bounds") == "entry"
    outcome = inject.reproduce(
        program, function, "bounds", failed[0].obligation.location, failed[0].counterexample, target, compiler=COMPILER
    )
    assert outcome.status == "reproduced" and outcome.checked_trap is True and "OutOfBounds" in outcome.reason


@pytest.mark.integration
def test_cli_inject_seals_candidates_and_rows(tmp_path, capsys):
    output = tmp_path / "inject"
    assert (
        main(
            [
                "inject",
                str(ROOT / "kernels" / "sum"),
                "--output",
                str(output),
                "--seed",
                "0",
                "--dry-run",
                "--clang",
                COMPILER,
            ]
        )
        == 0
    )
    assert not output.exists() and "no files" in capsys.readouterr().out.lower()
    assert (
        main(["inject", str(ROOT / "kernels" / "sum"), "--output", str(output), "--seed", "0", "--clang", COMPILER])
        == 0
    )
    assert evidence.status(output).complete
    rows = evidence.load(output).observations
    assert len(rows) == 19 and {r["kernel"] for r in rows} == {"sum"}
    assert {r["candidate_class"] for r in rows} >= {"false", "valid-and-sufficient", "valid-but-insufficient"}
    configuration = json.loads((output / "configuration.json").read_text(encoding="utf-8"))
    assert configuration["settings"]["operators"] == list(inject.OPERATORS)
    assert "candidates_sha256" in configuration["settings"] and (output / "candidates.json").exists()
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["kernels"]["sum"]["false"]["admitted"] == 0
