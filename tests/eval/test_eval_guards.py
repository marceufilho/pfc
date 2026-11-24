"""Guards endpoint for the held-out study: fraction of executable guard sites removed per kernel and condition."""

import pytest

from pfc_eval import guards


def _proposer(condition, kernel, run, removed, total=4):
    return {
        "condition": condition,
        "kernel": kernel,
        "run": run,
        "attempt": 1,
        "guards_removed": removed,
        "guards_total": total,
    }


def test_guard_endpoint_pairs_conditions_per_kernel_and_reports_l2_minus_b2_first_with_holm_over_three():
    proposer = [
        _proposer("L1", "a", 1, 4),
        _proposer("L1", "a", 2, 4),
        _proposer("L1", "b", 1, 2),
        _proposer("L1", "b", 2, 4),
        _proposer("L2", "a", 1, 4),
        _proposer("L2", "a", 2, 2),
        _proposer("L2", "b", 1, 0),
        _proposer("L2", "b", 2, None),
    ]
    houdini = [
        {"family": "b1", "kernel": "a", "loop_index": 0, "guards_removed": 0, "guards_total": 4},
        {"family": "b1", "kernel": "b", "loop_index": 0, "guards_removed": 4, "guards_total": 4},
        {"family": "b2", "kernel": "a", "loop_index": 0, "guards_removed": 2, "guards_total": 4},
        {"family": "b2", "kernel": "b", "loop_index": 0, "guards_removed": 2, "guards_total": 4},
        {"family": "b2", "kernel": "b", "loop_index": 1, "guards_removed": 2, "guards_total": 4},
    ]
    differential = [
        {"kernel": "a", "case_id": "c1", "guards_removed": 4, "guards_total": 4},
        {"kernel": "b", "case_id": "c1", "guards_removed": 4, "guards_total": 4},
    ]
    table = guards.endpoint(proposer, houdini, differential, seed=0)
    per_kernel = {(r["kernel"], r["condition"]): r for r in table["kernels"]}
    assert per_kernel[("a", "L1")]["fraction"] == 1.0 and per_kernel[("b", "L1")]["fraction"] == 0.75
    assert (
        per_kernel[("a", "L2")]["fraction"] == 0.75 and per_kernel[("b", "L2")]["fraction"] == 0.0
    )  # None counts as 0
    assert per_kernel[("a", "B2")]["fraction"] == 0.5 and per_kernel[("b", "B1")]["fraction"] == 1.0
    assert per_kernel[("a", "manual")]["fraction"] == 1.0 and per_kernel[("a", "L1")]["guards_total"] == 4
    contrasts = table["contrasts"]
    assert [c["contrast"] for c in contrasts] == ["L2 - B2", "L1 - L2", "L1 - B1"]
    primary = contrasts[0]
    assert primary["tasks"] == 2 and primary["estimate"] == pytest.approx((0.25 + -0.5) / 2)
    assert all(c["family"] == "preregistered-guards" and c["holm_m"] == 3 for c in contrasts)
    assert all(0 <= c["p_sign"] <= c["p_holm"] <= 1 for c in contrasts)
    assert table["minimum_meaningful_gain"] == 0.20 and table["primary"] == "L2 - B2"


def test_guard_endpoint_skips_conditions_without_rows_and_requires_a_consistent_denominator():
    proposer = [_proposer("L2", "a", 1, 1, total=2), _proposer("L2", "a", 2, 2, total=2)]
    houdini = [{"family": "b2", "kernel": "a", "loop_index": 0, "guards_removed": 0, "guards_total": 2}]
    table = guards.endpoint(proposer, houdini, [], seed=0)
    assert [c["contrast"] for c in table["contrasts"]] == ["L2 - B2"] and table["contrasts"][0]["holm_m"] == 1
    assert {r["condition"] for r in table["kernels"]} == {"L2", "B2"}
    with pytest.raises(ValueError, match="denominator"):
        guards.endpoint(
            proposer,
            [{"family": "b2", "kernel": "a", "loop_index": 0, "guards_removed": 0, "guards_total": 3}],
            [],
            seed=0,
        )
    assert guards.endpoint([], [], [], seed=0) == {
        "kernels": [],
        "contrasts": [],
        "primary": "L2 - B2",
        "minimum_meaningful_gain": 0.20,
    }
