import json
import os
from pathlib import Path

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


def test_bounded_loop_feeds_actual_rejection_and_admits_sum(tmp_path):
    from pfc.propose.invariants import synthesize
    from pfc.propose.llm import Exchange, Settings

    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    prompts = []

    def send(request):
        prompts.append(request.prompt)
        text = json.dumps({"invariants": ["0" if len(prompts) == 1 else "0<=i && i<=n"]})
        return Exchange(text, request.settings.model, f"test-{len(prompts)}", 1, 1, "test")

    result = synthesize(
        program,
        read_target(os.environ.get("PFC_CLANG", "clang-19")),
        settings=Settings(),
        sample_id="test/run-1/sum",
        cache=tmp_path / "cache",
        log=tmp_path / "proposals.jsonl",
        live=True,
        send=send,
    )
    assert result.found and len(result.attempts) == 2
    assert "counterexample" in prompts[1] and "entry" in prompts[1]
    assert "INVARIANT(" not in prompts[0]
    assert "0 <= i" not in prompts[0].split("Checker feedback")[0]
    records = [json.loads(line) for line in (tmp_path / "proposals.jsonl").read_text().splitlines()]
    assert len(records) == 2 and not records[0]["accepted"] and records[1]["accepted"]
    assert records[0]["proof"]["results"]


def test_five_attempt_budget_and_service_errors_are_recorded(tmp_path):
    from pfc.propose.invariants import synthesize
    from pfc.propose.llm import Settings

    calls = []

    def send(request):
        calls.append(request)
        raise ValueError("service unavailable")

    result = synthesize(
        lower(parse_source(ROOT / "kernels/sum/kernel.c")),
        read_target(os.environ.get("PFC_CLANG", "clang-19")),
        settings=Settings(),
        sample_id="test/run-1/sum",
        cache=tmp_path / "cache",
        log=tmp_path / "proposals.jsonl",
        live=True,
        send=send,
    )
    assert not result.found and len(result.attempts) == len(calls) == 5
    assert all(a.error and a.admission is None for a in result.attempts)
