"""Compile emitted C with identical flags; exercise guards before dangerous operations."""

import os
import resource
import signal
import subprocess
from pathlib import Path

import pytest

from pfc import smt
from pfc.config import C_FLAGS, CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def compile_run(tmp_path, code, driver, *arguments):
    source = tmp_path / "emitted.c"
    source.write_text(code)
    main = tmp_path / "main.c"
    main.write_text(driver)
    binary = tmp_path / "program"
    compiled = subprocess.run(
        [os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), *C_FLAGS, str(source), str(main), "-o", str(binary)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert compiled.returncode == 0, compiled.stderr
    assert not compiled.stderr
    return subprocess.run([str(binary), *arguments], capture_output=True, text=True, timeout=10, preexec_fn=no_core)


def unit(text):
    return lower(parse_preprocessed(text, "case.c"))


@pytest.mark.parametrize("mode", ["checked", "verified"])
def test_sum_modes_compile_and_run_with_identical_flags(tmp_path, mode):
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    target = read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    report = emit(program, target, mode=mode, proof=smt.check(program, target))
    driver = """#include <stdio.h>
long sum(const long[], long, long);
int main(void) { long a[16]; for (long i=0;i<16;i++) a[i]=i+1;
printf("%ld\\n",sum(a,16,16)); return 0; }"""
    result = compile_run(tmp_path, report.c, driver)
    assert result.returncode == 0 and result.stdout == "136\n"


@pytest.mark.parametrize("mode", ["checked", "verified"])
def test_failed_bounds_guard_traps_even_with_physically_accessible_array(tmp_path, mode):
    program = unit("long f(const long a[], long a_len, long i) { return a[i]; }")
    target = read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    report = emit(program, target, mode=mode, proof=smt.check(program, target))
    driver = """long f(const long[],long,long);
int main(void) { long a[2]={7,9}; return f(a,1,1)==9 ? 0 : 1; }"""
    assert compile_run(tmp_path, report.c, driver).returncode == -signal.SIGILL


@pytest.mark.parametrize("mode", ["checked", "verified"])
@pytest.mark.parametrize("values", ["7,0", "LONG_MIN,-1"])
def test_division_traps_before_zero_or_minimum_overflow(tmp_path, mode, values):
    program = unit("long f(long x, long y) { return x/y; }")
    target = read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    code = emit(program, target, mode=mode, proof=smt.check(program, target)).c
    driver = f"#include <limits.h>\nlong f(long,long); int main(void) {{ return f({values})!=0; }}"
    assert compile_run(tmp_path, code, driver).returncode == -signal.SIGILL


@pytest.mark.parametrize("mode", ["checked", "verified"])
def test_short_circuit_nested_read_and_negative_division(tmp_path, mode):
    program = unit("""long f(const long a[], long a_len, long n) {
        return n==0 || (n!=0 && a[a[0]]/n == -2); }""")
    target = read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    report = emit(program, target, mode=mode, proof=smt.check(program, target))
    driver = """long f(const long[],long,long);
int main(void) { long a[2]={1,-7}; if(f(a,0,0)!=1) return 1; return f(a,2,3)!=1; }"""
    assert compile_run(tmp_path, report.c, driver).returncode == 0
    assert len(report.decisions) == 3


def test_while_guard_checks_repeat_at_each_iteration(tmp_path):
    program = unit("""long f(const long a[], long a_len) {
        long i=0; while(a[i]>0) { __spec_invariant(0<=i); i=i+1; } return i; }""")
    target = read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    report = emit(program, target, mode="checked")
    driver = """long f(const long[],long);
int main(void) { long a[2]={1,0}; return f(a,1)!=1; }"""
    assert compile_run(tmp_path, report.c, driver).returncode == -signal.SIGILL


def test_generated_names_and_bool_promotions_preserve_values(tmp_path):
    program = unit("""long f(long pfc_tmp_1) {
        _Bool b=pfc_tmp_1; long pfc_tmp_2=b+1; return pfc_tmp_2/2; }""")
    target = read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    code = emit(program, target, mode="checked").c
    driver = "long f(long); int main(void) { return f(-7)!=1 || f(0)!=0; }"
    assert compile_run(tmp_path, code, driver).returncode == 0


def test_compilation_rejects_a_different_integer_target(tmp_path):
    from dataclasses import replace

    target = read_target(os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    code = emit(unit("long f(long n) { return n; }"), replace(target, long_bits=32), mode="checked").c
    source = tmp_path / "wrong-target.c"
    source.write_text(code)
    result = subprocess.run(
        [os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG), *C_FLAGS, "-c", str(source), "-o", str(tmp_path / "wrong.o")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0 and "pfc target widths mismatch" in result.stderr
