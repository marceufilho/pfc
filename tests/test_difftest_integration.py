"""Real binary agreement, deliberately wrong outputs, and a separate checked trap."""

import os
import signal
from dataclasses import replace
from pathlib import Path

import pytest

from pfc import difftest, smt
from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


@pytest.fixture
def program():
    return lower(parse_source(ROOT / "kernels/sum/kernel.c"))


def test_1000_cases_agree_with_oracle_and_complete_arrays(program):
    report = difftest.run_sum(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), seed=0)
    assert report.status == "passed" and report.compared == report.agreed == 1000
    assert not report.failures and not report.problem
    assert report.proof.required_passed and report.decisions[0].removed
    assert report.dataset_id and report.driver_id and report.checked_c_id != report.verified_c_id
    assert report.environment.clang == report.target.compiler


@pytest.mark.parametrize("mutation", ["common-wrong-return", "array-write"])
def test_comparison_detects_real_compiled_mutants(program, monkeypatch, mutation):
    original = difftest.emit

    def mutated(*args, **kwargs):
        result = original(*args, **kwargs)
        replacement = (
            "return 0;" if mutation == "common-wrong-return" else "if (a_len > 0) ((long *)a)[0] = 0; return s;"
        )
        return replace(result, c=result.c.replace("return s;", replacement))

    monkeypatch.setattr("pfc.difftest.emit", mutated)
    report = difftest.run_sum(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), seed=9)
    assert report.status == "mismatch" and report.agreed < report.compared == 1000
    assert report.failures and report.failures[0].case


def test_compile_failure_is_not_success_and_temp_directory_is_removed(program):
    directories = []

    def fail(arguments, cwd):
        directories.append(cwd)
        return difftest.ProcessResult("error", "", 1, "process exited 1")

    report = difftest.run_sum(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), runner=fail)
    assert report.status == "compile-error" and report.compared == 0
    assert len(directories) == 1 and all(not folder.exists() for folder in directories)


@pytest.mark.parametrize(
    "result,status",
    [
        (difftest.ProcessResult("timeout", "", None, "process timeout"), "timeout"),
        (difftest.ProcessResult("error", "", -signal.SIGILL, "process exited abnormally"), "run-error"),
        (difftest.ProcessResult("ok", "garbage\n", 0, ""), "protocol-error"),
    ],
)
def test_runtime_or_protocol_failure_never_counts_as_pass(program, result, status):
    def runner(arguments, cwd):
        if "-o" in arguments:
            return difftest.ProcessResult("ok", "", 0, "")
        return result

    report = difftest.run_sum(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), runner=runner)
    assert report.status == status and report.compared == 0 and report.agreed == 0


def test_solver_failure_prevents_claiming_a_verified_comparison(program, monkeypatch):
    original = smt.check

    def failed(*args, **kwargs):
        report = original(*args, **kwargs)
        return replace(
            report, required_passed=False, results=(replace(report.results[0], status="unknown"),) + report.results[1:]
        )

    monkeypatch.setattr("pfc.difftest.smt.check", failed)

    def unexpected(*args, **kwargs):
        pytest.fail("must not compile when proof stage fails")

    report = difftest.run_sum(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), runner=unexpected)
    assert report.status == "proof-failed" and report.compared == 0


def test_checked_binary_traps_on_logical_bounds_violation_separately(program, tmp_path):
    compiler = os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
    code = emit(program, read_target(compiler), mode="checked").c
    (tmp_path / "kernel.c").write_text(code)
    (tmp_path / "main.c").write_text("""#include <sys/resource.h>
long sum(const long[],long,long);
int main(void) { struct rlimit limit={0,0}; if(setrlimit(RLIMIT_CORE,&limit)) return 2;
long a[2]={7,9}; return sum(a,1,2)!=16; }""")
    binary = tmp_path / "negative"
    compiled = difftest.invoke(
        (compiler, *difftest.C_FLAGS, str(tmp_path / "kernel.c"), str(tmp_path / "main.c"), "-o", str(binary)), tmp_path
    )
    assert compiled.status == "ok"
    result = difftest.invoke((str(binary),), tmp_path)
    assert result.status == "error" and result.returncode == -signal.SIGILL


def test_incomplete_run_summary_does_not_count_unexecuted_cases(program):
    def fail(arguments, cwd):
        return difftest.ProcessResult("error", "", 1, "process exited 1")

    report = difftest.run_sum(program, compiler=os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), runner=fail)
    output = difftest.render_report(report)
    assert "0/0 agreed" in output and "1000 planned" in output
