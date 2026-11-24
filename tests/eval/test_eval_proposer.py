"""Proposer conditions L1/L2/L3 over function tasks through frozen admit(); transcripts and replay (roadmap E10)."""

import json
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import adapters, proposer

ROOT = Path(__file__).resolve().parents[2]
GOOD = '{"invariants":["0 <= i && i <= n"]}'
WEAK = '{"invariants":["i <= n"]}'  # inductive, but the lower bound the bounds proof needs is missing
FALSE = '{"invariants":["i < n"]}'


@pytest.fixture(scope="module")
def sum_program():
    return lower(parse_source(ROOT / "kernels/sum/kernel.c"))


@pytest.fixture(scope="module")
def target():
    return read_target("clang-19")


def _sender(replies):
    calls = []

    def send(request):
        calls.append(request)
        text = replies[min(len(calls), len(replies)) - 1]
        return adapters.Exchange(text, request.settings.model, f"resp_{len(calls)}", 10, 5, 1, 3.0, "live")

    send.calls = calls  # type: ignore[attr-defined]
    return send


@pytest.fixture(scope="module")
def zilu01_program():
    return lower(parse_source(ROOT / "bench/external/zilu/zilu01/kernel.c"))


def test_instruction_names_the_proof_goals_of_the_function(sum_program, zilu01_program):
    text = proposer.instruction(sum_program)
    assert "function sum." in text and "array of 1 expressions" in text and "INVARIANT" not in text
    assert "REQUIRES(" in text and "a_len" in text  # the function text without handwritten invariants
    assert "Proof goals: bounds of every array access" in text and "assertion" not in text
    assert not proposer.contains_feedback(text)
    scalar = proposer.instruction(zilu01_program)
    assert "function zilu01." in scalar and "Proof goals: the assertion y >= 1" in scalar
    assert "negated loop condition" in scalar and "bounds" not in scalar.split("Function without")[0]
    # The overflow caveat reflects the arithmetic closure of guard removal; it applies only where guards exist.
    assert "must also be proved free of overflow" in text and "mention an accumulator only" in text
    assert "does not decide admission" in scalar and "free of overflow" not in scalar
    assert adapters.PROTOCOL == "pfc-eval-proposer-v3"  # the goal-directed wording is a recorded protocol amendment


def test_l2_prompts_never_carry_feedback_and_l1_prompts_do(sum_program, target, tmp_path):
    settings = adapters.Settings.default()
    l1 = proposer.run_task(
        sum_program,
        target,
        condition="L1",
        settings=settings,
        sample_id="id/L1/run-1/sum",
        cache=tmp_path / "c1",
        live=True,
        send=_sender([FALSE, GOOD]),
    )
    assert l1.success and len(l1.attempts) == 2
    assert proposer.contains_feedback(l1.attempts[0].request.prompt)  # baseline feedback for `true`
    assert "Previous proposal" in l1.attempts[1].request.prompt and FALSE in l1.attempts[1].request.prompt
    assert l1.attempts[0].rejection_class == "not_inductive" and l1.attempts[1].rejection_class == "success"
    l2 = proposer.run_task(
        sum_program,
        target,
        condition="L2",
        settings=settings,
        sample_id="id/L2/run-1/sum",
        cache=tmp_path / "c2",
        live=True,
        send=_sender([WEAK, FALSE, GOOD]),
    )
    assert l2.success and len(l2.attempts) == 3
    prompts = {a.request.prompt for a in l2.attempts}
    assert len(prompts) == 1 and not proposer.contains_feedback(next(iter(prompts)))
    assert [a.request.sample_id for a in l2.attempts] == ["id/L2/run-1/sum/1", "id/L2/run-1/sum/2", "id/L2/run-1/sum/3"]
    assert l2.attempts[0].rejection_class == "too_weak"


def test_contains_feedback_detects_every_counterexample_channel():
    assert proposer.contains_feedback("... Checker feedback: ...")
    assert proposer.contains_feedback('{"counterexample": {"n": 1}}')
    assert proposer.contains_feedback("Previous proposal:\n{}")
    assert not proposer.contains_feedback("Propose inductive loop invariants for the C function f.")


