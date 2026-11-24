"""E2 integration: the interpreter agrees with the corpus oracles and with the plain build (clang)."""

import os
from pathlib import Path

import pytest

from pfc import corpus, difftest
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import interpret
from pfc_eval.interpret import Inputs, Observation

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")
# Scalar parameters after the array/length pairs, in signature order (hand-written, per reviewed profile).
SCALARS = {
    "sum": lambda case: {"n": case.n},
    "dot": lambda case: {"n": case.n},
    "saxpy": lambda case: {"alpha": case.alpha, "n": case.n},
    "prefix_sum": lambda case: {"n": case.n},
    "matvec": lambda case: {"rows": case.n, "cols": case.cols},
    "binary_search": lambda case: {"n": case.n, "needle": case.needle},
}


def inputs(program, name, case):
    arrays = {}
    scalars = SCALARS[name](case)
    names = [p.symbol.name for p in program.functions[0].parameters if p.length is not None]
    for array_name, values in zip(names, case.arrays, strict=True):
        arrays[array_name] = list(values)
        scalars[f"{array_name}_len"] = len(values)
    return Inputs(scalars, arrays)


def flatten(observation):
    return difftest.Observation(observation.result, tuple(v for values in observation.arrays.values() for v in values))


@pytest.fixture(scope="module")
def target():
    return read_target(COMPILER)


@pytest.mark.integration
@pytest.mark.parametrize("name", KERNELS)
def test_interpreter_reproduces_the_corpus_oracle_on_every_seeded_case(name, target):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    cases = corpus.generate(name, target, seed=0)
    assert len(cases) == 1000
    for case in cases:
        observation = interpret.run(program, inputs(program, name, case), target=target)
        assert isinstance(observation, Observation), (case, observation)
        assert observation.annotation_violations == ()
        assert corpus.matches(name, case, flatten(observation)), case


@pytest.mark.integration
@pytest.mark.parametrize("name", KERNELS)
def test_interpreter_agrees_with_the_plain_build(name, target, tmp_path):
    program = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    cases = corpus.generate(name, target, seed=1)
    driver = tmp_path / "driver.c"
    driver.write_text(corpus.driver_source(program, cases, target), encoding="utf-8")
    compiled, executed = difftest.execute_variant(
        emit(program, target, mode="plain"), driver, tmp_path, COMPILER, difftest.invoke
    )
    assert compiled.status == "ok" and executed is not None and executed.status == "ok"
    lines = executed.stdout.splitlines()
    assert len(lines) == 1000
    disagreements = []
    for row, (case, line) in enumerate(zip(cases, lines, strict=True)):
        fields = [int(x) for x in line.split()]
        observation = interpret.run(program, inputs(program, name, case), target=target)
        assert isinstance(observation, Observation), (row, observation)
        expected = (row, observation.result, sum(len(a) for a in case.arrays), *flatten(observation).values)
        if fields != list(expected):
            disagreements.append(row)
    assert disagreements == []


@pytest.mark.integration
def test_deliberately_overflowing_case_is_rejected_not_executed(target):
    program = lower(parse_source(ROOT / "kernels" / "sum" / "kernel.c"))
    high = target.long_range()[1]
    rejection = interpret.run(program, Inputs({"a_len": 2, "n": 2}, {"a": [high, 1]}), target=target)
    assert isinstance(rejection, interpret.Rejection) and rejection.kind == "Overflow"
    assert rejection.location.line == 12
