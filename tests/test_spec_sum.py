"""Step 2: C annotation transport and a runnable, unverified sum example."""

import os
import subprocess
from pathlib import Path

import pytest
from pycparser import c_ast, c_parser

from pfc.config import C_FLAGS, CLANG_ENV_VAR, DEFAULT_CLANG, PROCESS_TIMEOUT_SECONDS

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "kernels" / "sum" / "kernel.c"
DRIVER = ROOT / "kernels" / "sum" / "example.c"
# Erased contracts can be the only use of a length parameter. Keep other warnings as errors.
ANNOTATED_C_FLAGS = (*C_FLAGS, "-Wno-unused-parameter")
MODES = [pytest.param((), id="plain"), pytest.param(("-DVERIFIED",), id="verified-selector")]
pytestmark = pytest.mark.integration


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=PROCESS_TIMEOUT_SECONDS, check=False)


def _compile(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG),
            *ANNOTATED_C_FLAGS,
            "-I",
            str(ROOT / "include"),
            "-I",
            str(KERNEL.parent),
            *arguments,
        ]
    )


@pytest.mark.parametrize("defines", [*MODES, pytest.param(("-DSPEC_KEEP",), id="spec-keep")])
def test_kernel_compiles_in_each_annotation_mode(tmp_path: Path, defines: tuple[str, ...]) -> None:
    result = _compile([*defines, "-c", str(KERNEL), "-o", str(tmp_path / "sum.o")])
    assert result.returncode == 0, result.stderr
    assert not result.stderr


def test_cpp_preserves_sum_contracts_and_source_locations() -> None:
    result = _run(["cpp", "-std=c11", "-DSPEC_KEEP", "-I", str(ROOT / "include"), str(KERNEL)])
    assert result.returncode == 0, result.stderr
    tree = c_parser.CParser().parse(result.stdout)
    function = next(node for node in tree.ext if isinstance(node, c_ast.FuncDef))
    assert function.decl.name == "sum"

    class Sites(c_ast.NodeVisitor):
        def __init__(self) -> None:
            self.calls: list[c_ast.FuncCall] = []
            self.indexes: list[c_ast.ArrayRef] = []
            self.divisions: list[c_ast.BinaryOp] = []

        def visit_FuncCall(self, node: c_ast.FuncCall) -> None:
            self.calls.append(node)
            self.generic_visit(node)

        def visit_ArrayRef(self, node: c_ast.ArrayRef) -> None:
            self.indexes.append(node)
            self.generic_visit(node)

        def visit_BinaryOp(self, node: c_ast.BinaryOp) -> None:
            if node.op == "/":
                self.divisions.append(node)
            self.generic_visit(node)

    sites = Sites()
    sites.visit(function)
    assert [node.name.name for node in sites.calls] == ["__spec_requires", "__spec_invariant"]
    for call, names in [(sites.calls[0], ["n", "n", "a_len"]), (sites.calls[1], ["i", "i", "n"])]:
        condition = call.args.exprs[0]
        assert condition.op == "&&"
        assert condition.left.op == condition.right.op == "<="
        assert condition.left.left.value == "0"
        assert [condition.left.right.name, condition.right.left.name, condition.right.right.name] == names
    assert len(sites.indexes) == 1
    assert not sites.divisions
    source_lines = KERNEL.read_text().splitlines()
    for node, text in [
        (sites.calls[0], "REQUIRES(0 <= n && n <= a_len)"),
        (sites.calls[1], "INVARIANT(0 <= i && i <= n)"),
        (sites.indexes[0], "s = s + a[i]"),
    ]:
        assert Path(node.coord.file) == KERNEL
        assert text in source_lines[node.coord.line - 1]


def test_all_markers_compile_and_keep_arguments(tmp_path: Path) -> None:
    source = tmp_path / "contracts.c"
    source.write_text(
        '#include "spec.h"\n#include "spec.h"\n'
        "long example(const long a[], const long b[], long n) {\n"
        "    REQUIRES(n >= 0 && SEPARATED(a, n, b, n));\n"
        "    INVARIANT(n >= 0); ASSERT(n != 7); ENSURES(n >= 0); return n;\n}\n"
    )
    result = _compile(["-DSPEC_KEEP", "-c", str(source), "-o", str(tmp_path / "contracts.o")])
    assert result.returncode == 0, result.stderr
    result = _run(["cpp", "-DSPEC_KEEP", "-I", str(ROOT / "include"), str(source)])
    assert result.returncode == 0, result.stderr
    tree = c_parser.CParser().parse(result.stdout)
    function = next(node for node in tree.ext if isinstance(node, c_ast.FuncDef))
    requires, invariant, assertion, ensures, _ = function.body.block_items
    assert [node.name.name for node in (requires, invariant, assertion, ensures)] == [
        "__spec_requires",
        "__spec_invariant",
        "__spec_assert",
        "__spec_ensures",
    ]
    condition = requires.args.exprs[0]
    assert condition.op == "&&"
    separated = condition.right
    assert separated.name.name == "__spec_separated"
    assert [argument.name for argument in separated.args.exprs] == ["a", "n", "b", "n"]
    for call, operator, value in [(invariant, ">=", "0"), (assertion, "!=", "7"), (ensures, ">=", "0")]:
        expression = call.args.exprs[0]
        assert expression.op == operator
        assert expression.left.name == "n"
        assert expression.right.value == value


