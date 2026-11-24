"""Step 13: full structured emission and separately justified compiler facts."""

import os
import subprocess
from pathlib import Path

import pytest

from pfc import smt
from pfc.emit import GUARD_LINE, OVERFLOW_LINE, emit
from pfc.lower import lower
from pfc.parse import parse_preprocessed, parse_source
from pfc.target import read_target

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def target():
    return read_target(os.environ.get("PFC_CLANG", "clang-19"))


@pytest.mark.parametrize("name,count", [("sum", 1), ("dot", 2), ("saxpy", 3), ("prefix_sum", 3)])
def test_all_runtime_array_sites_have_checks_and_exact_proofs(name, count, target):
    p = lower(parse_source(ROOT / "kernels" / name / "kernel.c"))
    proof = smt.check(p, target)
    checked = emit(p, target, mode="checked")
    verified = emit(p, target, mode="verified", proof=proof)
    plain = emit(p, target, mode="plain")
    assert len(checked.decisions) == len(verified.decisions) == count
    assert len(GUARD_LINE.findall(checked.c)) == count  # guard-shaped traps; overflow traps are counted apart (v5)
    assert all(d.removed and len(d.obligation_ids) == 1 for d in verified.decisions)
    assert not GUARD_LINE.findall(verified.c) and "__builtin_trap" not in plain.c
    assert len(OVERFLOW_LINE.findall(checked.c)) == len(checked.overflow) >= 1
    assert len(OVERFLOW_LINE.findall(verified.c)) == sum(not d.removed for d in verified.overflow)
    for decision in verified.decisions:
        assert f"// proved: obligation {decision.obligation_ids[0]}" in verified.c


def test_fact_switches_are_independent_and_require_proofs(target):
    p = lower(parse_source(ROOT / "kernels/saxpy/kernel.c"))
    proof = smt.check(p, target)
    for disabled in ("delete", "restrict", "assume"):
        facts = frozenset({"delete", "restrict", "assume"} - {disabled})
        result = emit(p, target, mode="verified", proof=proof, facts=facts)
        assert bool(GUARD_LINE.findall(result.c)) == (disabled == "delete")
        assert ("[restrict]" in result.c) == (disabled != "restrict")
        assert ("__builtin_assume(" in result.c) == (disabled != "assume")
    no_proof = emit(p, target, mode="verified", facts=frozenset({"delete", "restrict", "assume"}))
    assert GUARD_LINE.findall(no_proof.c) and "[restrict]" not in no_proof.c
    assert "__builtin_assume(" not in no_proof.c


def test_separation_ablation_restores_alias_facts_only_when_justified(target):
    from dataclasses import replace

    p = lower(parse_source(ROOT / "kernels/saxpy/kernel.c"))
    function = p.functions[0]
    p = replace(p, functions=(replace(function, contracts=function.contracts[:1]),))
    report = smt.check(p, target)
    result = emit(p, target, mode="verified", proof=report, facts=frozenset({"delete", "restrict", "assume"}))
    assert "[restrict]" not in result.c
    assert "__builtin_assume_separate_storage" not in result.c


