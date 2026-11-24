"""Step 19d: regression coverage for the independent review's three findings."""

import json
import os
import resource
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from pfc import ir, report, smt, vc
from pfc.cli import main
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.propose.experiment import _recheck, run
from pfc.propose.llm import Exchange, Settings
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")


@pytest.fixture(scope="module")
def target():
    return read_target(COMPILER)


def disable_core_dump():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def test_failed_internal_precondition_preserves_checked_behavior(tmp_path, target):
    program = lower(
        parse_preprocessed(
            """
long get(const long a[], long a_len) {
    __spec_requires(a_len > 0);
    return a[0];
}
long entry(const long b[], long b_len) {
    __spec_requires(b_len == 0);
    return get(b, b_len);
}
""",
            "review-call.c",
        )
    )
    proof = smt.check(program, target)
    assert any(r.obligation.kind == "call-precondition" and r.status == "failed" for r in proof.results)
    driver = tmp_path / "driver.c"
    # Physical storage is valid even though the logical capacity supplied to entry is zero.
    driver.write_text(
        "long entry(const long[], long); int main(void) { long b[1] = {7}; return entry(b, 0); }",
        encoding="utf-8",
    )
    outcomes = {}
    for mode in ("checked", "verified"):
        result = emit(program, target, mode=mode, proof=proof if mode == "verified" else None)
        source, binary = tmp_path / f"{mode}.c", tmp_path / mode
        source.write_text(result.c, encoding="utf-8")
        subprocess.run(
            [COMPILER, "-std=c11", "-O2", str(source), str(driver), "-o", str(binary)], check=True, timeout=10
        )
        outcomes[mode] = subprocess.run([str(binary)], preexec_fn=disable_core_dump, timeout=10).returncode
    assert outcomes["checked"] < 0, outcomes
    assert outcomes["verified"] == outcomes["checked"], outcomes


@pytest.mark.parametrize("annotation", ["assert", "ensures"])
def test_boolean_read_in_annotation_has_its_type_domain(annotation, target):
    source = (
        "long f(const _Bool a[], long a_len) { __spec_requires(a_len > 0); "
        f"__spec_{annotation}(a[0] == 0 || a[0] == 1); return 0; }}"
    )
    proof = smt.check(lower(parse_preprocessed(source, "review-domain.c")), target)
    failures = [
        (r.obligation.kind, r.status, r.counterexample)
        for r in proof.results
        if r.obligation.category == "required" and r.status != "proved"
    ]
    assert proof.required_passed, failures


@pytest.mark.parametrize("origin", [[], {}, 0, True, None, "invalid"])
def test_replay_rejects_wrong_field_type_as_input_error(tmp_path, target, origin):
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    settings = Settings()
    records = [
        {
            "prompt": "p",
            "sample_id": "review/run-1/sum/1",
            "settings": ir.to_data(settings),
            "exchange": {
                "text": '{"invariants": ["0 <= i && i <= n"]}',
                "model": settings.model,
                "response_id": "review",
                "input_tokens": 1,
                "output_tokens": 1,
                "origin": origin,
            },
        }
    ]
    with pytest.raises(ValueError):
        _recheck(program, target, records, tmp_path / "attempts.jsonl", 1000)


@pytest.mark.parametrize("status", ["failed", "timeout", "unknown", "error"])
def test_unproved_call_contract_retains_guards_and_assumes(target, status):
    program = lower(
        parse_preprocessed(
            "long get(const long a[], long a_len) { __spec_requires(a_len > 0); return a[0]; }"
            "long f(const long b[], long b_len) { __spec_requires(b_len > 0); return get(b, b_len); }",
            "call-status.c",
        )
    )
    proof = smt.check(program, target)
    assert proof.required_passed
    result = emit(program, target, mode="verified", proof=proof, facts=frozenset({"delete", "assume"}))
    assert all(d.removed for d in result.decisions) and "__builtin_assume" in result.c
    # Keep exact queries, replacing only a solver outcome to exercise fail-closed authorization.
    uncertain = replace(
        proof,
        results=tuple(
            replace(r, status=status) if r.obligation.kind == "call-precondition" else r for r in proof.results
        ),
    )
    result = emit(program, target, mode="verified", proof=uncertain, facts=frozenset({"delete", "assume"}))
    assert result.decisions and all(not d.removed for d in result.decisions)
    assert "__builtin_assume" not in result.c


