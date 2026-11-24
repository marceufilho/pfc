"""Named negative fixtures and six-kernel frontend acceptance, not proof claims."""

import json
import os
from pathlib import Path

import pytest

from pfc import ir, subset
from pfc.cli import main
from pfc.config import C_FLAGS, CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.diagnostics import FrontendError
from pfc.difftest import invoke
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.vc import generate

ROOT = Path(__file__).resolve().parents[1]
NEGATIVE = Path(__file__).parent / "negative"
CASES = json.loads((NEGATIVE / "cases.json").read_text())
KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["file"])
def test_negative_fixture_has_exact_rule_and_position(case):
    with pytest.raises(FrontendError) as caught:
        subset.validate(parse_source(NEGATIVE / case["file"]))
    diagnostic = caught.value.diagnostic
    assert diagnostic.rule == case["rule"]
    assert diagnostic.location.file == str((NEGATIVE / case["file"]).resolve())
    assert (diagnostic.location.line, diagnostic.location.column) == (case["line"], case["column"])


@pytest.mark.parametrize("name", KERNELS)
@pytest.mark.integration
def test_six_kernels_validate_roundtrip_and_compile(name, tmp_path):
    source = ROOT / "kernels" / name / "kernel.c"
    parsed = parse_source(source)
    program = subset.validate(parsed)
    assert program == lower(parsed) and program.verification == "not-run"
    assert program.functions[0].name == name
    printed = ir.print_c(program)
    assert printed == (ROOT / "tests/golden" / f"{name}-ir.c").read_text()
    rebuilt = tmp_path / "kernel.c"
    rebuilt.write_text(printed)
    assert ir.print_c(subset.validate(parse_source(rebuilt))) == printed
    result = invoke(
        (
            os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG),
            *C_FLAGS,
            "-DSPEC_KEEP",
            "-I",
            str(ROOT / "include"),
            "-c",
            str(rebuilt),
            "-o",
            str(tmp_path / "kernel.o"),
        ),
        tmp_path,
    )
    assert result.status == "ok"


def test_direct_calls_have_typed_arguments_and_forward_declarations(tmp_path):
    program = subset.validate(
        parse_preprocessed(
            """long twice(long n);
long caller(long n) { long value = twice(n); return value; }
long twice(long n) { return n + n; }
""",
            "calls.c",
        )
    )
    call = program.functions[0].body.statements[0].initializer
    assert isinstance(call, ir.Call) and call.function == "twice"
    assert call.type is ir.ScalarType.LONG and call.arguments[0].type is ir.ScalarType.LONG
    printed = ir.print_c(program)
    assert printed.index("long twice(long n);") < printed.index("long caller(long n)\n{")
    source = tmp_path / "calls.c"
    source.write_text(printed)
    assert ir.print_c(subset.validate(parse_source(source))) == printed


def test_array_call_preserves_types_and_length_arguments():
    program = subset.validate(parse_preprocessed("""long first(const long a[], long a_len) { return a[0]; }
long f(long b[], long b_len) { return first(b, b_len); }"""))
    call = program.functions[1].body.statements[0].value
    assert isinstance(call, ir.Call) and isinstance(call.arguments[0].type, ir.ArrayType)
    assert call.arguments[1].symbol.name == "b_len"


def test_separated_transport_does_not_itself_claim_a_proof():
    program = subset.validate(parse_preprocessed("""long f(long a[], long a_len, long b[], long b_len) {
__spec_requires(__spec_separated(a, a_len, b, b_len)); return 0; }"""))
    predicate = program.functions[0].contracts[0].condition
    assert isinstance(predicate, ir.Separated) and predicate.type is ir.ScalarType.BOOL
    assert "SEPARATED(a, a_len, b, b_len)" in ir.print_c(program)
    assert generate(program).verification == "not-run"


