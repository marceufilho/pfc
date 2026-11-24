"""These smoke checks exercise dependencies, not compiler correctness."""

import os
import subprocess

import pytest
import z3
from pycparser import c_parser


def test_pycparser_parses_a_c_function():
    tree = c_parser.CParser().parse("long identity(long x) { return x; }")
    assert tree.ext[0].decl.name == "identity"


@pytest.mark.integration
def test_real_z3_distinguishes_sat_and_unsat():
    value = z3.Int("value")
    solver = z3.Solver()
    solver.add(value > 0, value < 0)
    assert solver.check() == z3.unsat
    solver.reset()
    solver.add(value == 7)
    assert solver.check() == z3.sat
    assert solver.model()[value].as_long() == 7


@pytest.mark.integration
def test_real_clang_compiles_and_runs_c(tmp_path):
    from pfc.config import C_FLAGS, DEFAULT_CLANG

    compiler = os.environ.get("PFC_CLANG", DEFAULT_CLANG)
    source = tmp_path / "smoke.c"
    binary = tmp_path / "smoke"
    source.write_text('#include <stdio.h>\nint main(void) { puts("42"); return 0; }\n')
    result = subprocess.run(
        [compiler, *C_FLAGS, str(source), "-o", str(binary)], capture_output=True, text=True, timeout=10, check=True
    )
    assert not result.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10, check=True)
    assert run.stdout == "42\n"
    assert not run.stderr
