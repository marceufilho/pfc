"""Independent clang checks for the step-4 printer, without proof claims."""

import os
import subprocess
from pathlib import Path

import pytest

from pfc.config import C_FLAGS, CLANG_ENV_VAR, DEFAULT_CLANG, PROCESS_TIMEOUT_SECONDS
from pfc.ir import print_c, to_data
from pfc.lower import lower
from pfc.parse import parse_source

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def compile_c(*arguments: str):
    return subprocess.run(
        [
            os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG),
            *C_FLAGS,
            "-I",
            str(ROOT / "include"),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def test_sum_round_trip_compiles_and_reparses(tmp_path):
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    output = print_c(program)
    source = tmp_path / "sum.c"
    source.write_text(output)
    result = compile_c("-DSPEC_KEEP", "-c", str(source), "-o", str(tmp_path / "sum.o"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    reconstructed = lower(parse_source(source))
    assert print_c(reconstructed) == output
    assert without_locations(to_data(reconstructed)) == without_locations(to_data(program))
    result = compile_c(
        "-Wno-unused-parameter", str(source), str(ROOT / "kernels/sum/example.c"), "-o", str(tmp_path / "sum")
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(tmp_path / "sum")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout == "136\n" and not result.stderr


def test_original_and_printed_functions_preserve_values_and_mutations(tmp_path):
    source = tmp_path / "cases.c"
    source.write_text("""#include "spec.h"
_Bool truth(long x) { _Bool b = x; return b; }
long arithmetic(long x, long y) { return x - (y - 1) + x / y; }
long guarded(const long a[], long a_len, long n) { return n != 0 && a[0] / n > 0; }
long mutate(long a[], long a_len) {
    long total = 0;
    for (long i = 0; i < a_len; i = i + 1) {
        INVARIANT(0 <= i && i <= a_len);
        if (a[i] > 0) { a[i] = a[i] / 2; } else { a[i] = -a[i]; }
        total = total + a[i];
    }
    while (total < 10) { total = total + 1; }
    return total;
}
""")
    printed = tmp_path / "printed.c"
    printed.write_text(print_c(lower(parse_source(source))))
    assert print_c(lower(parse_source(printed))) == printed.read_text()
    driver = tmp_path / "driver.c"
    driver.write_text("""#include <stdio.h>
_Bool truth(long); long arithmetic(long, long); long guarded(const long[], long, long);
long mutate(long[], long);
int main(void) {
    long a[] = {9, -3, 4, 0};
    if (truth(-4) != 1 || truth(0) != 0) return 1;
    if (arithmetic(-7, 3) != -11 || arithmetic(7, -3) != 9) return 2;
    if (guarded(a, 4, 0) != 0 || guarded(a, 4, 2) != 1) return 3;
    long result = mutate(a, 4);
    if (result != 10 || a[0] != 4 || a[1] != 3 || a[2] != 2 || a[3] != 0) return 4;
    printf("%ld %ld %ld %ld %ld\\n", result, a[0], a[1], a[2], a[3]);
    return 0;
}
""")
    outputs = []
    for index, candidate in enumerate([source, printed]):
        binary = tmp_path / f"cases-{index}"
        result = compile_c("-Wno-unused-parameter", str(candidate), str(driver), "-o", str(binary))
        assert result.returncode == 0, result.stderr
        assert not result.stderr
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs == ["10 4 3 2 0\n", "10 4 3 2 0\n"]


def without_locations(value):
    if isinstance(value, dict):
        return {key: without_locations(item) for key, item in value.items() if key != "location"}
    if isinstance(value, list):
        return [without_locations(item) for item in value]
    return value
