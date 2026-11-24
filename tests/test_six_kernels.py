import os
import subprocess
from pathlib import Path

import pytest

from pfc import smt
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["matvec", "binary_search"])
def test_new_profiles_verify_and_generate_defined_cases(name):
    from pfc.corpus import generate, profile, validate

    target = read_target(os.environ.get("PFC_CLANG", "clang-19"))
    p = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    assert smt.check(p, target).required_passed
    assert profile(p) == name
    cases = generate(name, target, seed=5)
    assert len(cases) == 1000
    for case in cases:
        validate(name, case, target)


def test_matrix_oracle_and_duplicate_search_use_independent_expectations():
    from pfc.corpus import Case, matches, oracle
    from pfc.difftest import Observation

    case = Case(((1, 2, 3, 4, 99), (2, 3), (8, 9, 77)), 2, cols=2)
    assert oracle("matvec", case) == Observation(2, (1, 2, 3, 4, 99, 2, 3, 8, 18, 77))
    duplicate = Case(((1, 1, 1, 9),), 3, needle=1)
    assert matches("binary_search", duplicate, Observation(1, duplicate.values))
    assert not matches("binary_search", duplicate, Observation(3, duplicate.values))
    assert not matches("binary_search", duplicate, Observation(-1, duplicate.values))


@pytest.mark.integration
@pytest.mark.parametrize("name", ["matvec", "binary_search"])
def test_new_kernel_differential_and_all_precondition_consequences(name):
    from pfc.breakit import check_kernel
    from pfc.difftest import run_kernel

    compiler = os.environ.get("PFC_CLANG", "clang-19")
    p = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    diff = run_kernel(p, compiler=compiler, seed=7)
    assert diff.status == "passed" and diff.agreed == diff.compared == 1000
    ablation = check_kernel(p, read_target(compiler))
    assert ablation.passed
    assert len(ablation.cases) == len(p.functions[0].contracts)


@pytest.mark.integration
@pytest.mark.parametrize(
    "name,expected",
    [("sum", 35), ("dot", 35), ("saxpy", 39), ("prefix_sum", 50), ("matvec", 20), ("binary_search", 26)],
)
def test_saved_drivers_match_generator_and_run_small_defined_inputs(name, expected, tmp_path):
    from pfc.build import build_source
    from pfc.drivers import benchmark_driver

    source = ROOT / "kernels" / name / "kernel.c"
    p = lower(parse_source(source))
    driver = source.parent / "main.c"
    assert driver.read_text() == benchmark_driver(p)
    assert (source.parent / "inputs.py").is_file()
    result = build_source(
        source, tmp_path / name, mode="checked", compiler=os.environ.get("PFC_CLANG", "clang-19"), driver=driver
    )
    assert result.passed
    execution = subprocess.run(
        [str(tmp_path / name / "program"), "16", "2"], capture_output=True, text=True, timeout=10
    )
    assert execution.returncode == 0 and execution.stdout == f"{expected}\n"
