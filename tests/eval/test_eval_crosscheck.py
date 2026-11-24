"""Second-solver cross-check of the obligations: SMT-LIB export, cvc5 driver, agreement rows (plan phase 6)."""

import hashlib
import os
import stat
from pathlib import Path

import pytest

from pfc import vc
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target
from pfc_eval import crosscheck

ROOT = Path(__file__).resolve().parents[2]
T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)


def kernel(name):
    return lower(parse_source(ROOT / "kernels" / name / "kernel.c"))


def test_export_contains_the_logic_declarations_hypotheses_and_negated_goal_and_check_sat():
    report = vc.generate(kernel("sum"))
    obligation = next(o for o in report.obligations if o.kind == "bounds")
    text = crosscheck.export(obligation, T64, logic="QF_AUFNIA")
    assert text.startswith("(set-logic QF_AUFNIA)\n") and text.rstrip().endswith("(check-sat)")
    assert text.count("(set-logic") == 1 and "(declare-fun" in text and "(assert" in text and "(not " in text
    entry = crosscheck.export_entry(report.entry_checks[0], T64, logic="QF_AUFNIA")
    assert (
        entry.startswith("(set-logic QF_AUFNIA)\n")
        and "(check-sat)" in entry
        and "(not " not in entry.split("(assert", 1)[0]
    )


def test_division_encoding_is_total_in_the_export_and_free_of_solver_specific_functions():
    report = vc.generate(kernel("binary_search"))
    # The bounds obligation on a[mid] carries the quotient (lo + hi) / 2 in its index: the totalized c_div term.
    obligation = next(o for o in report.obligations if o.kind == "bounds")
    text = crosscheck.export(obligation, T64, logic="QF_AUFNIA")
    # Z3 prints Abs as an ite over the sign; the quotient is SMT-LIB `div`, guarded by `ite (= r 0) 0 ...`.
    assert "ite" in text and "div" in text and "(/" not in text  # Int division is `div`, never `/`
    assert not any(token in text for token in ("bv", "to_real", "to_int", "int2bv"))


def test_cvc5_output_parsing_maps_statuses_and_anything_else_to_error():
    assert crosscheck.parse_status("unsat\n", "", 0, elapsed_ms=10, timeout_ms=1000) == "unsat"
    assert crosscheck.parse_status("sat\n(model)", "", 0, elapsed_ms=10, timeout_ms=1000) == "sat"
    assert crosscheck.parse_status("unknown\n", "", 0, elapsed_ms=10, timeout_ms=1000) == "unknown"
    assert crosscheck.parse_status("unknown\n", "", 0, elapsed_ms=1200, timeout_ms=1000) == "timeout"
    assert crosscheck.parse_status("", '(error "bad logic")', 1, elapsed_ms=5, timeout_ms=1000) == "error"
    assert crosscheck.parse_status("yes\n", "", 0, elapsed_ms=5, timeout_ms=1000) == "error"


def test_agreement_is_null_when_either_solver_is_inconclusive():
    assert crosscheck.agreement("proved", "unsat") is True and crosscheck.agreement("failed", "sat") is True
    assert crosscheck.agreement("proved", "sat") is False and crosscheck.agreement("failed", "unsat") is False
    assert crosscheck.agreement("timeout", "unsat") is None and crosscheck.agreement("proved", "unknown") is None
    assert crosscheck.agreement("proved", "error") is None and crosscheck.agreement("unknown", "timeout") is None
    assert (
        crosscheck.agreement("sat", "sat", entry=True) is True
        and crosscheck.agreement("unsat", "sat", entry=True) is False
    )


def test_configuration_records_path_version_and_sha256_and_the_harness_never_downloads(tmp_path):
    fake = tmp_path / "cvc5"
    fake.write_text(
        "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'This is cvc5 version 1.2.3 [git abc]'; exit 0; fi\n"
        "echo unsat\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    identity = crosscheck.cvc5_identity(fake)
    assert identity["cvc5_path"] == str(fake.resolve()) and identity["cvc5_version"].startswith(
        "This is cvc5 version 1.2.3"
    )
    assert identity["cvc5_sha256"] == hashlib.sha256(fake.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="cvc5"):
        crosscheck.cvc5_identity(tmp_path / "missing")
    result = crosscheck.run_cvc5(fake, "(set-logic ALL)\n(check-sat)\n", timeout_ms=1000, directory=tmp_path / "q")
    assert (
        result.status == "unsat" and (tmp_path / "q" / "query.smt2").is_file() and result.argv[0] == str(fake.resolve())
    )
    assert "--tlimit=1000" in result.argv and "--lang=smt2" in result.argv


def test_rows_and_summary_are_labelled_advisory():
    columns = {c.name: c for c in crosscheck.SCHEMA.columns}
    assert {
        "kernel",
        "function",
        "obligation_id",
        "kind",
        "category",
        "query_id",
        "z3_status",
        "cvc5_status",
        "agreement",
    } <= set(columns)
    assert columns["agreement"].nullable and set(columns["category"].allowed) == {"required", "reported", "entry"}
    rows = [
        {"kernel": "sum", "category": "required", "z3_status": "proved", "cvc5_status": "unsat", "agreement": True},
        {"kernel": "sum", "category": "required", "z3_status": "proved", "cvc5_status": "timeout", "agreement": None},
        {
            "kernel": "sum",
            "category": "reported",
            "z3_status": "failed",
            "cvc5_status": "unsat",
            "agreement": False,
            "obligation_id": "overflow:x",
        },
        {"kernel": "sum", "category": "entry", "z3_status": "sat", "cvc5_status": "sat", "agreement": True},
    ]
    summary = crosscheck.summarize(rows)
    assert summary["advisory"] is True and "no removal authority" in summary["note"]
    assert summary["by_category"]["required"] == {"n": 2, "agree": 1, "disagree": 0, "inconclusive": 1}
    assert summary["by_category"]["reported"]["disagree"] == 1 and summary["disagreements"] == ["overflow:x"]
    assert summary["passed"] is False  # a disagreement is a red flag, never silently accepted
    assert "advisory" in crosscheck.render(summary)


@pytest.mark.integration
def test_sum_required_obligations_agree_between_z3_and_cvc5(tmp_path):
    binary = os.environ.get("PFC_EVAL_CVC5")
    if not binary:
        pytest.skip("set PFC_EVAL_CVC5 to a cvc5 binary to run the second-solver cross-check")
    rows = crosscheck.run_kernel(kernel("sum"), "sum", T64, cvc5=Path(binary), timeout_ms=10000, directory=tmp_path)
    required = [r for r in rows if r["category"] == "required"]
    assert required and all(r["agreement"] is True for r in required), [
        (r["obligation_id"], r["cvc5_status"]) for r in required
    ]
    assert any(r["category"] == "entry" and r["agreement"] is True for r in rows)
