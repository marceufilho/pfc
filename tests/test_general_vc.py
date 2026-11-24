"""Step 11 obligations: hand-derived counts and real countermodels, including aliasing."""

from pathlib import Path

import pytest

from pfc import ir, smt, vc
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import Target

TARGET = Target("test clang", "test-target", 32, 64)
ROOT = Path(__file__).resolve().parents[1]


def program(text):
    return lower(parse_preprocessed(text, "general.c"))


def checked(text):
    return smt.check(program(text), TARGET)


@pytest.mark.parametrize("name", ["dot", "saxpy", "prefix_sum"])
def test_required_kernel_obligations_prove(name):
    report = smt.check(lower(parse_source(ROOT / "kernels" / name / "kernel.c")), TARGET)
    assert report.required_passed
    assert any(r.obligation.kind == "bounds" for r in report.results)


def test_result_binding_and_two_return_postconditions():
    p = program("""long f(long n) { __spec_ensures(RESULT >= 0);
if (n >= 0) return n; else return 0; }""")
    report = smt.check(p, TARGET)
    assert report.required_passed
    assert [r.obligation.kind for r in report.results] == ["postcondition", "postcondition"]
    assert "RESULT" in ir.print_c(p)


def test_false_postcondition_is_failed():
    report = checked("long f(long n) { __spec_ensures(RESULT == n); return 0; }")
    assert not report.required_passed and report.results[0].status == "failed"


def test_assert_is_proved_before_it_can_be_assumed():
    report = checked("""long f(long n) { __spec_assert(n > 0); __spec_assert(n >= 0); return n; }""")
    assert [r.obligation.kind for r in report.results] == ["assert", "assert"]
    assert report.results[0].status == "failed"
    assert report.results[1].status == "proved" and not report.results[1].dependencies_satisfied
    assert not report.required_passed


def test_store_then_read_same_array():
    report = checked("""long f(long a[], long a_len) { __spec_requires(a_len > 0);
a[0] = 7; __spec_assert(a[0] == 7); return 0; }""")
    assert report.required_passed
    assert [r.obligation.kind for r in report.results] == ["bounds", "bounds", "assert"]
    assert "store" in str(ir.to_data(vc.generate(program("""long f(long a[], long a_len) {
a[0] = 7; __spec_assert(a[0] == 7); return 0; }"""))))


def test_write_may_alias_other_array_without_separation():
    source = """long f(long a[], long a_len, long b[], long b_len) {
__spec_requires(a_len > 0 && b_len > 0);
__spec_requires(a[0] == 1);
b[0] = 2; __spec_assert(a[0] == 1); return 0; }"""
    report = checked(source)
    assertion = next(r for r in report.results if r.obligation.kind == "assert")
    assert assertion.status == "failed" and assertion.counterexample
    separated = source.replace(
        "__spec_requires(a[0] == 1);", "__spec_requires(__spec_separated(a,a_len,b,b_len)); __spec_requires(a[0] == 1);"
    )
    assert checked(separated).required_passed


def test_parameter_write_does_not_rewrite_entry_requires_or_capacity():
    report = checked("""long f(long a[], long a_len) { __spec_requires(a_len == 0);
a_len = 10; return a[0]; }""")
    assert not report.required_passed
    assert next(r for r in report.results if r.obligation.kind == "bounds").status == "failed"


def test_for_loop_checks_initiation_preservation_and_each_access():
    report = checked("""long f(long a[], long a_len, long n) { __spec_requires(0 <= n && n <= a_len);
for(long i=0; i<n; i=i+1) { __spec_invariant(0<=i && i<=n); a[i]=0; } return n; }""")
    assert report.required_passed
    kinds = [r.obligation.kind for r in report.results]
    assert kinds.count("entry") == kinds.count("preservation") == kinds.count("bounds") == 1
    assert "exit" not in kinds


def test_nested_invariant_validation_has_no_cyclic_dependencies():
    report = checked("""long f(long n) { __spec_requires(0<=n); long i=0;
while(i<n) { __spec_invariant(0<=i && i<=n); long j=0;
while(j<n) { __spec_invariant(0<=j && j<=n); j=j+1; } i=i+1; } return i; }""")
    assert report.required_passed
    assert sum(r.obligation.kind == "preservation" for r in report.results) == 2


def test_loop_cannot_keep_fact_about_modified_array():
    report = checked("""long f(long a[], long a_len, long n) {
__spec_requires(a_len>0 && n>=0); __spec_requires(a[0]==1); long i=0;
while(i<n) { __spec_invariant(0<=i && i<=n); a[0]=2; i=i+1; }
__spec_assert(a[0]==1); return 0; }""")
    assert not report.required_passed
    assert next(r for r in report.results if r.obligation.kind == "assert").status == "failed"


