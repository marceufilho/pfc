"""Small ACSL-derived safety slice; fixed examples are not a functional-correctness proof."""

import os
from pathlib import Path

import pytest

from pfc.build import build_source
from pfc.difftest import invoke

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = {
    "fill": "long fill(long[],long,long,long); int main(void){long a[4]={1,2,1,4};"
    "return !(fill(a,4,3,5)==3 && a[0]==5 && a[1]==5 && a[2]==5 && a[3]==4);}",
    "copy": "long copy(const long[],long,long[],long,long); int main(void){long a[4]={1,2,1,4},b[4]={9,8,7,6};"
    "return !(copy(a,4,b,4,3)==3 && a[0]==1 && a[1]==2 && a[2]==1 && a[3]==4"
    "&& b[0]==1 && b[1]==2 && b[2]==1 && b[3]==6);}",
    "replace": "long replace(long[],long,long,long,long); int main(void){long a[4]={1,2,1,4};"
    "return !(replace(a,4,3,1,9)==3 && a[0]==9 && a[1]==2 && a[2]==9 && a[3]==4);}",
    "reverse": "long reverse(long[],long,long); int main(void){long a[4]={9,8,7,6};"
    "return !(reverse(a,4,3)==3 && a[0]==7 && a[1]==8 && a[2]==9 && a[3]==6);}",
    "swap_ranges": "long swap_ranges(long[],long,long[],long,long); int main(void){long a[4]={1,2,1,4},b[4]={9,8,7,6};"
    "return !(swap_ranges(a,4,b,4,3)==3 && a[0]==9 && a[1]==8 && a[2]==7 && a[3]==4"
    "&& b[0]==1 && b[1]==2 && b[2]==1 && b[3]==6);}",
    "count": "long count(const long[],long,long,long); int main(void){long a[4]={1,2,1,4};"
    "return !(count(a,4,3,1)==2 && a[0]==1 && a[1]==2 && a[2]==1 && a[3]==4);}",
    "find": "long find(const long[],long,long,long); int main(void){long a[4]={1,2,1,4};"
    "return !(find(a,4,3,2)==1 && find(a,4,3,8)==3 && a[0]==1 && a[1]==2 && a[2]==1 && a[3]==4);}",
    "accumulate": "long accumulate(const long[],long,long,long); int main(void){long a[4]={1,2,1,4};"
    "return !(accumulate(a,4,3,10)==14 && a[0]==1 && a[1]==2 && a[2]==1 && a[3]==4);}",
}


@pytest.mark.integration
@pytest.mark.parametrize("name", DRIVERS)
@pytest.mark.parametrize("mode", ["plain", "checked", "verified"])
def test_external_array_port_compiles_and_matches_hand_example(name, mode, tmp_path):
    source = ROOT / "bench/external/acsl" / name / "kernel.c"
    driver = tmp_path / "driver.c"
    driver.write_text(DRIVERS[name], encoding="utf-8")
    result = build_source(
        source, tmp_path / "build", compiler=os.environ.get("PFC_CLANG", "clang-19"), mode=mode, driver=driver
    )
    assert result.passed, result
    run = invoke((str(tmp_path / "build/program"),), tmp_path)
    assert run.status == "ok" and run.returncode == 0, run