def test_rejection_classes_cover_protocol_errors_and_unparseable_replies(sum_program, target, tmp_path):
    def failing(request):
        raise ValueError("service unavailable")

    result = proposer.run_task(
        sum_program,
        target,
        condition="L1",
        settings=adapters.Settings.default(),
        sample_id="id/L1/run-1/sum",
        cache=tmp_path / "c",
        live=True,
        send=failing,
        attempts=2,
    )
    assert not result.success and [a.rejection_class for a in result.attempts] == ["protocol_error"] * 2
    garbage = proposer.run_task(
        sum_program,
        target,
        condition="L1",
        settings=adapters.Settings.default(),
        sample_id="id/L1/run-1/sumb",
        cache=tmp_path / "d",
        live=True,
        send=_sender(["not json", '{"invariants":["0 <= zz"]}', GOOD]),
    )
    assert [a.rejection_class for a in garbage.attempts] == ["unparseable", "out_of_scope", "success"]


def test_base_url_is_only_for_l3_and_conditions_are_fixed():
    assert proposer.CONDITIONS == ("L1", "L2", "L3")
    assert proposer.settings_for("L1", model="gpt-6-astra", effort="low", temperature=None, base_url=None).base_url
    with pytest.raises(ValueError):
        proposer.settings_for("L1", model="m", effort="low", temperature=None, base_url="http://localhost:8000/v1")
    with pytest.raises(ValueError):
        proposer.settings_for("L3", model="m", effort=None, temperature=0.7, base_url=None)
    local = proposer.settings_for(
        "L3", model="local", effort=None, temperature=0.7, base_url="http://localhost:8000/v1"
    )
    assert local.base_url == "http://localhost:8000/v1"


def test_transcript_records_round_trip_into_replay_groups(sum_program, target, tmp_path):
    result = proposer.run_task(
        sum_program,
        target,
        condition="L1",
        settings=adapters.Settings.default(),
        sample_id="abc/L1/run-2/sum",
        cache=tmp_path / "c",
        live=True,
        send=_sender([FALSE, GOOD]),
    )
    log = tmp_path / "logs" / "proposals.jsonl"
    for attempt in result.attempts:
        proposer.record(log, "sum", "L1", attempt)
    groups = proposer.replay_groups(log)
    assert set(groups) == {("sum", 2)} and [r["attempt"] for r in groups[("sum", 2)]] == [1, 2]
    assert groups[("sum", 2)][0]["condition"] == "L1" and groups[("sum", 2)][1]["accepted"] is True

    def never(request):
        raise AssertionError("replay must not send")

    rechecked = proposer.recheck(sum_program, target, groups[("sum", 2)])
    assert rechecked.success and [a.rejection_class for a in rechecked.attempts] == ["not_inductive", "success"]
    assert all(a.exchange.origin == "replay" for a in rechecked.attempts)
    assert proposer.replay_consistent(groups[("sum", 2)], rechecked)


def test_rows_and_summary_report_success_at_k_with_cluster_bootstrap():
    def row(kernel, run, attempt, success, task_success, cls="not_inductive"):
        return {
            "kernel": kernel,
            "function": kernel,
            "condition": "L1",
            "run": run,
            "attempt": attempt,
            "model": "m",
            "protocol": "p",
            "base_url": "https://api.openai.com/v1",
            "snapshot_status": "undated",
            "origin": "test",
            "success": success,
            "task_success": task_success,
            "rejection_class": "success" if success else cls,
            "tokens_in": 10,
            "tokens_out": 5,
            "reasoning_tokens": 1,
            "latency_ms": 2.0,
            "loops": 1,
        }

    rows = [
        row("sum", 1, 1, True, True),
        row("sum", 2, 1, False, True),
        row("sum", 2, 2, True, True),
        row("dot", 1, 1, False, False, "too_weak"),
        row("dot", 1, 2, False, False, "too_weak"),
        row("dot", 2, 1, True, True),
    ]
    summary = proposer.summarize(rows, runs=2, seed=0)
    condition = summary["conditions"]["L1"]
    assert condition["tasks"] == 2 and condition["success_at_k"] == {"dot": 0.5, "sum": 1.0}
    assert condition["mean_success_at_k"]["estimate"] == pytest.approx(0.75)
    assert 0.0 <= condition["mean_success_at_k"]["low"] <= 0.75 <= condition["mean_success_at_k"]["high"] <= 1.0
    assert condition["successes_by_run"] == [1, 2]
    assert condition["attempts_to_first_success"] == pytest.approx((1 + 2 + 1) / 3)
    assert condition["rejection_classes"] == {"not_inductive": 1, "too_weak": 2}
    assert condition["tokens_per_success"] == pytest.approx(90 / 3)
    table = proposer.render_table(summary)
    assert "success@2" in table and "sum" in table and "dot" in table
    json.dumps(summary)


