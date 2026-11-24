"""Replay of stored witnesses on weakened programs (reframing, W8): the faulty program must be stopped at run time."""

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("witness_replay", ROOT / "tools/witness_replay.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_summary_counts_traps_per_build() -> None:
    rows = [
        {"checked": "trap", "verified": "trap", "checked_stop": "witnessed guard", "verified_stop": "witnessed guard"},
        {"checked": "trap", "verified": "returned", "checked_stop": "another guard", "verified_stop": "returned"},
        {"checked": "trap", "verified": "not executed", "checked_stop": "other trap", "verified_stop": "not executed"},
    ]
    summary = _module().summarize(rows)
    assert summary["witnesses"] == 3 and summary["checked_trap"] == 3 and summary["verified_trap"] == 1
    assert summary["passed"] is False
    assert summary["checked_stopped_by_witnessed_guard"] == 1 and summary["verified_stopped_by_witnessed_guard"] == 1
