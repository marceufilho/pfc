"""Revisions from the step-19 review: feedback, join sites, per-site admission, evidence closure, cpp."""

import json
import os
import shutil
from pathlib import Path

import pytest

from pfc import report, smt
from pfc.cli import main
from pfc.diagnostics import FrontendError
from pfc.emit import GUARD_LINE, emit
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]
BRANCH_THEN_ACCESS = """long pick(const long a[], long a_len, long n, long flag) {
    __spec_requires(0 < n && n <= a_len);
    long k = 0;
    if (flag != 0) { k = 1; } else { k = 0; }
    if (k >= n) { k = 0; }
    return a[k]; }"""
INDEPENDENT_SITES = """long f(const long a[], long a_len, long n, long k) {
    __spec_requires(0 <= n && n < a_len);
    long x = a[n];
    long y = a[k];
    return x + y; }"""


@pytest.fixture(scope="module")
def target():
    return read_target(os.environ.get("PFC_CLANG", "clang-19"))


def unit(source):
    return lower(parse_preprocessed(source, "case.c"))


def test_feedback_names_retained_guards_when_only_arithmetic_blocks_admission(target):
    from pfc.propose.invariants import admit, feedback

    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    admission = admit(program, json.dumps({"invariants": ["(0 <= i && i <= n) && (s == s)"]}), target)
    assert not admission.accepted and admission.proof is not None and admission.proof.required_passed
    text = feedback(admission)
    assert text != "[]"
    data = json.loads(text)
    assert data["retained_guards"] and "arithmetic" in data["retained_guards"][0]
    assert any(item["kind"] == "overflow" for item in data["failures"])


def test_single_site_reached_by_several_paths_is_removed_with_every_citation(target):
    program = unit(BRANCH_THEN_ACCESS)
    proof = smt.check(program, target)
    bounds = [r.obligation.id for r in proof.results if r.obligation.kind == "bounds"]
    assert len(bounds) == 4 and proof.required_passed
    result = emit(program, target, mode="verified", proof=proof)
    assert len(result.decisions) == 1 and result.decisions[0].removed
    assert set(result.decisions[0].obligation_ids) == set(bounds)
    assert not GUARD_LINE.search(result.c)
    for identifier in bounds:
        assert f"// proved: obligation {identifier}" in result.c
    checked = emit(program, target, mode="checked")
    assert len(GUARD_LINE.findall(checked.c)) == 1


def test_one_failed_path_retains_the_shared_site(target):
    program = unit(BRANCH_THEN_ACCESS.replace("0 < n && n <= a_len", "0 <= n && n <= a_len"))
    proof = smt.check(program, target)
    statuses = {r.status for r in proof.results if r.obligation.kind == "bounds"}
    assert statuses == {"proved", "failed"}
    result = emit(program, target, mode="verified", proof=proof)
    assert not result.decisions[0].removed and GUARD_LINE.search(result.c)


def test_distinct_operations_sharing_a_location_stay_ambiguous(target):
    from dataclasses import replace

    program = unit("long f(const long a[], long a_len) { __spec_requires(a_len>1); return a[0]+a[1]; }")
    function = program.functions[0]
    statement = function.body.statements[0]
    expression = statement.value
    right = replace(expression.right, location=expression.left.location)
    statement = replace(statement, value=replace(expression, right=right))
    program = replace(program, functions=(replace(function, body=replace(function.body, statements=(statement,))),))
    proof = smt.check(program, target)
    result = emit(program, target, mode="verified", proof=proof)
    assert proof.required_passed
    assert all(not d.removed and d.obligation_ids == () for d in result.decisions)


def test_independent_failure_does_not_block_a_proved_site(target):
    program = unit(INDEPENDENT_SITES)
    proof = smt.check(program, target)
    assert not proof.required_passed
    result = emit(program, target, mode="verified", proof=proof)
    assert [d.removed for d in result.decisions] == [True, False]
    assert len(GUARD_LINE.findall(result.c)) == 1
    assert sum("// proved: obligation bounds:" in line for line in result.c.splitlines()) == 1
    assert "not proved" in result.decisions[1].reason


def test_restrict_still_requires_every_required_proof(target):
    source = (ROOT / "kernels/saxpy/kernel.c").read_text().replace('#include "spec.h"', "")
    source = source.replace("REQUIRES(", "__spec_requires(").replace("INVARIANT(", "__spec_invariant(")
    source = source.replace("SEPARATED(", "__spec_separated(").replace("return n;", "return y[n];")
    program = unit(source)
    proof = smt.check(program, target)
    assert not proof.required_passed
    result = emit(program, target, mode="verified", proof=proof, facts=frozenset({"delete", "restrict", "assume"}))
    assert "[restrict]" not in result.c
    assert any(d.removed for d in result.decisions) and not result.decisions[-1].removed


def test_unlisted_files_cannot_join_sealed_evidence(tmp_path):
    entry = report.Row("kernel", 4, 3, 1, 0, 0, 0, True, 1, 1, 1000, 1000, True, True)
    summary = report.Summary("test", (entry,), True)
    report.seal(tmp_path, summary)
    assert report.load(tmp_path) == summary
    (tmp_path / "benchmark.json").write_text("[]", encoding="utf-8")
    with pytest.raises(FrontendError, match="report-input"):
        report.load(tmp_path)
    assert main(["report", str(tmp_path)]) == 1


@pytest.fixture
def sum_directory(tmp_path):
    root = tmp_path / "kernels"
    shutil.copytree(ROOT / "kernels" / "sum", root / "sum")
    return root


def test_directory_commands_honor_cpp_selection(sum_directory, capsys, monkeypatch):
    assert main(["verify", str(sum_directory), "--cpp", "/missing/cpp", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["rows"][0]["problem"]
    monkeypatch.setenv("PFC_CPP", "/missing/cpp")
    assert main(["verify", str(sum_directory), "--json"]) == 1
    capsys.readouterr()
    assert main(["build", str(sum_directory), "--mode", "checked", "--dry-run", "--json"]) == 1
    capsys.readouterr()
    assert main(["propose", str(sum_directory), "--dry-run"]) == 1
    capsys.readouterr()
    monkeypatch.delenv("PFC_CPP")
    assert main(["verify", str(sum_directory), "--json"]) == 0


@pytest.mark.parametrize("name", ["sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search"])
def test_near_miss_invariants_are_rejected_by_the_solver_not_the_parser(name, target):
    from pfc.corpus import NEAR_MISS_INVARIANTS, PROFILES
    from pfc.propose.invariants import admit, loop_count

    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    assert set(NEAR_MISS_INVARIANTS) == set(PROFILES)
    candidates = NEAR_MISS_INVARIANTS[name]
    assert len(candidates) == loop_count(program)
    result = admit(program, json.dumps({"invariants": list(candidates)}), target)
    assert not result.accepted and result.problem is None and result.proof is not None
    assert any(r.status == "failed" and r.counterexample for r in result.proof.results)


def test_replay_with_malformed_exchange_is_input_validation(target):
    from pfc import ir
    from pfc.propose.experiment import _recheck
    from pfc.propose.llm import Settings

    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    records = [
        {
            "prompt": "p",
            "sample_id": "x/run-1/sum/1",
            "settings": ir.to_data(Settings()),
            "exchange": {"text": "{}"},
        }
    ]
    with pytest.raises(ValueError):
        _recheck(program, target, records, Path(os.devnull), 1000)
