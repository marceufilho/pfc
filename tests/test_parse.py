"""Hand-written expectations for the syntax frontend, not proof results."""

from pathlib import Path

import pytest
from pycparser import c_ast

from pfc.diagnostics import FrontendError
from pfc.parse import Spec, SpecKind, parse_preprocessed, parse_source

SUM = """long sum(const long a[], long a_len, long n) {
    __spec_requires(0 <= n && n <= a_len);
    long s = 0;
    long i = 0;
    while (i < n) {
        __spec_invariant(0 <= i && i <= n);
        s = s + a[i];
        i = i + 1;
    }
    return s;
}
"""


def test_sum_contains_explicit_spec_nodes_and_index_site() -> None:
    unit = parse_preprocessed(SUM, "sum.c")
    function = unit.ast.ext[0]
    assert isinstance(function, c_ast.FuncDef)
    requires = function.body.block_items[0]
    invariant = function.body.block_items[3].stmt.block_items[0]
    assert isinstance(requires, Spec) and isinstance(invariant, Spec)
    assert requires.kind is SpecKind.REQUIRES
    assert invariant.kind is SpecKind.INVARIANT
    assert requires.arguments[0].op == "&&"
    assert requires.arguments[0].right.right.name == "a_len"
    assert [item.kind for item in unit.report.contracts] == ["REQUIRES", "INVARIANT"]
    assert [(site.kind, site.context, site.location.line) for site in unit.report.sites] == [("index", "program", 7)]
    assert unit.report.functions == ("sum",)


def test_line_markers_and_contract_sites_are_preserved() -> None:
    unit = parse_preprocessed(
        '# 40 "original.c"\nlong f(const long a[], long n) {\n' "__spec_requires(a[0] / n > 0);\nreturn a[1] / n;\n}\n",
        "temporary.i",
    )
    assert [(item.kind, item.context) for item in unit.report.sites] == [
        ("division", "contract"),
        ("index", "contract"),
        ("division", "program"),
        ("index", "program"),
    ]
    assert {item.location.file for item in unit.report.sites} == {"original.c"}
    assert [item.location.line for item in unit.report.sites] == [41, 41, 42, 42]


def test_assert_and_nested_separated_become_spec_nodes() -> None:
    unit = parse_preprocessed(
        "long f(const long a[], const long b[], long n) {\n"
        "__spec_requires(__spec_separated(a, n, b, n));\n"
        "__spec_ensures(n >= 0);\n__spec_assert(n > 0);\nreturn n;\n}",
        "f.c",
    )
    requires = unit.ast.ext[0].body.block_items[0]
    assert isinstance(requires.arguments[0], Spec)
    assert requires.arguments[0].kind is SpecKind.SEPARATED
    assert [node.name for node in requires.arguments[0].arguments] == ["a", "n", "b", "n"]
    assert [item.kind for item in unit.report.contracts] == ["REQUIRES", "SEPARATED", "ENSURES", "ASSERT"]