def test_sources_outside_the_repository_are_refused_for_full_transcripts(tmp_path):
    outside = tmp_path / "kernel.c"
    outside.write_text("int f(void) { return 0; }\n")
    with pytest.raises(ValueError, match="repository"):
        proposer.authorize_transcripts((outside,), ROOT)
    proposer.authorize_transcripts((ROOT / "kernels/sum/kernel.c",), ROOT)


def test_an_empty_invariant_list_is_an_abstention_not_a_parse_failure(sum_program, target, tmp_path):
    assert "abstained" in proposer.REJECTION_CLASSES
    result = proposer.run_task(
        sum_program,
        target,
        condition="L2",
        settings=adapters.Settings.default(),
        sample_id="id/L2/run-1/sum",
        cache=tmp_path / "c",
        live=True,
        send=_sender(['{"invariants": []}', "not json", GOOD]),
    )
    assert [a.rejection_class for a in result.attempts] == ["abstained", "unparseable", "success"]


HELPER_AND_CALLER = """#include "spec.h"

long helper(long v)
{
    REQUIRES(0 <= v);
    ENSURES(RESULT == v);
    return v;
}

long caller(long n)
{
    REQUIRES(0 <= n && n <= 1000);
    long i = 0;
    long s = 0;
    while (i < n) {
        INVARIANT(0 <= i && i <= n && s >= 0);
        s = helper(i);
        i = i + 1;
    }
    ASSERT(s >= 0);
    return s;
}
"""


def test_instruction_addresses_the_entry_function_of_a_multi_function_unit(tmp_path):
    path = tmp_path / "kernel.c"
    path.write_text(HELPER_AND_CALLER, encoding="utf-8")
    program = lower(parse_source(path))
    assert proposer.entry(program).name == "caller"  # the callee is defined first, as the subset requires
    text = proposer.instruction(program).split("Function")[0]
    assert "for the C function caller" in text and "2 functions" in text and "array of 1 expressions" in text
    assert "the assertion s >= 0" in text and "the precondition of helper at every call" in text
    assert "the postcondition RESULT == v" in text


def test_rows_record_guards_total_and_removed_from_the_admission_emission_and_null_without_one():
    from pfc.propose.invariants import admit
    from pfc.target import Target

    T64 = Target("clang", "x86_64-pc-linux-gnu", 32, 64)
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    settings = adapters.Settings.default()
    accepted = admit(program, json.dumps({"invariants": ["0 <= i && i <= n"]}), T64)
    rejected = admit(program, json.dumps({"invariants": ["1"]}), T64)
    assert accepted.accepted and not rejected.accepted
    attempts = (
        proposer.Attempt(1, adapters.Request("p", "s/L1/run-1/sum/1", settings), None, rejected, None, "too_weak"),
        proposer.Attempt(2, adapters.Request("p", "s/L1/run-1/sum/2", settings), None, None, "boom", "protocol_error"),
        proposer.Attempt(3, adapters.Request("p", "s/L1/run-1/sum/3", settings), None, accepted, None, "success"),
    )
    result = proposer.TaskResult("L1", attempts, True)
    rows = proposer.rows("sum", program, 1, result, origin="test", target=T64)
    assert [r["guards_total"] for r in rows] == [1, 1, 1]
    assert [r["guards_removed"] for r in rows] == [0, None, 1]
    columns = {c.name: c for c in proposer.SCHEMA.columns}
    assert columns["guards_removed"].nullable and columns["guards_total"].nullable  # null without a target
    without = proposer.rows("sum", program, 1, result, origin="test")
    assert all(r["guards_total"] is None and r["guards_removed"] is None for r in without)  # no target: no emission
