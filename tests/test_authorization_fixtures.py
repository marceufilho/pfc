"""Authorization fixtures: small programs whose per-site decisions, overflow traps and entry checks are hand-derived
(revision plan, phase 2B; ADR-0058). Each fixture is checked with Z3 and compiled in both instrumented modes; the
expectations in cases.json were written before the fixtures were run and are never regenerated from the output."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from pfc import ir, smt
from pfc.config import C_FLAGS, CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.emit import ENTRY_LINE, GUARD_LINE, OVERFLOW_LINE, emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target

FIXTURES = Path(__file__).parent / "fixtures" / "authorization"
CASES = {k: v for k, v in json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8")).items() if k != "_comment"}
COMPILER = os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
SIGILL = -4


def _program(name):
    return lower(parse_source(FIXTURES / f"{name}.c"))


def _emissions(name):
    program = _program(name)
    target = read_target(COMPILER)
    proof = smt.check(program, target)
    return (
        program,
        proof,
        {
            "checked": emit(program, target, mode="checked"),
            "verified": emit(program, target, mode="verified", proof=proof),
        },
    )


def _driver(program, arrays, scalars, observe_trap=False):
    """Host driver; with observe_trap the arrays are globals and a SIGILL handler prints the state it interrupted."""
    function = program.functions[0]
    declarations, arguments = [], []
    for parameter in function.parameters:
        name = parameter.symbol.name
        if parameter.length is not None:
            values = arrays[name]
            initializer = ", ".join(f"{v}L" for v in values) or "0L"
            declarations.append(f"long {name}[{max(len(values), 1)}] = {{{initializer}}};")
            arguments.append(name)
        elif any(p.length is not None and p.length.name == name for p in function.parameters):
            owner = next(p.symbol.name for p in function.parameters if p.length is not None and p.length.name == name)
            arguments.append(f"{len(arrays[owner])}L")
        else:
            value = scalars[name]
            arguments.append(f"({value + 1}L - 1L)" if value == -(2**63) else f"{value}L")
    prototype = ", ".join(ir._declaration(p.symbol) for p in function.parameters)
    call = f'    printf("%ld\\n", {function.name}({", ".join(arguments)}));\n    return 0;\n}}\n'
    if not observe_trap:
        body = "\n".join(f"    {d}" for d in declarations)
        return f"#include <stdio.h>\nlong {function.name}({prototype});\nint main(void) {{\n{body}\n{call}"
    dumps = []
    for name, values in arrays.items():
        cells = "".join(f'    printf("%s%ld", "{"" if i == 0 else ","}", {name}[{i}]);\n' for i in range(len(values)))
        dumps.append(f'    printf(" {name}=");\n{cells}')
    return (
        "#include <signal.h>\n#include <stdio.h>\n#include <unistd.h>\n"
        f"long {function.name}({prototype});\n"
        + "\n".join(f"static {d}" for d in declarations)
        + '\nstatic void on_trap(int signal_number) {\n    (void)signal_number;\n    printf("sigill");\n'
        + "".join(dumps)
        + '    printf("\\n");\n    fflush(stdout);\n    _exit(0);\n}\n'
        f"int main(void) {{\n    signal(SIGILL, on_trap);\n{call}"
    )


def _operator(condition):
    """Operator of an overflow site from its builtin call: add, mul, sub, or sub from a zero literal (negation)."""
    builtin = condition.split("(")[0].removeprefix("__builtin_").removesuffix("_overflow")
    if builtin == "add":
        return "+"
    if builtin == "mul":
        return "*"
    first = condition.split("(", 1)[1].split(",")[0].strip()
    return "neg" if first in ("0", "0L") else "-"


def test_every_fixture_is_listed_in_cases_json():
    sources = {p.stem for p in FIXTURES.glob("*.c")}
    assert sources == set(CASES) and sources


@pytest.mark.parametrize("name", sorted(CASES))
def test_fixture_decisions_match_expectations(name):
    expected = CASES[name]
    program, proof, emissions = _emissions(name)
    verified = emissions["verified"]
    assert proof.required_passed is expected["required_passed"]
    assert [(d.kind, d.removed) for d in verified.decisions] == [
        (d["kind"], d["removed"]) for d in expected["decisions"]
    ], [(d.kind, d.location.line, d.location.column, d.removed, d.reason) for d in verified.decisions]
    assert [(_operator(d.condition), d.removed) for d in verified.overflow] == [
        (o["operator"], o["removed"]) for o in expected["overflow"]
    ], [(d.condition, d.removed, d.reason) for d in verified.overflow]
    assert sum(bool(c.rendered) for c in verified.requires_checks) == expected["requires_rendered"]
    assert len(ENTRY_LINE.findall(verified.c)) == expected["requires_rendered"]
    checked = emissions["checked"]
    assert len(GUARD_LINE.findall(checked.c)) == len(expected["decisions"])
    assert len(OVERFLOW_LINE.findall(checked.c)) == len(expected["overflow"]) and not ENTRY_LINE.findall(checked.c)
    assert len(GUARD_LINE.findall(verified.c)) == sum(not d["removed"] for d in expected["decisions"])
    assert len(OVERFLOW_LINE.findall(verified.c)) == sum(not o["removed"] for o in expected["overflow"])
    removed = [d for d in verified.decisions if d.removed] + [d for d in verified.overflow if d.removed]
    assert all(d.obligation_ids and f"// proved: obligation {d.obligation_ids[0]}" in verified.c for d in removed)


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(CASES))
def test_fixture_runtime_observations(name, tmp_path):
    program, _, emissions = _emissions(name)
    for index, run in enumerate(CASES[name]["runs"]):
        driver = _driver(program, run["arrays"], run["scalars"], run.get("observe_trap", False))
        for mode, expectation in run["modes"].items():
            directory = tmp_path / f"{index}-{mode}"
            directory.mkdir()
            (directory / "kernel.c").write_text(emissions[mode].c, encoding="utf-8")
            (directory / "main.c").write_text(driver, encoding="utf-8")
            compiled = subprocess.run(
                [
                    COMPILER,
                    *C_FLAGS,
                    str(directory / "kernel.c"),
                    str(directory / "main.c"),
                    "-o",
                    str(directory / "p"),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert compiled.returncode == 0 and not compiled.stderr, (name, mode, compiled.stderr)
            result = subprocess.run([str(directory / "p")], capture_output=True, text=True, timeout=60)
            observed = "sigill" if result.returncode == SIGILL else result.stdout.strip()
            assert observed == expectation, (name, index, mode, result.returncode, result.stdout)
