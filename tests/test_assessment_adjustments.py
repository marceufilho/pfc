"""Assessment corrections: meaningful VCs, non-vacuous entry and explicit evidence versions."""

from dataclasses import replace
from pathlib import Path

import pytest
import z3

from pfc import report, smt, vc
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import Target

ROOT = Path(__file__).resolve().parents[1]
TARGET = Target("test", "x86_64", 32, 64)


def program(requires="a_len > 0"):
    return lower(
        parse_preprocessed(
            f"long first(const long a[], long a_len) {{ __spec_requires({requires}); return a[0]; }}",
            "assessment.c",
        )
    )


def test_only_substantive_obligations_are_counted():
    conditions = vc.generate(lower(parse_source(ROOT / "kernels/sum/kernel.c")))
    assert vc.MODEL.startswith("vc-v4:")
    assert [o.kind for o in conditions.obligations] == ["entry", "preservation", "bounds", "overflow", "overflow"]
    assert len(conditions.entry_checks) == 1
    assert conditions.entry_checks[0].function == "sum"


def test_contradictory_entry_blocks_removal_without_falsifying_formula_verdict():
    p = program("a_len > 0 && a_len < 0")
    proof = smt.check(p, TARGET)
    assert proof.results[0].status == "proved"  # The implication really is valid vacuously.
    assert not proof.required_passed
    assert proof.entry_results[0].status == "unsat"
    emission = emit(p, TARGET, mode="verified", proof=proof)
    assert not emission.decisions[0].removed
    assert "entry" in emission.decisions[0].reason
    assert "inconsistent" in smt.render_report(proof)


def test_empty_contract_domain_fails_even_without_safety_sites():
    p = lower(parse_preprocessed("long f(long n) { __spec_requires(n > 0 && n < 0); return n; }"))
    proof = smt.check(p, TARGET)
    assert not proof.required_passed
    assert proof.results == ()


@pytest.mark.parametrize(
    "source",
    [
        "long f(long n) { __spec_requires(n == 0); long i=0; while(i<n) { __spec_invariant(i==0); i=i+1; } return i; }",
        "long f(long n) { __spec_requires(n == 0); if(n>0) { long i=0; "
        "while(i<n) { __spec_invariant(0); i=i+1; } } return n; }",
    ],
)
def test_empty_iteration_domain_or_unreachable_loop_is_not_an_inconsistent_entry(source):
    proof = smt.check(lower(parse_preprocessed(source)), TARGET)
    assert proof.entry_results[0].status == "sat"
    assert proof.required_passed


@pytest.mark.parametrize(("reason", "status"), [("timeout", "timeout"), ("incomplete", "unknown")])
def test_inconclusive_entry_retains_guards_and_is_not_reported_as_unsat(reason, status):
    class Unknown:
        def set(self, **kwargs):
            pass

        def add(self, *args):
            pass

        def check(self):
            return z3.unknown

        def reason_unknown(self):
            return reason

    calls = []

    def factory():
        calls.append(1)
        return z3.Solver() if len(calls) == 1 else Unknown()

    p = program()
    proof = smt.check(p, TARGET, solver_factory=factory)
    assert proof.results[0].status == "proved"
    assert proof.entry_results[0].status == status
    assert not proof.required_passed
    assert not emit(p, TARGET, mode="verified", proof=proof).decisions[0].removed
    assert "inconsistent" not in smt.render_report(proof)


@pytest.mark.parametrize("change", ["missing", "duplicate", "context", "model"])
def test_authorization_recomputes_entry_evidence_identity(change):
    p = program()
    proof = smt.check(p, TARGET)
    if change == "missing":
        proof = replace(proof, entry_results=())
    elif change == "duplicate":
        proof = replace(proof, entry_results=proof.entry_results * 2)
    elif change == "context":
        other = smt.check(program("a_len > 1"), TARGET)
        proof = replace(proof, entry_results=other.entry_results)
    else:
        proof = replace(proof, vc_model="vc-v3")
    assert not emit(p, TARGET, mode="verified", proof=proof).decisions[0].removed


def test_entry_queries_share_the_bounded_process_cache_without_inflating_obligations():
    calls = []

    def factory():
        calls.append(1)
        return z3.Solver()

    cache = smt.SolverCache()
    first = smt.check(program(), TARGET, solver_factory=factory, cache=cache)
    second = smt.check(program(), TARGET, solver_factory=factory, cache=cache)
    assert len(calls) == 2
    assert len(first.results) == len(second.results) == 1
    assert second.entry_results[0].cache_hit
    assert second.required_passed


def test_current_proof_and_sealed_summary_identify_vc_and_encoding_separately(tmp_path):
    import json

    proof = smt.check(program(), TARGET)
    assert proof.vc_model == vc.MODEL
    summary = report.Summary("verify", (report.row("first", proof, passed=True),), True)
    report.seal(tmp_path, summary)
    for name in ("report.json", "manifest.json"):
        data = json.loads((tmp_path / name).read_text())
        assert data["schema_version"] == 3
        assert data["vc_model"] == vc.MODEL
        assert data["smt_encoding"] == smt.ENCODING
    assert report.load(tmp_path) == summary
    assert "obligations" in report.render(summary)
    assert "guard sites" in report.render(summary)


def test_entry_solver_errors_fail_closed_without_claiming_inconsistency():
    class Broken:
        def set(self, **kwargs):
            pass

        def add(self, *args):
            pass

        def check(self):
            raise z3.Z3Exception("private detail")

    proof = smt.check(program(), TARGET, solver_factory=Broken)
    assert proof.entry_results[0].status == "error"
    assert "private detail" not in smt.render_report(proof)
    assert not proof.required_passed


def test_summary_explains_entry_failure_even_when_all_implications_prove():
    proof = smt.check(program("a_len > 0 && a_len < 0"), TARGET)
    row = report.row("first", proof, passed=False)
    assert "entry" in row.problem and "unsat" in row.problem


def test_experiment_preserves_current_model_and_does_not_invent_a_legacy_version(tmp_path):
    import json

    from pfc.corpus import PROFILES
    from pfc.propose.experiment import KernelRun, load, summarize

    rows = tuple(KernelRun("sum", i, PROFILES["sum"], False, 1, 0, 0, 1, 1, 0, True) for i in (1, 2, 3))
    current = summarize("assessment", "test", rows)
    assert current.vc_model == vc.MODEL
    report.write_json(tmp_path / "experiment.json", current)
    assert load(tmp_path).vc_model == vc.MODEL
    data = json.loads((tmp_path / "experiment.json").read_text())
    data.pop("vc_model")
    (tmp_path / "experiment.json").write_text(json.dumps(data))
    assert load(tmp_path).vc_model is None
