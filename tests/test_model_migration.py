"""Current sampling must not reinterpret or invalidate historical experiments."""

import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from pfc import ir
from pfc.corpus import PROFILES
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.propose.experiment import KernelRun, _recheck, load, summarize
from pfc.propose.llm import Exchange, Request, Settings, complete
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


def test_historical_experiment_keeps_its_original_configuration(tmp_path):
    rows = tuple(KernelRun("sum", i, PROFILES["sum"], False, 1, 0, 0, 1, 1, 0, True) for i in (1, 2, 3))
    old = summarize("legacy", "live", rows, settings=Settings.legacy())
    (tmp_path / "experiment.json").write_text(json.dumps(ir.to_data(old)))
    assert load(tmp_path) == old
    assert old.settings.model == "gpt-5.4-2026-03-05"
    assert old.settings != Settings()


def test_historical_response_is_reproved_with_recorded_settings(tmp_path):
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    target = read_target(os.environ.get("PFC_CLANG", "clang-19"))
    legacy = Settings.legacy()
    record = {
        "prompt": "historical prompt",
        "sample_id": "legacy/run-1/sum/1",
        "settings": ir.to_data(legacy),
        "exchange": ir.to_data(Exchange('{"invariants":["0<=i && i<=n"]}', legacy.model, "old", 1, 1, "live")),
    }
    result = _recheck(program, target, [record], tmp_path / "replay.jsonl", 10000, settings=legacy)
    assert result.found
    assert result.attempts[0].request.settings == legacy
    assert result.attempts[0].exchange.origin == "replay"
    with pytest.raises(ValueError, match="replay request"):
        _recheck(program, target, [record], tmp_path / "wrong.jsonl", 10000, settings=Settings())


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", 0.2),
        ("reasoning_effort", "none"),
        ("max_output_tokens", 2048.0),
        ("protocol", "pfc-invariants-v2"),
    ],
)
def test_mixed_or_malformed_protocol_is_rejected(field, value):
    with pytest.raises(ValueError, match="protocol"):
        replace(Settings(), **{field: value})


def test_serialized_configuration_is_not_an_arbitrary_model_selector():
    for settings in (Settings(), Settings.legacy()):
        assert Settings.from_data(ir.to_data(settings)) == settings
        assert Settings.from_data(asdict(settings)) == settings
    for malformed in (
        None,
        [],
        {**asdict(Settings()), "model": "unreviewed"},
        {**asdict(Settings()), "node": "Other"},
        {**asdict(Settings()), "extra": 1},
    ):
        with pytest.raises(ValueError, match="protocol"):
            Settings.from_data(malformed)


def test_model_migration_cannot_reuse_historical_response_cache(tmp_path):
    request = Request("same prompt", "run-1", Settings.legacy())
    complete(
        request,
        tmp_path,
        live=True,
        send=lambda r: Exchange('{"invariants":["1"]}', r.settings.model, "old", 1, 1, "test"),
    )
    with pytest.raises(ValueError, match="cache miss"):
        complete(replace(request, settings=Settings()), tmp_path, live=False)