def test_structured_branches_for_loop_and_multiple_functions() -> None:
    unit = parse_preprocessed(
        "long f(long n) { for (long i = 0; i < n; i = i + 1) {\n"
        "__spec_invariant(i >= 0); if (n > 0) __spec_assert(n > i); else { ; } } return n; }\n"
        "_Bool g(_Bool b) { return !b; }",
        "f.c",
    )
    assert unit.report.functions == ("f", "g")
    assert [item.kind for item in unit.report.contracts] == ["INVARIANT", "ASSERT"]


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("", "source-empty"),
        ("  \n", "source-empty"),
        ('# 1 "bad.c"\n', "empty-program"),
        ("long f(long);", "empty-program"),
        ("long f( {", "parse-syntax"),
        ("long f(void) { goto end; end: return 0; }", "subset-goto"),
        ("long f(void) { float x = 0; return 0; }", "subset-type"),
        ("void f(void) { return; }", "subset-type"),
        ("long f(void) { void x; return 0; }", "subset-type"),
        ("void __spec_mystery(_Bool x); long f(void) { return 0; }", "contract-marker"),
        ("long f(void) { long x = (long)1; return x; }", "subset-cast"),
        ("long state; long f(void) { return state; }", "subset-global"),
        ("long f(void) { return f(); }", "subset-recursion"),
        ("long f(void) { while (1) { break; } return 0; }", "subset-break"),
        ("long f(long x) { return x % 2; }", "subset-operator"),
        ("long f(long x) { x /= 2; return x; }", "subset-assignment"),
        ("long f(void) { __spec_requires(); return 0; }", "contract-arity"),
        ("long f(void) { __spec_assert(1, 2); return 0; }", "contract-arity"),
        ("long f(void) { return __spec_assert(1); }", "contract-position"),
        ("long f(void) { __spec_invariant(1); return 0; }", "contract-position"),
        ("long f(void) { long x = 0; __spec_requires(1); return x; }", "contract-position"),
        ("long f(long n) { while(n) { n = n - 1; __spec_invariant(n); } return n; }", "contract-position"),
        ("long f(long n) { __spec_requires(n = 2); return n; }", "contract-effect"),
        ("long f(long n) { __spec_requires(++n); return n; }", "contract-effect"),
        ("long f(long n) { __spec_mystery(n); return n; }", "contract-marker"),
        ("long __spec_requires(long x) { return x; }", "contract-marker"),
        ("long f(long n) { __spec_requires(__spec_separated(n,n)); return n; }", "contract-arity"),
        ("void __spec_requires(...); long f(void) { return 0; }", "parse-syntax"),
        ("long __spec_requires(long x); long f(void) { return 0; }", "contract-marker"),
        (
            "_Bool __spec_separated(const void *a, long n, const void *b, ...); long f(void) { return 0; }",
            "contract-marker",
        ),
    ],
)
def test_invalid_or_unhandled_syntax_fails_with_rule_and_location(source: str, rule: str) -> None:
    with pytest.raises(FrontendError) as failure:
        parse_preprocessed(source, "bad.c")
    assert failure.value.diagnostic.rule == rule
    assert failure.value.diagnostic.location.file == "bad.c"
    assert failure.value.diagnostic.location.line >= 1


def test_syntax_error_does_not_echo_source_tokens() -> None:
    with pytest.raises(FrontendError) as failure:
        parse_preprocessed("long f(void) { return 1 private_payload; }", "bad.c")
    assert "private_payload" not in str(failure.value)


def test_ast_complexity_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pfc.parse.MAX_AST_NODES", 2)
    with pytest.raises(FrontendError) as failure:
        parse_preprocessed(SUM, "sum.c")
    assert failure.value.diagnostic.rule == "parse-complexity"


def test_ast_depth_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pfc.parse.MAX_AST_DEPTH", 2)
    with pytest.raises(FrontendError) as failure:
        parse_preprocessed(SUM, "sum.c")
    assert failure.value.diagnostic.rule == "parse-complexity"


@pytest.mark.integration
def test_real_sum_pipeline() -> None:
    source = Path(__file__).resolve().parents[1] / "kernels/sum/kernel.c"
    unit = parse_source(source)
    assert [item.kind for item in unit.report.contracts] == ["REQUIRES", "INVARIANT"]
    assert [(item.kind, item.location.line) for item in unit.report.sites] == [("index", 12)]


@pytest.mark.integration
def test_bool_header_and_quoted_local_header_from_other_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "source with spaces"
    directory.mkdir()
    (directory / "api.h").write_text("long f(_Bool enabled);\n")
    source = directory / "kernel.c"
    source.write_text(
        '#include "spec.h"\n#include <stdbool.h>\n#include "api.h"\n'
        "long f(bool enabled) { REQUIRES(enabled == true); return 1; }\n"
    )
    monkeypatch.chdir(tmp_path)
    assert parse_source(source).report.contracts[0].location.line == 4


@pytest.mark.integration
def test_unavailable_libc_header_fails_explicitly(tmp_path: Path) -> None:
    source = tmp_path / "libc.c"
    source.write_text("#include <stdio.h>\nlong f(void) { return 0; }")
    with pytest.raises(FrontendError) as failure:
        parse_source(source)
    assert failure.value.diagnostic.rule == "preprocess-failed"


@pytest.mark.integration
def test_comments_only_source_is_an_empty_program(tmp_path: Path) -> None:
    source = tmp_path / "empty.c"
    source.write_text("/* nothing */\n")
    with pytest.raises(FrontendError) as failure:
        parse_source(source)
    assert failure.value.diagnostic.rule == "empty-program"