def test_conditional_or_partial_separation_does_not_grant_restrict(target):
    sources = [
        "__spec_requires(n==0 || __spec_separated(a,a_len,b,b_len));",
        "__spec_requires(__spec_separated(a,0,b,0));",
    ]
    for contract in sources:
        p = lower(
            parse_preprocessed(
                "long f(long a[],long a_len,long b[],long b_len,long n) {"
                + "__spec_requires(a_len>0 && b_len>0);"
                + contract
                + " a[0]=b[0]; return 0;}",
                "alias.c",
            )
        )
        result = emit(p, target, mode="verified", proof=smt.check(p, target), facts=frozenset({"restrict"}))
        assert "[restrict]" not in result.c


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["plain", "checked", "verified"])
def test_nested_for_branch_write_and_call_execute_equally(tmp_path, target, mode):
    source = """long positive(long n) { __spec_requires(n>=0); __spec_ensures(RESULT>=0); return n; }
long f(long a[],long a_len,long n) { __spec_requires(0<=n && n<=a_len);
for(long i=0;i<n;i=i+1) { __spec_invariant(0<=i && i<=n);
long x=positive(i); if(i==0) a[i]=x+1; else a[i]=x; } return n; }"""
    p = lower(parse_preprocessed(source, "structured.c"))
    report = smt.check(p, target)
    assert report.required_passed
    output = emit(p, target, mode=mode, proof=report if mode == "verified" else None)
    generated = tmp_path / "kernel.c"
    generated.write_text(output.c)
    driver = tmp_path / "main.c"
    driver.write_text("""long f(long[],long,long);
int main(void){long a[3]={0}; return !(f(a,3,3)==3 && a[0]==1 && a[1]==1 && a[2]==2);}""")
    binary = tmp_path / "test"
    compiler = os.environ.get("PFC_CLANG", "clang-19")
    build = subprocess.run(
        [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", str(generated), str(driver), "-o", str(binary)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    assert subprocess.run([str(binary)], check=False).returncode == 0


def test_parameter_assignment_uses_saved_entry_capacity(target):
    p = lower(parse_preprocessed("long f(long a[],long a_len){a_len=10;return a[0];}", "capacity.c"))
    result = emit(p, target, mode="checked")
    assert " = a_len;" in result.c
    guard = next(line for line in result.c.splitlines() if "__builtin_trap" in line)
    assert "< a_len)" not in guard


def test_plain_mode_does_not_require_an_invariant(target):
    p = lower(parse_preprocessed("long f(long n){while(n>0){n=n-1;}return n;}", "plain.c"))
    assert "__builtin_trap" not in emit(p, target, mode="plain").c


@pytest.mark.integration
def test_saxpy_facts_accept_disjoint_slices_of_one_allocation(tmp_path, target):
    p = lower(parse_source(ROOT / "kernels/saxpy/kernel.c"))
    result = emit(
        p, target, mode="verified", proof=smt.check(p, target), facts=frozenset({"delete", "restrict", "assume"})
    )
    source = tmp_path / "test.c"
    source.write_text(result.c + """
int main(void) {
 long storage[6] = {1,2,3,10,20,30};
 if (saxpy(2,storage,3,storage+3,3,3)!=3) return 1;
 return !(storage[0]==1 && storage[1]==2 && storage[2]==3 &&
          storage[3]==12 && storage[4]==24 && storage[5]==36);
}""")
    binary = tmp_path / "test"
    build = subprocess.run(
        [
            os.environ.get("PFC_CLANG", "clang-19"),
            "-std=c11",
            "-O3",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    assert subprocess.run([str(binary)], check=False).returncode == 0
    assert all(fact.obligation_ids for fact in result.emitted_facts)


def test_arithmetic_written_through_alias_and_used_in_control_blocks_deletion(target):
    p = lower(
        parse_preprocessed(
            """long f(long a[],long a_len,long b[],long b_len,long n) {
__spec_requires(a_len>0 && b_len>0); a[0]=n+1;
if(b[0]>0) return a[0]; return 0; }""",
            "dataflow.c",
        )
    )
    report = smt.check(p, target)
    assert report.required_passed
    result = emit(p, target, mode="verified", proof=report)
    assert result.arithmetic_dependencies
    assert all(not d.removed for d in result.decisions)


def test_disabling_delete_is_recorded_as_the_reason(target):
    p = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    result = emit(p, target, mode="verified", proof=smt.check(p, target), facts=frozenset())
    assert all(d.reason == "delete fact disabled" for d in result.decisions)


@pytest.mark.parametrize("name", ["sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search"])
@pytest.mark.parametrize("mode", ["plain", "checked", "verified"])
def test_reviewed_structured_emission_goldens(name, mode):
    from pfc.target import Target

    source = (ROOT / "kernels" / name / "kernel.c").read_text()
    source = source.replace('#include "spec.h"', "").replace('#include "kernel.h"', "")
    for macro in ("REQUIRES", "ENSURES", "INVARIANT", "ASSERT", "SEPARATED"):
        source = source.replace(macro, "__spec_" + macro.lower())
    p = lower(parse_preprocessed(source, f"kernels/{name}/kernel.c"))
    target = Target("test", "test", 32, 64)
    result = emit(p, target, mode=mode, proof=smt.check(p, target) if mode == "verified" else None)
    assert result.c == (ROOT / "tests/golden" / f"{name}-{mode}.c").read_text()