@pytest.mark.parametrize(
    "entry_contract", ["__spec_requires(b_len == 0);", "__spec_requires(b_len > 0); __spec_assert(0);"]
)
def test_call_chain_and_multiple_callers_require_admitted_preconditions(target, entry_contract):
    program = lower(
        parse_preprocessed(
            "long get(const long a[], long a_len) { __spec_requires(a_len > 0); return a[0]; }"
            "long bridge(const long a[], long a_len) { __spec_requires(a_len > 0); return get(a, a_len); }"
            "long good(const long a[], long a_len) { __spec_requires(a_len > 0); return get(a, a_len); }"
            f"long entry(const long b[], long b_len) {{ {entry_contract} return bridge(b, b_len); }}",
            "call-chain.c",
        )
    )
    proof = smt.check(program, target)
    assert not proof.required_passed
    result = emit(program, target, mode="verified", proof=proof, facts=frozenset({"delete", "assume", "restrict"}))
    assert result.decisions and all(not d.removed for d in result.decisions)
    assert not result.emitted_facts


def test_annotation_domain_enters_invariant_initiation_and_preservation(target):
    program = lower(
        parse_preprocessed(
            "long f(_Bool a[], long a_len) { __spec_requires(a_len > 0); long i = 0;"
            "while (i < 2) { __spec_invariant(0 <= i && i <= 2 && (a[0] == 0 || a[0] == 1));"
            "a[0] = !a[0]; i = i + 1; } return i; }",
            "bool-invariant.c",
        )
    )
    proof = smt.check(program, target)
    assert proof.required_passed
    assert all(r.status == "proved" for r in proof.results)


@pytest.mark.parametrize("length", [0, 1])
def test_annotation_short_circuit_allows_unread_empty_array(target, length):
    program = lower(
        parse_preprocessed(
            f"long f(const _Bool a[], long a_len) {{ __spec_requires(a_len == {length});"
            "__spec_assert(a_len == 0 || (a[0] == 0 || a[0] == 1)); return 0; }",
            "bool-short-circuit.c",
        )
    )
    proof = smt.check(program, target)
    assert proof.required_passed


def test_annotation_type_domain_cannot_hide_failed_bounds(target):
    program = lower(
        parse_preprocessed(
            "long f(const _Bool a[], long a_len) { __spec_requires(a_len == 0);"
            "__spec_assert(a[0] == 0 || a[0] == 1); return 0; }",
            "invalid-bool-read.c",
        )
    )
    proof = smt.check(program, target)
    assert not proof.required_passed
    assertion = next(r for r in proof.results if r.obligation.kind == "assert")
    bounds = [r for r in proof.results if r.obligation.kind == "bounds"]
    assert bounds and all(r.status == "failed" for r in bounds)
    assert assertion.status == "proved" and not assertion.dependencies_satisfied
    assert set(r.obligation.id for r in bounds) <= set(assertion.obligation.dependencies)


def test_annotation_read_domain_is_guarded_by_its_short_circuit_path():
    program = lower(
        parse_preprocessed(
            "long f(const _Bool a[], long a_len) { __spec_requires(a_len >= 0);"
            "__spec_assert(a_len == 0 || a[0]); return 0; }",
            "guarded-domain.c",
        )
    )
    conditions = vc.generate(program)
    bounds = next(o for o in conditions.obligations if o.kind == "bounds")
    assertion = next(o for o in conditions.obligations if o.kind == "assert")
    domain = "((!(a_len == 0)) => ((0 <= a[0]) && (a[0] <= 1)))"
    assert domain not in [vc.render_term(h.formula) for h in bounds.hypotheses]
    assert domain in [vc.render_term(h.formula) for h in assertion.hypotheses if h.kind == "type-domain"]


def test_cli_replay_rejects_invalid_origin_without_traceback(tmp_path, capsys):
    recorded = tmp_path / "recorded"
    source = ROOT / "kernels/sum"
    run(
        (source,),
        recorded,
        compiler=COMPILER,
        live=True,
        send=lambda r: Exchange('{"invariants": ["0 <= i && i <= n"]}', r.settings.model, "test", 1, 1, "test"),
    )
    summary = report.load(recorded)
    transcript = recorded / "logs/proposals.jsonl"
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    records[0]["exchange"]["origin"] = []
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    # A consistent checksum is not a substitute for validating the imported field types.
    (recorded / "manifest.json").unlink()
    (recorded / "report.json").unlink()
    report.seal(recorded, summary)
    assert (
        main(
            [
                "propose",
                str(source),
                "--replay",
                str(recorded),
                "--output",
                str(tmp_path / "replayed"),
                "--clang",
                COMPILER,
            ]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "invalid, incomplete or mismatched model response" in error
    assert "Traceback" not in error
