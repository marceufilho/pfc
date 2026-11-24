import os
import subprocess

import pytest

from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.environment import ToolchainError
from pfc.target import Target, read_target


@pytest.mark.integration
def test_target_limits_match_actual_c_limits(tmp_path):
    compiler = os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
    target = read_target(compiler)
    source = tmp_path / "limits.c"
    source.write_text(
        "#include <limits.h>\n#include <stdio.h>\n"
        'int main(void) { printf("%d %d %ld %ld\\n", INT_MIN, INT_MAX, LONG_MIN, LONG_MAX); }\n'
    )
    binary = tmp_path / "limits"
    subprocess.run(
        [compiler, "-std=c11", str(source), "-o", str(binary)], capture_output=True, text=True, check=True, timeout=10
    )
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=10)
    assert list(map(int, result.stdout.split())) == [
        -(2 ** (target.int_bits - 1)),
        2 ** (target.int_bits - 1) - 1,
        -(2 ** (target.long_bits - 1)),
        2 ** (target.long_bits - 1) - 1,
    ]
    assert "clang" in target.compiler and target.triple


@pytest.mark.parametrize(("int_bits", "long_bits"), [(0, 64), (32, 16), (True, 64), (32, 10000)])
def test_reject_unsupported_target_widths(int_bits, long_bits):
    with pytest.raises(ValueError):
        Target("clang", "test", int_bits, long_bits)


def test_target_query_rejects_missing_macros(monkeypatch):
    def run(arguments, **kwargs):
        output = "clang version test" if "--version" in arguments else "test-target"
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr("pfc.environment.subprocess.run", run)
    with pytest.raises(ToolchainError):
        read_target("clang")


def test_nul_compiler_is_a_controlled_toolchain_error():
    with pytest.raises(ToolchainError):
        read_target("bad\x00compiler")


def test_compiler_probe_cannot_inherit_dependency_output(monkeypatch):
    monkeypatch.setenv("DEPENDENCIES_OUTPUT", "/private/should-not-write")

    def run(arguments, **kwargs):
        assert "DEPENDENCIES_OUTPUT" not in kwargs["env"]
        output = "clang version test" if "--version" in arguments else "test-target"
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr("pfc.environment.subprocess.run", run)
    with pytest.raises(ToolchainError):
        read_target("clang")