@pytest.mark.parametrize("defines", MODES)
def test_erased_annotations_neither_evaluate_nor_assume(tmp_path: Path, defines: tuple[str, ...]) -> None:
    source = tmp_path / "erased.c"
    source.write_text(
        '#include "spec.h"\n#include "spec.h"\n'
        "int main(void) {\n"
        "    long counter = 0;\n"
        "    REQUIRES(++counter); INVARIANT(++counter); ENSURES(++counter); ASSERT(++counter);\n"
        "    REQUIRES(0); INVARIANT(0); ENSURES(0); ASSERT(0);\n"
        "    SEPARATED(&counter, ++counter, &counter, ++counter);\n"
        "    return counter != 0;\n}\n"
    )
    binary = tmp_path / "erased"
    result = _compile([*defines, str(source), "-o", str(binary)])
    assert result.returncode == 0, result.stderr
    result = _run([str(binary)])
    assert result.returncode == 0, result.stderr
    # Inspect preprocessing too: optimizing UB away is not acceptable evidence of non-evaluation.
    preprocessed = _compile([*defines, "-E", "-P", str(source)])
    assert preprocessed.returncode == 0, preprocessed.stderr
    assert "__builtin_assume" not in preprocessed.stdout
    assert "__spec_" not in preprocessed.stdout
    assert "++counter" not in preprocessed.stdout


def test_conflicting_annotation_modes_are_rejected(tmp_path: Path) -> None:
    result = _compile(["-DSPEC_KEEP", "-DVERIFIED", "-c", str(KERNEL), "-o", str(tmp_path / "sum.o")])
    assert result.returncode != 0
    assert "SPEC_KEEP and VERIFIED are mutually exclusive" in result.stderr


def test_spec_keep_is_not_an_executable_contract_runtime(tmp_path: Path) -> None:
    result = _compile(["-DSPEC_KEEP", str(KERNEL), str(DRIVER), "-o", str(tmp_path / "sum")])
    assert result.returncode != 0
    assert "__spec_requires" in result.stderr


@pytest.mark.parametrize("defines", MODES)
def test_sum_handles_valid_prefixes_without_mutating_the_array(tmp_path: Path, defines: tuple[str, ...]) -> None:
    source = tmp_path / "sum_cases.c"
    source.write_text(
        '#include <limits.h>\n#include "kernel.h"\n'
        "int main(void) {\n"
        "    long values[] = {5, -2, 7, -4};\n"
        "    long high[] = {LONG_MAX, -1};\n"
        "    long low[] = {LONG_MIN, 1};\n"
        "    if (sum(values, 4, 0) != 0) return 1;\n"
        "    if (sum(values, 4, 1) != 5) return 2;\n"
        "    if (sum(values, 4, 3) != 10) return 3;\n"
        "    if (sum(values, 4, 4) != 6) return 4;\n"
        "    if (sum(values, 0, 0) != 0) return 5;\n"
        "    if (sum(high, 2, 2) != LONG_MAX - 1) return 6;\n"
        "    if (sum(low, 2, 2) != LONG_MIN + 1) return 7;\n"
        "    if (values[0] != 5 || values[1] != -2 || values[2] != 7 || values[3] != -4) return 8;\n"
        "    if (high[0] != LONG_MAX || high[1] != -1 || low[0] != LONG_MIN || low[1] != 1) return 9;\n"
        "    return 0;\n}\n"
    )
    binary = tmp_path / "sum_cases"
    result = _compile([*defines, str(KERNEL), str(source), "-o", str(binary)])
    assert result.returncode == 0, result.stderr
    result = _run([str(binary)])
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr


@pytest.fixture(params=[(), ("-DVERIFIED",)], ids=["plain", "verified-selector"])
def sum_driver(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    binary = tmp_path / "sum-demo"
    result = _compile([*request.param, str(KERNEL), str(DRIVER), "-o", str(binary)])
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    return binary


def test_driver_prints_known_sum(sum_driver: Path) -> None:
    result = _run([str(sum_driver)])
    assert result.returncode == 0, result.stderr
    assert result.stdout == "136\n"
    assert not result.stderr


def test_driver_help(sum_driver: Path) -> None:
    result = _run([str(sum_driver), "--help"])
    assert result.returncode == 0
    assert "usage:" in result.stdout and "Example:" in result.stdout
    assert not result.stderr


@pytest.mark.parametrize("arguments", [["--unknown"], ["--help", "extra"], [""], ["16"]])
def test_driver_rejects_unexpected_arguments(sum_driver: Path, arguments: list[str]) -> None:
    result = _run([str(sum_driver), *arguments])
    assert result.returncode == 2
    assert not result.stdout
    assert "usage:" in result.stderr