@pytest.mark.parametrize(
    "source,rule",
    [
        ("long f(long n) { return missing(n); }", "subset-call"),
        ("long g(long n) { return n; } long f(void) { return g(); }", "subset-call-arity"),
        (
            "long g(long a[], long a_len) { return 0; } long f(const long b[], long b_len) { return g(b,b_len); }",
            "subset-call-type",
        ),
        ("long g(long n) { return n; } long f(long n) { return g(n)+1; }", "subset-call-position"),
        ("long g(long n) { return n; } long f(long n) { return g(g(n)); }", "subset-call-position"),
        ("long g(long n) { return n; } long f(long g) { return g(0); }", "subset-indirect-call"),
        ("long g(long n) { return n; } long f(long n) { __spec_requires(g(n)); return n; }", "contract-effect"),
        ("long f(long n) { __spec_requires(__spec_separated(n,n,n,n)); return n; }", "subset-separated"),
    ],
)
def test_call_and_predicate_boundary(source, rule):
    with pytest.raises(FrontendError) as caught:
        subset.validate(parse_preprocessed(source, "boundary.c"))
    assert caught.value.diagnostic.rule == rule


def test_cli_reports_negative_fixture_and_keeps_stdout_empty(capsys):
    assert main(["ir", str(NEGATIVE / "pointer-arith.c"), "--json"]) == 1
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["error"]["rule"] == "subset-pointer-arith"


def test_new_kernels_generate_conditions_without_claiming_verification():
    for name in ("saxpy", "prefix_sum", "matvec", "binary_search"):
        program = subset.validate(parse_source(ROOT / "kernels" / name / "kernel.c"))
        report = generate(program)
        assert report.verification == "not-run"
        assert any(item.kind == "bounds" for item in report.obligations)


@pytest.mark.parametrize(
    "source,rule",
    [
        ("long f(long n) { return later(n); } long later(long n) { return n; }", "subset-call-declaration"),
        ("long g(long n); long f(long n) { return g(n); } long g(long n) { return f(n); }", "subset-recursion"),
    ],
)
def test_call_visibility_and_indirect_recursion(source, rule):
    with pytest.raises(FrontendError) as caught:
        subset.validate(parse_preprocessed(source, "graph.c"))
    assert caught.value.diagnostic.rule == rule


@pytest.mark.integration
def test_calls_compile_and_execute_after_roundtrip(tmp_path):
    program = subset.validate(parse_preprocessed("""long twice(long n);
long caller(long n) { long value = twice(n); return value; }
long twice(long n) { return n + n; }"""))
    source = tmp_path / "calls.c"
    source.write_text(ir.print_c(program))
    driver = tmp_path / "main.c"
    driver.write_text("long caller(long); int main(void) { return caller(21) != 42; }")
    binary = tmp_path / "calls"
    compiled = invoke(
        (
            os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG),
            *C_FLAGS,
            "-I",
            str(ROOT / "include"),
            str(source),
            str(driver),
            "-o",
            str(binary),
        ),
        tmp_path,
    )
    assert compiled.status == "ok"
    assert invoke((str(binary),), tmp_path).status == "ok"


def test_vc_never_silently_skips_a_call_expression():
    # The current generator must reject this even if a future caller slices the multi-function IR.
    from dataclasses import replace

    program = subset.validate(parse_preprocessed("long g(long n) { return n; } long f(long n) { return g(n); }"))
    caller = replace(program, functions=(program.functions[1],))
    with pytest.raises(FrontendError, match="vc-expression"):
        generate(caller)


def test_separated_records_header_length_conversions():
    program = subset.validate(parse_preprocessed("""long f(long a[], long a_len) {
    __spec_requires(__spec_separated(a, 1, a, 0)); return a_len; }"""))
    predicate = program.functions[0].contracts[0].condition
    assert all(
        isinstance(predicate.arguments[i], ir.Convert) and predicate.arguments[i].type is ir.ScalarType.LONG
        for i in (1, 3)
    )
