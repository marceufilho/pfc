"""The actual benchmark flags and enabled facts need full-output validation too."""

import os
from pathlib import Path

import pytest

from pfc import difftest
from pfc.config import BENCH_FLAGS
from pfc.corpus import PROFILES
from pfc.emit import emit
from pfc.facts import ALL_FACTS
from pfc.lower import lower
from pfc.parse import parse_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(PROFILES))
def test_benchmark_flags_and_all_facts_preserve_full_observations(name, monkeypatch):
    def with_all_facts(*args, **kwargs):
        return emit(*args, **kwargs, facts=ALL_FACTS)

    # Substitute configuration only: compilation, execution, Z3 and the oracle are real.
    monkeypatch.setattr(difftest, "C_FLAGS", BENCH_FLAGS)
    monkeypatch.setattr(difftest, "emit", with_all_facts)
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    result = difftest.run_kernel(program, compiler=os.environ.get("PFC_CLANG", "clang-19"), seed=19)
    assert result.flags == BENCH_FLAGS
    assert set(result.emissions[-1].facts) == ALL_FACTS
    assert result.status == "passed"
    assert result.compared == result.agreed == result.cases == 1000
    assert not result.failures
