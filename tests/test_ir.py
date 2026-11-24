"""Typed lowering and source reconstruction; expectations are independent of the printer."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from pfc.diagnostics import FrontendError
from pfc.ir import Assign, Binary, Convert, For, If, Index, Local, ScalarType, While, print_c
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source

ROOT = Path(__file__).resolve().parents[1]


def unit(source: str):
    return lower(parse_preprocessed(source, "example.c"))


def test_sum_has_typed_symbols_lengths_contracts_and_locations():
    parsed = parse_source(ROOT / "kernels/sum/kernel.c")
    program = lower(parsed)
    function = program.functions[0]
    assert function.name == "sum"
    assert function.return_type is ScalarType.LONG
    array, length, count = function.parameters
    assert array.length is length.symbol
    assert array.symbol.const
    assert array.symbol.type.element is ScalarType.LONG
    assert [p.symbol.name for p in function.parameters] == ["a", "a_len", "n"]
    assert len(function.contracts) == 1
    assert function.contracts[0].location.line == 6
    first, second, loop, returned = function.body.statements
    assert isinstance(first, Local) and isinstance(second, Local)
    assert isinstance(loop, While)
    assert loop.invariant is not None and loop.invariant.location.line == 11
    assignment = loop.body.statements[0]
    assert isinstance(assignment, Assign)
    assert isinstance(assignment.value, Binary)
    assert isinstance(assignment.value.right, Index)
    assert assignment.value.right.location.line == 12
    assert assignment.value.right.array is array.symbol
    assert returned.value.symbol is first.symbol
    assert lower(parsed) == program  # Lowering did not mutate its input.
    with pytest.raises(FrozenInstanceError):
        function.name = "other"


def test_sum_prints_reviewed_golden():
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    assert print_c(program) == (ROOT / "tests/golden/sum-ir.c").read_text()


def test_scoped_for_if_arrays_and_optional_invariant():
    program = unit("""long f(long a[], long a_len, long n) {
        long s = 0;
        for (long i = 0; i < n; i = i + 1) {
            __spec_invariant(0 <= i && i <= n);
            if (a[i] > 0) { a[i] = a[i] / 2; } else { s = s - a[i]; }
        }
        while (s < 0) { s = s + 1; }
        return s;
    }""")
    loop = program.functions[0].body.statements[1]
    assert isinstance(loop, For)
    assert isinstance(loop.initializer, Local)
    assert isinstance(loop.body.statements[0], If)
    assert loop.invariant is not None
    assert program.functions[0].body.statements[2].invariant is None
    output = print_c(program)
    assert "for (long i = 0; (i < n); i = (i + 1))" in output
    assert "a[i] = (a[i] / 2);" in output


def test_c_promotions_and_bool_assignment_are_explicit():
    program = unit("_Bool f(_Bool b, long n) { _Bool flag = n; long x = b + 2; return flag || x; }")
    body = program.functions[0].body.statements
    assert isinstance(body[0].initializer, Convert)
    assert body[0].initializer.type is ScalarType.BOOL
    addition = body[1].initializer.operand
    assert addition.type is ScalarType.INT
    assert addition.left.type is ScalarType.INT
    assert body[1].initializer.type is ScalarType.LONG
    assert body[2].value.type is ScalarType.BOOL
    assert body[2].value.operand.type is ScalarType.INT


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("long f(long a[]) { return 0; }", "ir-array-length"),
        ("long f(long a[], _Bool a_len) { return 0; }", "ir-array-length"),
        ("long f(long a[3], long a_len) { return 0; }", "ir-array-shape"),
        ("long f(void) { long a[2]; return 0; }", "ir-local"),
        ("long f(void) { long x; return 0; }", "ir-local"),
        ("long f(void) { long x = x; return x; }", "ir-name"),
        ("long f(void) { return missing; }", "ir-name"),
        ("long f(long n) { long n = 1; return n; }", "ir-name"),
        ("long f(long n) { { long n = 1; } return n; }", "ir-name"),
        ("long f(void) { { long n = 1; } return n; }", "ir-name"),
        ("long f(void) { for (long i=0; i<2; i=i+1) {} return i; }", "ir-name"),
        ("long f(void) { const long n = 1; n = 2; return n; }", "ir-const"),
        ("long f(const long a[], long a_len) { a[0] = 1; return 0; }", "ir-const"),
        ("long f(long a[], long a_len) { return a + 1; }", "subset-pointer-arith"),
        ("long f(long a[], long a_len) { a = a; return 0; }", "ir-lvalue"),
        ("long f(long n) { return n[0]; }", "ir-type"),
        ("long f(void) { 1 = 2; return 0; }", "ir-lvalue"),
        ("long f(long n) { return (n = 2); }", "ir-expression"),
        ("long f(void) { return; }", "ir-return"),
        ("long f(long n) { if (n) return 1; }", "ir-return"),
        ("long f(void) { return 32768; }", "ir-literal-range"),
        ("long f(void) { return 2147483648L; }", "ir-literal-range"),
        ("long f(); long f(void) { return 0; }", "ir-prototype"),
        ("long f(long n); long f(_Bool n) { return n; }", "ir-prototype"),
        ("long absent(long n); long f(void) { return 0; }", "ir-prototype"),
        ("long f(void) { return 0; } long f(void) { return 1; }", "ir-function"),
        ("long f(void) { __spec_requires(RESULT >= 0); return 0; }", "ir-name"),
        ("long f(long a[], long a_len) { __spec_separated(a, a_len, a, a_len); return 0; }", "ir-contract"),
    ],
)
def test_rejects_unrepresentable_input_with_rule_and_location(source, rule):
    with pytest.raises(FrontendError) as caught:
        unit(source)
    diagnostic = caught.value.diagnostic
    assert diagnostic.rule == rule
    assert diagnostic.location.file == "example.c" and diagnostic.location.line == 1


def test_literals_keep_spelling_and_c_type():
    program = unit("long f(void) { return 0x7f + 077 + 2147483647L; }")
    expression = program.functions[0].body.statements[0].value
    assert expression.type is ScalarType.LONG
    assert "((0x7f + 077) + 2147483647L)" in print_c(program)


def test_entry_contracts_assertions_and_if_returns():
    program = unit("""long f(long n) {
        __spec_requires(n >= 0); __spec_ensures(n >= 0);
        __spec_assert(n != 2);
        if (n) return n; else return 1;
    }""")
    assert len(program.functions[0].contracts) == 2
    output = print_c(program)
    assert "ASSERT((n != 2));" in output and "ENSURES((n >= 0));" in output
    assert "else {" in output


def test_oversized_integer_is_a_diagnostic_not_a_python_conversion_error():
    with pytest.raises(FrontendError) as caught:
        unit("long f(void) { return " + "9" * 5000 + "; }")
    assert caught.value.diagnostic.rule == "ir-literal-range"


def test_parameter_may_hide_its_function_name():
    # Function names are not variable bindings: no ambiguous local variable is hidden.
    program = unit("long f(long f) { return f; }")
    assert program.functions[0].parameters[0].symbol.name == "f"


def test_arrays_have_distinct_length_bindings_and_preserved_locations():
    program = unit("long f(const long a[], long a_len, long b[], long b_len) { b[0] = a[0]; return b[0]; }")
    function = program.functions[0]
    assert function.parameters[0].length.id != function.parameters[2].length.id
    assignment = function.body.statements[0]
    assert assignment.target.array.id != assignment.value.array.id
    assert assignment.target.location.column is not None


def test_disjoint_scopes_and_shadow_rejection_do_not_leak_bindings():
    program = unit("long f(long n) { if (n) { long x = 2; n = x; } else { long x = 3; n = x; } return n; }")
    branch = program.functions[0].body.statements[0]
    left = branch.then_body.statements[0].symbol
    right = branch.else_body.statements[0].symbol
    assert left.name == right.name == "x"
    assert left.id != right.id


def test_for_missing_headers_and_assignment_initializer():
    program = unit("long f(long n) { for (n = 0; n < 1; n = n + 1) {} for (;;) { return n; } return n; }")
    first, second, _ = program.functions[0].body.statements
    assert isinstance(first.initializer, Assign)
    assert second.initializer is second.condition is second.update is None
    assert "for (; ; )" in print_c(program)