def test_modular_call_checks_precondition_before_postcondition():
    good = """long nonnegative(long n) { __spec_requires(n>=0); __spec_ensures(RESULT>=0); return n; }
long f(long n) { __spec_requires(n>=0); long result=nonnegative(n); __spec_assert(result>=0); return result; }"""
    assert checked(good).required_passed
    bad = good.replace("long f(long n) { __spec_requires(n>=0);", "long f(long n) {")
    report = checked(bad)
    call = next(r for r in report.results if r.obligation.kind == "call-precondition")
    assert call.status == "failed" and not report.required_passed
    assertion = next(r for r in report.results if r.obligation.kind == "assert")
    assert not assertion.dependencies_satisfied


def test_failed_callee_postcondition_cannot_certify_caller():
    report = checked("""long g(void) { __spec_ensures(RESULT==1); return 0; }
long f(void) { long x=g(); __spec_assert(x==1); return x; }""")
    assert not report.required_passed
    assert not next(r for r in report.results if r.obligation.kind == "assert").dependencies_satisfied


def test_call_havocs_memory_effects_instead_of_preserving_stale_reads():
    report = checked("""long set(long a[], long a_len) { __spec_requires(a_len>0); a[0]=2; return 0; }
long f(long b[], long b_len) { __spec_requires(b_len>0); __spec_requires(b[0]==1);
long ignored=set(b,b_len); __spec_assert(b[0]==1); return ignored; }""")
    assert not report.required_passed
    assert next(r for r in report.results if r.obligation.kind == "assert").status == "failed"


def test_contract_division_definedness_respects_short_circuit():
    report = checked("long f(long n) { __spec_requires(n != 0 && 1L/n > 0); return n; }")
    assert report.required_passed
    bad = checked("long f(long n) { __spec_requires(1L/n > 0); return n; }")
    assert not bad.required_passed


def test_partial_overlap_is_preserved_by_shared_memory():
    source = """long f(long a[], long a_len, long b[], long b_len) {
__spec_requires(a_len>1 && b_len>0); __spec_requires(a[1]==1);
b[0]=2; __spec_assert(a[1]==1); return 0; }"""
    assert not checked(source).required_passed


def test_nested_assertions_do_not_make_preservation_circular():
    report = checked("""long f(long n) { __spec_requires(n>=0); long i=0;
while(i<n) { __spec_invariant(0<=i && i<=n); __spec_assert(i<n);
long j=0; while(j<n) { __spec_invariant(0<=j && j<=n); __spec_assert(j<n); j=j+1; }
i=i+1; } return i; }""")
    assert report.required_passed


def test_branch_paths_cannot_hide_failed_preservation():
    report = checked("""long f(long n) { __spec_requires(n>=0); long i=0;
while(i<n) { __spec_invariant(0<=i && i<=n); if(i==0) i=n+1; else i=i+1; } return i; }""")
    assert not report.required_passed
    assert next(r for r in report.results if r.obligation.kind == "preservation").status == "failed"


def test_updated_callee_parameter_is_not_confused_with_actual_argument():
    report = checked("""long g(long n) { __spec_ensures(RESULT==n); n=0; return n; }
long f(long n) { __spec_requires(n>0); long x=g(n); __spec_assert(x==n); return x; }""")
    assert not report.required_passed
    assert next(r for r in report.results if r.obligation.kind == "assert").status == "failed"


def test_step13_emitter_instruments_new_write_semantics():
    from pfc.emit import emit

    sources = [
        "long f(long a[],long a_len){a[0]=1;return 0;}",
        "long f(long a[],long a_len){a_len=10;return a[0];}",
        "long f(void){return 0;} long g(void){return 1;}",
    ]
    outputs = [emit(program(source), TARGET, mode="checked") for source in sources]
    assert all(len(result.decisions) == 1 and not result.decisions[0].removed for result in outputs[:2])
    assert "long f(void)" in outputs[2].c and "long g(void)" in outputs[2].c


def test_vc_identity_is_stable_across_python_hash_seeds(tmp_path):
    import os
    import subprocess
    import sys

    source = """long f(long a[],long a_len,_Bool b[],long b_len,long n) {
__spec_requires(a_len>0 && b_len>0 && n>=0); long i=0;
while(i<n) { __spec_invariant(0<=i && i<=n); a[0]=0; b[0]=0; i=i+1; }
return a[0]+b[0]; }"""
    path = tmp_path / "source.c"
    path.write_text(source)
    script = """import sys
from pathlib import Path
from pfc.lower import lower
from pfc.parse import parse_preprocessed
from pfc.vc import generate
p=lower(parse_preprocessed(Path(sys.argv[1]).read_text(), 'stable.c'))
print([o.id for o in generate(p).obligations])
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script, str(path)],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
        ).stdout
        for seed in range(4)
    ]
    assert len(set(outputs)) == 1
