"""External held-out set: SV-COMP `array-tiling` and `array-multidimensional` tasks ported by fixed rules.

The user asked (2026-11-09) for a held-out set as unbiased as possible, taken from external benchmarks instead of
kernels written for this study. The candidates are every verification task of two SV-COMP families whose expected
`unreach-call` verdict is true, in lexicographic order; the port rules below are fixed before any proposer run, the B0
hand invariant is the mechanical loop-counter bound, and a task leaves the set only for a reason listed in `EXCLUDED`
(construct outside the subset, false verdict) or because the frozen compiler rejects the port or its B0 (recorded in
`PROVENANCE.json` under `rejected`, never silently). The content assertions of the upstream tasks are universally
quantified over the array and cannot be stated in the subset; the ported proof goals are the bounds of every access
and the definedness of the index arithmetic, which is exactly what the compiler removes checks for (revision plan,
Phase 7; AMENDMENTS 34). The author of the compiler ported the tasks, so the set is external but not blinded; the
paper says so.

Run once, before sealing: `uv run --locked python -m pfc_eval.svcomp_arrays --output heldout --profiles
pfc_eval/profiles-heldout --upstream <sv-benchmarks checkout>`; then `python -m pfc_eval heldout seal`.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pfc import ir
from pfc.diagnostics import FrontendError
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.propose.invariants import admit
from pfc.target import Target, read_target
from pfc_eval import generate, ports

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = {
    "repository": "https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks",
    "commit": "f3feef51a07ffe7c6ef58f3da3b7fe041af4fd25",
    "directories": ["c/array-tiling", "c/array-multidimensional"],
    "license": "Apache-2.0 (upstream LICENSE.Apache-2.0.txt, copied beside the ports)",
    "origin": {
        "array-tiling": "Chakraborty, Gupta, Unadkat: Verifying array manipulating programs by tiling, SAS 2017",
        "array-multidimensional": "Rajkhowa, Lin: Extending VIAP to handle array programs, VSTTE 2018",
    },
}
SELECTION = (
    "every .c task of c/array-tiling and c/array-multidimensional at the recorded commit whose unreach-call expected "
    "verdict is true, in lexicographic order of the file name; a task is excluded only for a construct outside the "
    "pfc subset (listed in EXCLUDED) or because the frozen admit() rejects the mechanical B0 (recorded per task); "
    "the rules were fixed on 2026-11-10 before any proposer, Houdini or differential run read the set"
)
EXCLUDED = {
    "array-tiling/mlceu.c": "expected unreach-call verdict false",
    "array-tiling/mlceu2.c": "shift operator >> and sizeof are outside the subset (also uses 66060288/sizeof(int))",
    "array-tiling/skippedu.c": "expected unreach-call verdict false",
}
RULES = (
    "R1 types: int, unsigned int and long long become long; unsigned wrap-around is replaced by the "
    "trap-unless-proved overflow semantics of the emitter (the content values are not a proof goal)",
    "R2 sizes: nondeterministic or constant array dimensions become long parameters; the upstream entry guard "
    "becomes REQUIRES (SIZE > 1 && SIZE < MAX stays literal, MAX spelled 100000L for the portable literal range); "
    "a constant dimension gets 0 <= v, or 0 < v where it divides in the capacity contract or where the task reads "
    "index 0 unconditionally",
    "R3 arrays: a VLA or safe_malloc array becomes `long a[], long a_len` with the capacity conjunct n <= a_len; "
    "a 2-D array A[m][n] becomes a flat array indexed A[i * n + j] with m <= A_len / n; 3-D A[(i*n + j)*p + k] "
    "with m <= A_len / p / n; 4-D likewise",
    "R4 nondeterminism: loops that only fill arrays with nondeterministic values are dropped and the array "
    "becomes an input parameter (const when never written); a per-iteration nondeterministic read (ReadFromPort) "
    "becomes a read of an input array `port` at the index of the store it feeds; a nondeterministic or "
    "uninitialized scalar becomes a parameter",
    "R5 property: the final loop asserting a universally quantified content property is dropped (not expressible "
    "in the subset); the proof goals are the bounds of every remaining access and the overflow of the index "
    "arithmetic; the function returns the last loop counter (or the computed scalar for max/min); locals used "
    "only by the dropped loop are dropped",
    "R6 control: for loops become while loops with explicit i = i + 1 / i = i - 1; `SIZE % k == 0` assumptions "
    "and the early `return 1` are dropped (they constrain only the content property); `SIZE / k` bounds stay "
    "literal",
    "R7 B0 invariant: for every loop, the conjunction of the counter's bounds at the loop head (initial value and "
    "exit bound: lo <= i && i <= hi for ascending, lo - 1 <= i && i <= hi for descending), nothing else",
    "R8 identifiers: upstream names are kept (CELLCOUNT, volArray, MINVAL, A, B, C, m, n, p, q, s); no SEPARATED "
    "is added",
)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Task:
    name: str
    family: str
    upstream: str
    upstream_sha256: str
    parameters: tuple[str, ...]
    requires: str
    body: str
    returns: str
    shape: str
    transformations: tuple[str, ...]


T_LONG, T_PARAMS, T_ARRAY, T_PROPERTY, T_WHILE, T_B0, T_NAMES = (f"R{i}" for i in (1, 2, 3, 5, 6, 7, 8))
T_NONDET = "R4"
COMMON = (T_LONG, T_PARAMS, T_ARRAY, T_PROPERTY, T_WHILE, T_B0, T_NAMES)
TILING_SHA = {
    "mbpr2": "a98ea70d5d9f752732070144a56514c68e9571f139726211e2f0ade721d6c5f3",
    "mbpr3": "a50e57e46342104030586dde3ea26bc2e3984490f4fdef34fa383d24842d8980",
    "mbpr4": "d3770c63a87ff983494c9353997301d9eb30588c650f83b67bcf55f325443055",
    "mbpr5": "5503daab190ef8b57bdc3b49ed300bfd661cba05f76774439c2c92cf1668f08b",
    "nr2": "2a5d4c3d8db636946363c81d25b5c2e381fc236cdcde6b4765d7ce6e059d8a21",
    "nr3": "4885a4fffd0288753086daaa59bdb12816a7be62636dc49311bbb97ad277d866",
    "nr4": "3219130777e42bfc39497c312a66c6d1b1af231a6df358861eaa545434d20cb1",
    "nr5": "7a05e17fa6f76d5004d5085f0b83441da7dbecb194f05c3b661e69c00b5f4df0",
    "pnr2": "45158f6a13b5973615c2bf09580b115243957fb0331948310ff4c49f7ae3a23f",
    "pnr3": "62ec3adf218afb19ac3a8cc5ae9be94d7ddedb51aca1154ad96a3adfa20f8914",
    "pnr4": "f6afa97ea0a451d0528a273d6cd07c473d9399b3b8fd4299d8d7a90b1474c356",
    "pnr5": "391b0cc32beff546317074ee4c99e1e513eed04a5d56bd715a68523ca02460f9",
    "poly1": "009d23902b2ef0eb2715d1f4e26992b5e86c81418ae8268f4fece9923bfeb336",
    "poly2": "e61905032a34e05ad89c8e55802d7778dc3ba7c153f4f9e02a2fe17c13e5e96e",
    "pr2": "5922a5be589e27d89439744489152c928c7510f0140c8b0fb4d1faadee969752",
    "pr3": "73efddcda095c52d9900d13088d1ec70520de6007541f46343a568e878cf2ed4",
    "pr4": "14a75e050103eb09182e049a798a738dec478e3acaa4fb01ae8649b715b8362f",
    "pr5": "374c198ea90130277bd1a124bc94571dd240bc0803e6f3936a68065c719ddd5a",
    "revcpyswp2": "c2189ee49190f51453c25834336e81ef30709d68a57ad442b493bb043ae42430",
    "rew": "903f2f51bf9e6b991a86a8d6b8eab8fe5409408cff09fd4149716f384eace7e1",
    "rewnif": "aa97b90092c5967397bc128fddc7d3bbe2dda3dfb1a1011e1f2e50ec9dfc6189",
    "rewnifrev": "71fcda709b5159542e902a92bacc7c0ee36d565baaab9d528a5f36e6783c1752",
    "rewnifrev2": "83484de485ed525d34fd61f747b070d4ed309cc25995b2129d81119d62c2e538",
    "rewrev": "668c3585abd39fe8bed7b5fc57c2c11abb11e558bd06e5c9a1d6ce6f83d56f8a",
    "skipped": "e26f1e8c2144ecd874379457832176c144206bbb891179f0215136cc680d897b",
    "tcpy": "bd03875beb64a7944b42d794ad2fd70c32d94c7f0329f5f423023620a3779cd0",
}
MULTI_SHA = {
    "add-2-n-u": "7202e877c1332442c2c226005d8bd7e5f4094e339ca214931497ee0f3766da6a",
    "add-3-n-u": "efbd812580893a7521723358e51d4f43c4b930b57b026212f5a7d5f447f3e0f8",
    "copy-2-u": "d9e382438d8fbe8dfce0f613a239dc56836f436a035cb511dde070c4306eb1f9",
    "copy-3-n-u": "caaec4b6d97bde36a1fbc7dfb6c9c9f88e0ac7fb0d2e87f4054fca32d8e102e9",
    "copy-partial-2-n-u": "2a46b64c20324a7544dfe7936b5f39eaa32bba659068797748195a7f292bdaee",
    "copy-partial-3-u": "52a248b2b2cf395cf0c12ea6447af889ed3691d7fa3d2de2504a7728d19c705b",
    "diff-2-n-u": "cb0d07e46dc94102f1a204dcfe0d8cca3d45653b0db7cea07d03c16ba8563271",
    "diff-3-n-u": "ae698f02be6e3657db0e7ca3e62c4cfb121d43a876624ed4d21d19b033e0c02e",
    "init-2-n-u": "44b18e123610caaf6237dddd08dfbd765ad919d1168e6261239628431b3c22b1",
    "init-3-u": "385b06dfd41f8b2cf291934cb4635d07bfe2a63001fa162165126d7b890c1d72",
    "init-4-n-u": "7e1c46a3eec085decec4254bf9381483b25ba7b6541d5728ae52d1793b9b8c91",
    "init-non-constant-2-n-u": "4fa401c700a21fa1f98a1481c8e8ba1555a45c0429555dc041a1c04ce1059991",
    "init-non-constant-3-u": "df76811034b688387cfe28dd1eb6de91c21580662ac8ddb8163b41c1d6179172",
    "max-2-u": "d56d1653edfb1a7b1c72e82d390b1b74d1df9114797a2f1426eb2285f6c4408f",
    "max-3-n-u": "6c8d330fcf7e017e6b51632eb7a9f8f167fb4f3e932700b6ef82da7b1572a2f0",
    "min-2-u": "68785685c781649dca8a870a36ccbd5837fb99f65c0418361b1a73f7848e1bda",
    "min-3-n-u": "9bd7b1a8bed68c803bda82a4dd989302b30da7d6d96131ca67565e0ea166dbcb",
    "rev-2-n-u": "bf03248da0b4b82491822f692d5527664b721dbcd28594bdb518a5eab7d5a1f3",
    "rev-3-u": "fe3cf414f0e241a9a4e73e59647cf6619ea50b905df4a1c2a404b3413992b780",
    "transpose-u": "44b06771d393671cf9550617b8bf478abc837489f84f20705c70efc1808f7508",
}
VOLUMES = {5: 8, 4: 5, 3: 7, 2: 3, 1: 1}  # CCCELVOLk constants of mbpr*/pr* (pr5 uses CCCELVOL5 = 2)


def _indent(lines: Sequence[str], level: int = 1) -> str:
    pad = "    " * level
    return "\n".join(pad + line if line else line for line in lines)


def _if_else(condition: str, then: str, otherwise: str) -> list[str]:
    return [f"if ({condition}) {{", f"    {then}", "} else {", f"    {otherwise}", "}"]


def _loop(init: str, guard: str, invariant: str, body: Sequence[str], step: str, *, counter: str) -> list[str]:
    lines = [init, f"while ({guard}) {{", f"    INVARIANT({invariant});"]
    lines += [f"    {line}" for line in body]
    lines += [f"    {counter} = {step};", "}"]
    return lines


def _tiling(
    name: str, parameters: Sequence[str], requires: str, body: Sequence[str], returns: str, extra: Sequence[str]
) -> Task:
    return Task(
        name,
        "array-tiling",
        f"array-tiling/{name}.c",
        TILING_SHA[name],
        tuple(parameters),
        requires,
        _indent(body),
        returns,
        "tiling",
        COMMON + tuple(extra),
    )


def _strided(k: int, cell: str, body: Sequence[str]) -> list[str]:
    """One tiling pass over cells 1..CELLCOUNT/k (upstream `for (i = 1; i <= CELLCOUNT/k; i++)`)."""
    return _loop("i = 1;", f"i <= {cell} / {k}", f"1 <= i && i <= {cell} / {k} + 1", body, "i + 1", counter="i")


def _mbpr(k: int) -> Task:
    lines = ["long MINVAL = 2;"] + [f"long CCCELVOL{v} = {VOLUMES[v]};" for v in range(k, 0, -1)] + ["long i = 0;"]
    for v in range(k, 0, -1):
        others = [u for u in range(k, 0, -1) if u != v]
        if k == 3 and v == 1:
            others = [2, 3]  # upstream mbpr3 copies the cells of its last pass in this order
        body = _if_else(
            f"CCCELVOL{v} >= MINVAL", f"volArray[i * {k} - {v}] = CCCELVOL{v};", f"volArray[i * {k} - {v}] = 0;"
        )
        body += [f"volArray[i * {k} - {u}] = volArray[i * {k} - {u}];" for u in others]
        lines += _strided(k, "CELLCOUNT", body)
    return _tiling(
        f"mbpr{k}",
        ("long volArray[]", "long volArray_len", "long CELLCOUNT"),
        "CELLCOUNT > 1 && CELLCOUNT <= volArray_len",
        lines,
        "i",
        (),
    )


def _pr(k: int) -> Task:
    volumes = {**VOLUMES, 5: 2} if k == 5 else VOLUMES
    lines = [f"long CCCELVOL{v} = {volumes[v]};" for v in range(k, 0, -1)] + ["long i = 0;"]
    body: list[str] = []
    for v in range(k, 0, -1):
        body += _if_else(
            f"CCCELVOL{v} >= MINVAL", f"volArray[i * {k} - {v}] = CCCELVOL{v};", f"volArray[i * {k} - {v}] = 0;"
        )
    lines += _strided(k, "CELLCOUNT", body)
    return _tiling(
        f"pr{k}",
        ("long volArray[]", "long volArray_len", "long CELLCOUNT", "long MINVAL"),
        "CELLCOUNT > 1 && CELLCOUNT <= volArray_len",
        lines,
        "i",
        (T_NONDET + " (uninitialized MINVAL)",),
    )


def _nr(k: int) -> Task:
    inner = _loop(
        f"j = {k};",
        "j >= 1",
        f"0 <= j && j <= {k}",
        _if_else("j >= MINVAL", f"volArray[i * {k} - j] = j;", f"volArray[i * {k} - j] = 0;"),
        "j - 1",
        counter="j",
    )
    lines = ["long MINVAL = 2;", "long i = 0;", "long j = 0;"] + _strided(k, "CELLCOUNT", inner)
    return _tiling(
        f"nr{k}",
        ("long volArray[]", "long volArray_len", "long CELLCOUNT"),
        "CELLCOUNT > 1 && CELLCOUNT <= volArray_len",
        lines,
        "i",
        (),
    )


def _pnr(k: int) -> Task:
    body: list[str] = []
    for v in range(k, 0, -1):
        body.append(f"value = port[i * {k} - {v}];")
        body += _if_else("value != DEFAULTVAL", f"a[i * {k} - {v}] = value;", f"a[i * {k} - {v}] = FIXEDVAL;")
    lines = ["long DEFAULTVAL = 0;", "long FIXEDVAL = 10;", "long value = 0;", "long i = 0;"] + _strided(
        k, "SIZE", body
    )
    return _tiling(
        f"pnr{k}",
        ("const long port[]", "long port_len", "long a[]", "long a_len", "long SIZE"),
        "SIZE > 1 && SIZE < 100000L && SIZE <= a_len && SIZE <= port_len",
        lines,
        "i",
        (T_NONDET + " (ReadFromPort)",),
    )


def _ascending(guard_hi: str, body: Sequence[str], *, start: str = "0", counter: str = "i") -> list[str]:
    return _loop(
        f"{counter} = {start};",
        f"{counter} < {guard_hi}",
        f"{start} <= {counter} && {counter} <= {guard_hi}",
        body,
        f"{counter} + 1",
        counter=counter,
    )


SIZE_REQ = "SIZE > 1 && SIZE < 100000L && SIZE <= a_len"


def _tiling_tasks() -> list[Task]:
    tasks = [_mbpr(k) for k in (2, 3, 4, 5)] + [_nr(k) for k in (2, 3, 4, 5)] + [_pnr(k) for k in (2, 3, 4, 5)]
    tasks.append(
        _tiling(
            "poly1",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long i = 0;"] + _ascending("SIZE", ["a[i] = i * i;"]),
            "i",
            (),
        )
    )
    tasks.append(
        _tiling(
            "poly2",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long i = 0;"] + _ascending("SIZE", ["a[i] = i * i + 2;"]) + _ascending("SIZE", ["a[i] = a[i] - 2;"]),
            "i",
            (),
        )
    )
    tasks += [_pr(k) for k in (2, 3, 4, 5)]
    tasks.append(
        _tiling(
            "revcpyswp2",
            (
                "long a[]",
                "long a_len",
                "long b[]",
                "long b_len",
                "const long a_copy[]",
                "long a_copy_len",
                "const long b_copy[]",
                "long b_copy_len",
                "long SIZE",
            ),
            "SIZE > 1 && SIZE < 100000L && SIZE <= a_len && SIZE <= b_len && SIZE <= a_copy_len && SIZE <= b_copy_len",
            ["long i = 0;", "long tmp = 0;"]
            + _ascending("SIZE", ["a[i] = a_copy[SIZE - i - 1];", "b[i] = b_copy[SIZE - i - 1];"])
            + _ascending("SIZE", ["tmp = a[i];", "a[i] = b[i];", "b[i] = tmp;"])
            + _ascending("SIZE", ["tmp = a[i];", "a[i] = b[i];", "b[i] = tmp;"]),
            "i",
            (T_NONDET + " (a_copy, b_copy filled nondeterministically -> inputs)",),
        )
    )
    tasks.append(
        _tiling(
            "rew",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long val2 = 3;", "long val1 = 0;", "long i = 0;"]
            + _ascending("SIZE", ["if (i + 1 < SIZE) {", "    a[i + 1] = val1;", "}", "a[i] = val2;"]),
            "i",
            (),
        )
    )
    tasks.append(
        _tiling(
            "rewnif",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long i = 0;"] + _ascending("SIZE", ["if (i + 1 < SIZE) {", "    a[i + 1] = i;", "}", "a[i] = i;"]),
            "i",
            (),
        )
    )
    tasks.append(
        _tiling(
            "rewnifrev",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long i = 0;"]
            + _loop(
                "i = SIZE - 1;",
                "i >= 0",
                "-1 <= i && i <= SIZE - 1",
                ["if (i - 1 >= 0) {", "    a[i - 1] = i - 2;", "}", "a[i] = i;"],
                "i - 1",
                counter="i",
            ),
            "i",
            (),
        )
    )
    tasks.append(
        _tiling(
            "rewnifrev2",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long i = 0;"]
            + _loop(
                "i = SIZE - 2;",
                "i >= 0",
                "-1 <= i && i <= SIZE - 2",
                ["a[i] = i;", "a[i + 1] = i + 1;"],
                "i - 1",
                counter="i",
            ),
            "i",
            (),
        )
    )
    tasks.append(
        _tiling(
            "rewrev",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long val2 = 3;", "long val1 = 7;", "long i = 0;"]
            + _loop(
                "i = SIZE - 2;",
                "i >= -1",
                "-2 <= i && i <= SIZE - 2",
                ["if (i >= 0) {", "    a[i] = val1;", "}", "a[i + 1] = val2;"],
                "i - 1",
                counter="i",
            ),
            "i",
            (),
        )
    )
    tasks.append(
        _tiling(
            "skipped",
            ("long a[]", "long a_len", "long SIZE"),
            SIZE_REQ,
            ["long i = 0;"]
            + _strided(
                2,
                "SIZE",
                [
                    "if (a[2 * i - 2] > 2 * i - 2) {",
                    "    a[2 * i - 2] = 2 * i - 2;",
                    "}",
                    "if (a[2 * i - 1] > 2 * i - 1) {",
                    "    a[2 * i - 1] = 2 * i - 1;",
                    "}",
                ],
            ),
            "i",
            (T_NONDET + " (a filled nondeterministically -> input, updated in place)",),
        )
    )
    tasks.append(
        _tiling(
            "tcpy",
            ("const long a[]", "long a_len", "long acopy[]", "long acopy_len", "long SIZE"),
            "SIZE > 1 && SIZE < 100000L && SIZE <= a_len && SIZE <= acopy_len",
            ["long i = 0;"]
            + _loop(
                "i = 0;",
                "i < SIZE / 2",
                "0 <= i && i <= SIZE / 2",
                ["acopy[SIZE - i - 1] = a[SIZE - i - 1];", "acopy[i] = a[i];"],
                "i + 1",
                counter="i",
            ),
            "i",
            (T_NONDET + " (a filled nondeterministically -> input)",),
        )
    )
    return tasks


def _nest(dims: Sequence[tuple[str, str, str]], statements: Sequence[str]) -> list[str]:
    """Nested ascending loops `(counter, start, bound)` from the outside in, around the statements."""
    lines = list(statements)
    for counter, start, bound in reversed(dims):
        lines = _loop(
            f"{counter} = {start};",
            f"{counter} < {bound}",
            f"{start} <= {counter} && {counter} <= {bound}",
            lines,
            f"{counter} + 1",
            counter=counter,
        )
    return lines


def _multi(
    name: str,
    parameters: Sequence[str],
    requires: str,
    body: Sequence[str],
    returns: str,
    shape: str,
    extra: Sequence[str] = (),
) -> Task:
    identifier = name.replace("-", "_")
    return Task(
        identifier,
        "array-multidimensional",
        f"array-multidimensional/{name}.c",
        MULTI_SHA[name],
        tuple(parameters),
        requires,
        _indent(body),
        returns,
        shape,
        COMMON + tuple(extra),
    )


NONDET_FILL = T_NONDET + " (nondeterministic fill loop dropped -> input array)"
GRID2 = (("i", "0", "m"), ("j", "0", "n"))
SQUARE2 = (("i", "0", "n"), ("j", "0", "n"))
GRID3 = (("i", "0", "m"), ("j", "0", "n"), ("k", "0", "p"))
CUBE3 = (("i", "0", "n"), ("j", "0", "n"), ("k", "0", "n"))
IDX2 = "i * n + j"
IDX3 = "(i * n + j) * p + k"
CUBE_IDX3 = "(i * n + j) * n + k"
REQ_GRID2 = "0 <= m && 0 < n && m <= A_len / n"
REQ_GRID3 = "0 <= m && 0 < n && 0 < p && m <= A_len / p / n"
REQ_SQUARE = "0 < n && n <= A_len / n"
REQ_CUBE = "0 < n && n <= A_len / n / n"


def _multi_tasks() -> list[Task]:
    counters2 = ["long i = 0;", "long j = 0;"]
    counters3 = counters2 + ["long k = 0;"]
    grid2_abc = (
        "const long A[]",
        "long A_len",
        "const long B[]",
        "long B_len",
        "long C[]",
        "long C_len",
        "long m",
        "long n",
    )
    grid3_abc = (
        "const long A[]",
        "long A_len",
        "const long B[]",
        "long B_len",
        "long C[]",
        "long C_len",
        "long m",
        "long n",
        "long p",
    )
    tasks = [
        _multi(
            "add-2-n-u",
            grid2_abc,
            f"{REQ_GRID2} && m <= B_len / n && m <= C_len / n",
            counters2 + _nest(GRID2, [f"C[{IDX2}] = A[{IDX2}] + B[{IDX2}];"]),
            "i",
            "grid2",
            (NONDET_FILL,),
        ),
        _multi(
            "add-3-n-u",
            grid3_abc,
            f"{REQ_GRID3} && m <= B_len / p / n && m <= C_len / p / n",
            counters3 + _nest(GRID3, [f"C[{IDX3}] = A[{IDX3}] + B[{IDX3}];"]),
            "i",
            "grid3",
            (NONDET_FILL,),
        ),
        _multi(
            "copy-2-u",
            ("long A[]", "long A_len", "const long B[]", "long B_len", "long n"),
            f"{REQ_SQUARE} && n <= B_len / n",
            counters2 + _nest(SQUARE2, [f"A[{IDX2}] = B[{IDX2}];"]),
            "i",
            "square",
            (NONDET_FILL,),
        ),
        _multi(
            "copy-3-n-u",
            ("long A[]", "long A_len", "const long B[]", "long B_len", "long m", "long n", "long p"),
            f"{REQ_GRID3} && m <= B_len / p / n",
            counters3 + _nest(GRID3, [f"A[{IDX3}] = B[{IDX3}];"]),
            "i",
            "grid3",
            (NONDET_FILL,),
        ),
        _multi(
            "copy-partial-2-n-u",
            ("long A[]", "long A_len", "const long B[]", "long B_len", "long m", "long n", "long q", "long s"),
            f"{REQ_GRID2} && m <= B_len / n && 0 <= q && q < m && 0 <= s && s < n",
            counters2 + _nest((("i", "0", "q"), ("j", "0", "s")), [f"A[{IDX2}] = B[{IDX2}];"]),
            "i",
            "grid2-partial",
            (NONDET_FILL,),
        ),
        _multi(
            "copy-partial-3-u",
            ("long A[]", "long A_len", "const long B[]", "long B_len", "long n", "long p"),
            f"{REQ_CUBE} && n <= B_len / n / n && 0 <= p && p < n",
            counters3
            + _nest((("i", "0", "p"), ("j", "0", "p"), ("k", "0", "p")), [f"A[{CUBE_IDX3}] = B[{CUBE_IDX3}];"])
            + _nest((("i", "p", "n"), ("j", "p", "n"), ("k", "p", "n")), [f"A[{CUBE_IDX3}] = B[{CUBE_IDX3}];"]),
            "i",
            "cube-partial",
            (NONDET_FILL,),
        ),
        _multi(
            "diff-2-n-u",
            grid2_abc,
            f"{REQ_GRID2} && m <= B_len / n && m <= C_len / n",
            counters2 + _nest(GRID2, [f"C[{IDX2}] = A[{IDX2}] - B[{IDX2}];"]),
            "i",
            "grid2",
            (NONDET_FILL,),
        ),
        _multi(
            "diff-3-n-u",
            grid3_abc,
            f"{REQ_GRID3} && m <= B_len / p / n && m <= C_len / p / n",
            counters3 + _nest(GRID3, [f"C[{IDX3}] = A[{IDX3}] - B[{IDX3}];"]),
            "i",
            "grid3",
            (NONDET_FILL,),
        ),
        _multi(
            "init-2-n-u",
            ("long A[]", "long A_len", "long m", "long n", "long C"),
            REQ_GRID2,
            counters2 + _nest(GRID2, [f"A[{IDX2}] = C;"]),
            "i",
            "grid2",
            (T_NONDET + " (C)",),
        ),
        _multi(
            "init-3-u",
            ("long A[]", "long A_len", "long n", "long C"),
            REQ_CUBE,
            counters3 + _nest(CUBE3, [f"A[{CUBE_IDX3}] = C;"]),
            "i",
            "cube",
            (T_NONDET + " (C)",),
        ),
        _multi(
            "init-4-n-u",
            ("long A[]", "long A_len", "long m", "long n", "long p", "long q", "long C"),
            "0 <= m && 0 < n && 0 < p && 0 < q && m <= A_len / q / p / n",
            counters3 + ["long l = 0;"] + _nest(GRID3 + (("l", "0", "q"),), [f"A[(({IDX2}) * p + k) * q + l] = C;"]),
            "i",
            "grid4",
            (T_NONDET + " (C)",),
        ),
        _multi(
            "init-non-constant-2-n-u",
            ("long A[]", "long A_len", "long m", "long n", "long q", "long s", "long C"),
            f"{REQ_GRID2} && 0 <= q && q < m && 0 <= s && s < n",
            counters2 + _nest((("i", "0", "q"), ("j", "0", "s")), [f"A[{IDX2}] = i + j + C;"]),
            "i",
            "grid2-partial",
            (T_NONDET + " (C, q, s)",),
        ),
        _multi(
            "init-non-constant-3-u",
            ("long A[]", "long A_len", "long n", "long C"),
            REQ_CUBE,
            counters3 + _nest(CUBE3, [f"A[{CUBE_IDX3}] = i + j + k + C;"]),
            "i",
            "cube",
            (T_NONDET + " (C)",),
        ),
        _multi(
            "max-2-u",
            ("const long A[]", "long A_len", "long n"),
            REQ_SQUARE,
            counters2
            + ["long max = A[0];"]
            + _nest(SQUARE2, [f"if (A[{IDX2}] > max) {{", f"    max = A[{IDX2}];", "}"]),
            "max",
            "square",
            (NONDET_FILL,),
        ),
        _multi(
            "max-3-n-u",
            ("const long A[]", "long A_len", "long m", "long n", "long p"),
            "0 < m && 0 < n && 0 < p && m <= A_len / p / n",
            counters3 + ["long max = A[0];"] + _nest(GRID3, [f"if (A[{IDX3}] > max) {{", f"    max = A[{IDX3}];", "}"]),
            "max",
            "grid3-positive",
            (NONDET_FILL,),
        ),
        _multi(
            "min-2-u",
            ("const long A[]", "long A_len", "long n"),
            REQ_SQUARE,
            counters2
            + ["long min = A[0];"]
            + _nest(SQUARE2, [f"if (A[{IDX2}] < min) {{", f"    min = A[{IDX2}];", "}"]),
            "min",
            "square",
            (NONDET_FILL,),
        ),
        _multi(
            "min-3-n-u",
            ("const long A[]", "long A_len", "long m", "long n", "long p"),
            "0 < m && 0 < n && 0 < p && m <= A_len / p / n",
            counters3 + ["long min = A[0];"] + _nest(GRID3, [f"if (A[{IDX3}] < min) {{", f"    min = A[{IDX3}];", "}"]),
            "min",
            "grid3-positive",
            (NONDET_FILL,),
        ),
        _multi(
            "rev-2-n-u",
            ("long A[]", "long A_len", "const long B[]", "long B_len", "long m", "long n"),
            f"{REQ_GRID2} && m <= B_len / n",
            counters2 + _nest(GRID2, [f"A[{IDX2}] = B[(m - i - 1) * n + (n - j - 1)];"]),
            "i",
            "grid2",
            (NONDET_FILL,),
        ),
        _multi(
            "rev-3-u",
            ("long A[]", "long A_len", "const long B[]", "long B_len", "long n"),
            f"{REQ_CUBE} && n <= B_len / n / n",
            counters3 + _nest(CUBE3, [f"A[{CUBE_IDX3}] = B[((n - i - 1) * n + (n - j - 1)) * n + (n - k - 1)];"]),
            "i",
            "cube",
            (NONDET_FILL,),
        ),
        _multi(
            "transpose-u",
            ("const long A[]", "long A_len", "long C[]", "long C_len", "long n"),
            f"{REQ_SQUARE} && n <= C_len / n",
            counters2 + _nest(SQUARE2, [f"C[j * n + i] = A[{IDX2}];"]),
            "i",
            "square",
            (NONDET_FILL,),
        ),
    ]
    return tasks


TASKS: tuple[Task, ...] = tuple(
    sorted(_tiling_tasks(), key=lambda t: t.upstream) + sorted(_multi_tasks(), key=lambda t: t.upstream)
)


def render(task: Task) -> str:
    """Deterministic kernel.c in the house style of kernels/*/kernel.c (no canary: sealing embeds it)."""
    return (
        '#include "spec.h"\n\n'
        f"long {task.name}({', '.join(task.parameters)})\n{{\n"
        f"    REQUIRES({task.requires});\n{task.body}\n    return {task.returns};\n}}\n"
    )


# --- generation profiles (pfc-eval-inputs-v1) ---------------------------------------------------------------------

_ARRAY_LENGTHS = {
    "tiling": (2, 1024),
    "grid2": (1, 4096),
    "grid2-partial": (64, 4096),
    "square": (1600, 4096),
    "cube": (1728, 4096),
    "cube-partial": (1728, 4096),
    "grid3": (512, 4096),
    "grid3-positive": (512, 4096),
    "grid4": (625, 2048),
}
_VALUES = (-1000000, 1000000)
_EXTRA_RANGE = (-1000, 1000)


def _names(task: Task) -> tuple[list[str], list[str]]:
    arrays = [p.split()[-1].rstrip("[]") for p in task.parameters if p.endswith("[]")]
    scalars = [p.split()[-1] for p in task.parameters if not p.endswith("[]") and not p.endswith("_len")]
    return arrays, scalars


def profile(task: Task, program: ir.Program) -> dict[str, object]:
    """A bound profile from the task's shape: size scalars derived from the arrays, extras in a small range."""
    arrays, scalars = _names(task)
    low, high = _ARRAY_LENGTHS[task.shape]
    array_specs = {name: {"length": [low, high], "values": list(_VALUES), "sorted": False} for name in arrays}
    specs: dict[str, dict[str, object]] = {}
    boundary: list[dict[str, object]] = []
    if task.shape == "tiling":
        size = scalars[0]
        specs[size] = {"kind": "prefix", "low": 2, "of": arrays}
        for extra in scalars[1:]:
            specs[extra] = {"kind": "range", "low": _EXTRA_RANGE[0], "high": _EXTRA_RANGE[1]}
        extras = {e: 2 for e in scalars[1:]}
        boundary = [
            {"arrays": {a: [3, -3] for a in arrays}, "scalars": {size: 2, **extras}},
            {"arrays": {a: [1, 2, 3] for a in arrays}, "scalars": {size: 2, **extras}},
            {"arrays": {a: [5, 4, 3, 2, 1] for a in arrays}, "scalars": {size: 5, **extras}},
        ]
    elif task.shape in ("grid2", "grid2-partial"):
        specs["n"] = (
            {"kind": "range", "low": 1, "high": 32}
            if task.shape == "grid2-partial"
            else {"kind": "prefix", "low": 1, "of": [arrays[0]]}
        )
        specs["m"] = {"kind": "quotient", "of": arrays, "by": "n"}
        for extra in (s for s in scalars if s not in ("m", "n")):
            specs[extra] = (
                {"kind": "range", "low": 0, "high": 2}
                if extra in ("q", "s")
                else {"kind": "range", "low": _EXTRA_RANGE[0], "high": _EXTRA_RANGE[1]}
            )
        extras = {s: (0 if s in ("q", "s") else 7) for s in scalars if s not in ("m", "n")}
        boundary = [
            {"arrays": {a: [i + 1] for i, a in enumerate(arrays)}, "scalars": {"n": 1, "m": 1, **extras}},
            {"arrays": {a: [1, 2, 3, 4, 5, 6] for a in arrays}, "scalars": {"n": 3, "m": 2, **extras}},
        ]
        if task.shape == "grid2":  # m = 0 is admitted only without the partial bounds q < m
            boundary.append(
                {"arrays": {a: [i + 1] for i, a in enumerate(arrays)}, "scalars": {"n": 1, "m": 0, **extras}}
            )
    elif task.shape == "square":
        specs["n"] = {"kind": "range", "low": 1, "high": 40}
        boundary = [
            {"arrays": {a: [i + 1] for i, a in enumerate(arrays)}, "scalars": {"n": 1}},
            {"arrays": {a: [4, 3, 2, 1] for a in arrays}, "scalars": {"n": 2}},
        ]
    elif task.shape in ("cube", "cube-partial"):
        specs["n"] = {"kind": "range", "low": 1, "high": 12}
        for extra in (s for s in scalars if s != "n"):
            specs[extra] = (
                {"kind": "range", "low": 0, "high": 3}
                if extra == "p"
                else {"kind": "range", "low": _EXTRA_RANGE[0], "high": _EXTRA_RANGE[1]}
            )
        extras = {s: (0 if s == "p" else 7) for s in scalars if s != "n"}
        boundary = [
            {"arrays": {a: [i + 1] for i, a in enumerate(arrays)}, "scalars": {"n": 1, **extras}},
            {"arrays": {a: list(range(8)) for a in arrays}, "scalars": {"n": 2, **extras}},
        ]
    elif task.shape in ("grid3", "grid3-positive"):
        specs["m"] = {"kind": "range", "low": 0 if task.shape == "grid3" else 1, "high": 8}
        specs["n"] = {"kind": "range", "low": 1, "high": 8}
        specs["p"] = {"kind": "range", "low": 1, "high": 8}
        boundary = [
            {"arrays": {a: [i + 1] for i, a in enumerate(arrays)}, "scalars": {"m": 1, "n": 1, "p": 1}},
            {"arrays": {a: list(range(8)) for a in arrays}, "scalars": {"m": 2, "n": 2, "p": 2}},
        ]
    else:  # grid4
        specs["m"] = {"kind": "range", "low": 0, "high": 5}
        for name in ("n", "p", "q"):
            specs[name] = {"kind": "range", "low": 1, "high": 5}
        specs["C"] = {"kind": "range", "low": _EXTRA_RANGE[0], "high": _EXTRA_RANGE[1]}
        boundary = [
            {"arrays": {a: [1] for a in arrays}, "scalars": {"m": 1, "n": 1, "p": 1, "q": 1, "C": 7}},
            {"arrays": {a: list(range(16)) for a in arrays}, "scalars": {"m": 2, "n": 2, "p": 2, "q": 2, "C": -7}},
        ]
    function = program.functions[0]
    declared = {p.symbol.name for p in function.parameters if p.length is None} - {
        p.length.name for p in function.parameters if p.length is not None
    }
    if set(specs) != declared:
        raise ValueError(f"{task.name}: profile scalars {sorted(specs)} differ from the parameters {sorted(declared)}")
    return {
        "profile": generate.PROFILE_VERSION,
        "kernel": task.name,
        "function": function.name,
        "program_sha256": generate.program_digest(program),
        "cases": 1000,
        "arrays": array_specs,
        "scalars": specs,
        "boundary": boundary,
        "stress": 0.1,
    }


# --- host drivers -------------------------------------------------------------------------------------------------

_DIMENSIONS = (
    ("half", "long half = len / 2;"),
    ("side", "long side = 1; while ((side + 1) * (side + 1) <= len) side = side + 1;"),
    ("cube", "long cube = 1; while ((cube + 1) * (cube + 1) * (cube + 1) <= len) cube = cube + 1;"),
    ("cols", "long cols = len < 64 ? len : 64;"),
    ("rows", "long rows = len / cols;"),
    ("p3", "long p3 = len < 16 ? len : 16;"),
    ("n3", "long n3 = len / p3 < 16 ? len / p3 : 16;"),
    ("m3", "long m3 = len / (n3 * p3);"),
    ("q4", "long q4 = len < 8 ? len : 8;"),
    ("p4", "long p4 = len / q4 < 8 ? len / q4 : 8;"),
    ("n4", "long n4 = len / (q4 * p4) < 8 ? len / (q4 * p4) : 8;"),
    ("m4", "long m4 = len / (n4 * p4 * q4);"),
)
_SCALAR_ARGUMENTS = {
    "tiling": {"SIZE": "len", "CELLCOUNT": "len", "MINVAL": "2"},
    "grid2": {"m": "rows", "n": "cols", "C": "7"},
    "grid2-partial": {"m": "rows", "n": "cols", "q": "rows / 2", "s": "cols / 2", "C": "7"},
    "square": {"n": "side"},
    "cube": {"n": "cube", "C": "7"},
    "cube-partial": {"n": "cube", "p": "cube / 2"},
    "grid3": {"m": "m3", "n": "n3", "p": "p3"},
    "grid3-positive": {"m": "m3", "n": "n3", "p": "p3"},
    "grid4": {"m": "m4", "n": "n4", "p": "p4", "q": "q4", "C": "7"},
}
BUFFERS = ("a", "b", "c", "d")


def driver_arguments(task: Task) -> list[str]:
    arrays, _ = _names(task)
    if len(arrays) > len(BUFFERS):
        raise ValueError(f"{task.name}: more arrays than driver buffers")
    buffers = dict(zip(arrays, BUFFERS, strict=False))
    arguments = []
    for parameter in task.parameters:
        name = parameter.split()[-1].rstrip("[]")
        if parameter.endswith("[]"):
            arguments.append(buffers[name])
        elif name.endswith("_len") and name[:-4] in buffers:
            arguments.append("len")
        else:
            arguments.append(_SCALAR_ARGUMENTS[task.shape][name])
    return arguments


def driver(task: Task, program: ir.Program) -> str:
    """A standalone host-C timing driver like pfc.drivers.benchmark_driver: `program [elements [repeats]]`."""
    function = program.functions[0]
    parameters = ", ".join(ir._declaration(p.symbol) for p in function.parameters)
    call = f"{task.name}({', '.join(driver_arguments(task))})"
    needed: set[str] = set()
    for name, _line in reversed(_DIMENSIONS):
        if re.search(rf"\b{name}\b", call) or any(
            re.search(rf"\b{name}\b", _dimension_line(other)) for other in needed
        ):
            needed.add(name)
    setup = "\n".join(f" {line}" for name, line in _DIMENSIONS if name in needed)
    return f"""/* Generated by pfc_eval.svcomp_arrays.driver for the external held-out set (SV-COMP {task.upstream}). */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
{function.return_type} {task.name}({parameters});
static long positive(const char *text, long maximum) {{
 char *end; errno=0; long value=strtol(text,&end,10);
 return errno || *end || end==text || value<1 || value>maximum ? 0 : value;
}}
int main(int argc,char **argv) {{
 if(argc>3) return 2;
 long len=argc>1?positive(argv[1],16777216):16777216;
 long repeats=argc>2?positive(argv[2],50):50;
 if(len<16 || !repeats) return 2;
 long *a=calloc((size_t)len,sizeof(long));
 long *b=calloc((size_t)len,sizeof(long));
 long *c=calloc((size_t)len,sizeof(long));
 long *d=calloc((size_t)len,sizeof(long));
 if(!a || !b || !c || !d) {{free(a);free(b);free(c);free(d);return 2;}}
 for(long i=0;i<len;i++) {{a[i]=i%5;b[i]=(i%7)-3;c[i]=1;d[i]=i%3;}}
{setup}
 uint64_t checksum=0;
 for(long iteration=0;iteration<repeats;iteration++) {{
  checksum+=(uint64_t){call};
 }}
 checksum+=(uint64_t)a[len-1]+(uint64_t)b[len-1]+(uint64_t)c[len-1]+(uint64_t)d[len-1];
 printf("%llu\\n",(unsigned long long)checksum);
 free(a);free(b);free(c);free(d);
 return ferror(stdout)?2:0;
}}
"""


def _dimension_line(name: str) -> str:
    return dict(_DIMENSIONS)[name]


# --- writing the set ----------------------------------------------------------------------------------------------


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lower_text(text: str, name: str, cpp: str) -> ir.Program:
    with tempfile.TemporaryDirectory(prefix="pfc-svcomp-") as scratch:
        path = Path(scratch) / name / "kernel.c"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        return lower(parse_source(path, cpp=cpp))


def describe(task: Task, program: ir.Program, target: Target) -> dict[str, object]:
    """Frontend acceptance, mechanical B0 through the frozen admit(), guard sites (checked emission); no proposer."""
    invariants = ports.hand_invariants(program)
    admission = admit(program, json.dumps({"invariants": invariants}), target)
    checked = emit(program, target, mode="checked")
    return {
        "name": task.name,
        "family": task.family,
        "upstream_file": f"c/{task.upstream}",
        "source_url": f"{UPSTREAM['repository']}/-/blob/{UPSTREAM['commit']}/c/{task.upstream}",
        "upstream_sha256": task.upstream_sha256,
        "transformations": list(task.transformations),
        "shape": task.shape,
        "loops": len(invariants),
        "hand_invariants": invariants,
        "outside_b1": [not ports.in_b1_family(text) for text in invariants],
        "b0_admitted": admission.accepted,
        "b0_problem": admission.problem,
        "required_obligations": (
            sum(r.obligation.category == "required" for r in admission.proof.results) if admission.proof else None
        ),
        "guard_sites": len(checked.decisions),
        "overflow_sites": len(checked.overflow),
    }


def write(
    destination: Path,
    profiles_dir: Path,
    *,
    compiler: str,
    cpp: str = "cpp",
    upstream: Path | None = None,
    tasks: Sequence[Task] = TASKS,
) -> dict[str, object]:
    """Write heldout/<name>/{kernel.c,main.c}, profiles-heldout/<name>.json and PROVENANCE.json; skip B0 rejections."""
    target = read_target(compiler)
    destination.mkdir(parents=True, exist_ok=True)
    profiles_dir.mkdir(parents=True, exist_ok=True)
    if (destination / "PROVENANCE.json").exists():
        raise FileExistsError(f"{destination}/PROVENANCE.json exists; the external set is written once")
    entries: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for task in tasks:
        if upstream is not None:
            actual = sha256((upstream / "c" / task.upstream).read_bytes())
            if actual != task.upstream_sha256:
                raise ValueError(f"{task.upstream}: upstream digest {actual} differs from the recorded one")
        text = render(task)
        try:
            program = _lower_text(text, task.name, cpp)
            entry = describe(task, program, target)
        except FrontendError as error:
            # The frozen compiler rejects the port itself (for example the cumulative VC limit, `vc-complexity`):
            # a deterministic exclusion, recorded with the diagnostic and never worked around.
            rejected.append(
                {
                    "name": task.name,
                    "family": task.family,
                    "upstream_file": f"c/{task.upstream}",
                    "upstream_sha256": task.upstream_sha256,
                    "port_sha256": sha256(text.encode("utf-8")),
                    "b0_admitted": None,
                    "b0_problem": None,
                    "frontend_rejection": f"{error.diagnostic.rule}: {error.diagnostic.message}",
                }
            )
            continue
        entry["port"] = f"{task.name}/kernel.c"
        entry["port_sha256"] = sha256(text.encode("utf-8"))
        entry["frontend_rejection"] = None
        if not entry["b0_admitted"]:
            rejected.append(entry)
            continue
        directory = destination / task.name
        if directory.exists() or (profiles_dir / f"{task.name}.json").exists():
            raise FileExistsError(f"{task.name}: kernel directory or profile already exists")
        directory.mkdir()
        (directory / "kernel.c").write_text(text, encoding="utf-8")
        (directory / "main.c").write_text(driver(task, program), encoding="utf-8")
        bound = profile(task, program)
        generate.bind(generate.parse_profile(bound), program)
        (profiles_dir / f"{task.name}.json").write_text(json.dumps(bound, indent=2) + "\n", encoding="utf-8")
        entry["profile_sha256"] = sha256((profiles_dir / f"{task.name}.json").read_bytes())
        entries.append(entry)
    provenance = {
        "schema_version": 1,
        "upstream": UPSTREAM,
        "selection": SELECTION,
        "rules": list(RULES),
        "excluded": EXCLUDED,
        "rejected": rejected,  # B0 not admitted by the frozen admit(), or the frozen compiler rejected the port
        "generator": "pfc_eval/svcomp_arrays.py",
        "entries": entries,
    }
    (destination / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if upstream is not None and (upstream / "LICENSE.Apache-2.0.txt").is_file():
        shutil.copyfile(upstream / "LICENSE.Apache-2.0.txt", destination / "LICENSE.Apache-2.0.txt")
    return provenance


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pfc_eval.svcomp_arrays",
        description="Write the external held-out set ported from SV-COMP array-tiling and array-multidimensional.",
        epilog="example: uv run --locked python -m pfc_eval.svcomp_arrays --output heldout "
        "--profiles pfc_eval/profiles-heldout --upstream /path/to/sv-benchmarks",
    )
    parser.add_argument("--output", required=True, help="held-out directory (PROVENANCE.json must not exist)")
    parser.add_argument("--profiles", required=True, help="profiles directory for the generated profiles")
    parser.add_argument("--upstream", default=None, help="sv-benchmarks checkout to verify the upstream digests")
    parser.add_argument("--clang", default=None, help="clang executable (default: PFC_CLANG or clang-19)")
    parser.add_argument("--cpp", default="cpp", help="cpp executable")
    parser.add_argument("--dry-run", action="store_true", help="print the rendered kernels and write nothing")
    arguments = parser.parse_args(argv)
    if arguments.dry_run:
        for task in TASKS:
            print(f"// {task.upstream}")
            print(render(task))
        return 0
    import os

    from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG

    compiler = arguments.clang or os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG)
    try:
        provenance = write(
            Path(arguments.output),
            Path(arguments.profiles),
            compiler=compiler,
            cpp=arguments.cpp,
            upstream=Path(arguments.upstream) if arguments.upstream else None,
        )
    except (FileExistsError, ValueError, OSError) as error:
        print(f"pfc_eval: {error}", file=sys.stderr)
        return 1
    entries = provenance["entries"]
    rejected = provenance["rejected"]
    assert isinstance(entries, list) and isinstance(rejected, list)
    print(
        f"{len(entries)} task(s) written, {len(rejected)} rejected by the frozen admit() (recorded in PROVENANCE.json)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
