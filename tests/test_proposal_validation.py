import json
import os
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search"])
def test_false_invariant_is_rejected_with_real_counterexample(name):
    from pfc.propose.experiment import inject_wrong

    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    injection = inject_wrong(program, read_target(os.environ.get("PFC_CLANG", "clang-19")))
    assert injection.rejected
    for result in (injection.literal_false, injection.near_miss):
        assert not result.accepted and result.problem is None
        assert result.proof and any(r.status == "failed" and r.counterexample for r in result.proof.results)


def test_sum_proposal_checked_and_program_body_preserved():
    from pfc.propose.invariants import admit

    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    target = read_target(os.environ.get("PFC_CLANG", "clang-19"))
    result = admit(program, json.dumps({"invariants": ["0<=i && i<=n"]}), target)
    assert result.accepted and result.proof.required_passed
    assert not admit(program, json.dumps({"invariants": ["1"]}), target).accepted


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "{}",
        "[]",
        '{"invariants": [1]}',
        '{"invariants": ["1); return 0; INVARIANT(1"]}',
        '{"invariants": ["system(0)"]}',
        '{"invariants":["x"],"other":1}',
        "x" * 17000,
    ],
)
def test_malformed_or_executable_proposal_is_data_only(reply):
    from pfc.propose.invariants import admit

    p = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    result = admit(p, reply, read_target(os.environ.get("PFC_CLANG", "clang-19")))
    assert not result.accepted and result.problem


def test_nested_candidates_keep_preorder_even_when_first_is_zero():
    from pfc import ir
    from pfc.propose.invariants import admit
    from pfc.wp import walk

    p = lower(parse_source(ROOT / "kernels/matvec/kernel.c"))
    result = admit(p, '{"invariants":["0","1"]}', read_target(os.environ.get("PFC_CLANG", "clang-19")))
    assert result.program is not None
    loops = [s for s in walk(result.program.functions[0].body) if isinstance(s, ir.While)]
    assert [ir._expression(s.invariant.condition) for s in loops] == ["0", "1"]
