"""Independent CI layers, each parameterized over all six reviewed kernels."""

import os
from pathlib import Path

import pytest

from pfc.breakit import check_kernel
from pfc.corpus import PROFILES
from pfc.difftest import run_kernel
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(PROFILES))
def test_six_differential(name):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    result = run_kernel(program, compiler=os.environ.get("PFC_CLANG", "clang-19"), seed=19)
    assert result.status == "passed" and result.agreed == result.compared == 1000


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(PROFILES))
def test_six_breakit(name):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    result = check_kernel(program, read_target(os.environ.get("PFC_CLANG", "clang-19")))
    assert result.passed and len(result.cases) == len(program.functions[0].contracts)
