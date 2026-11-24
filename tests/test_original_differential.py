"""Compile original C independently of pfc lowering/emission, then compare full state.

This detects frontend/emitter disagreement on the corpus. It is not an independent
VC oracle, and intentionally shares the reviewed input generator and ABI driver.
"""

import os
import subprocess
from pathlib import Path

import pytest

from pfc import corpus, difftest
from pfc.config import C_FLAGS
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(corpus.PROFILES))
def test_original_c_and_emitted_plain_match_full_state(name, tmp_path):
    compiler = os.environ.get("PFC_CLANG", "clang-19")
    target = read_target(compiler)
    source = ROOT / "kernels" / name / "kernel.c"
    program = lower(parse_source(source))
    cases = corpus.generate(name, target, seed=23)
    driver = tmp_path / "driver.c"
    driver.write_text(corpus.driver_source(program, cases, target), encoding="utf-8")
    plain = tmp_path / "plain.c"
    plain.write_text(emit(program, target, mode="plain").c, encoding="utf-8")
    # Erasing contracts leaves length parameters unused in the original C.
    # Apply the same warning-only exception to both compilations.
    flags = (*C_FLAGS, "-Wno-unused-parameter")
    observations = []
    for label, input_path in (("original", source), ("plain", plain)):
        binary = tmp_path / label
        subprocess.run(
            [compiler, *flags, "-I", str(ROOT / "include"), str(input_path), str(driver), "-o", str(binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
        observations.append(
            difftest.parse_observations(
                result.stdout, tuple(difftest.SumInput(case.values, case.n) for case in cases), target
            )
        )
    assert len(observations[0]) == len(observations[1]) == len(cases) == 1000
    assert observations[0] == observations[1]
    assert all(corpus.matches(name, case, actual) for case, actual in zip(cases, observations[0], strict=True))
