"""Ported public loops for the contamination strata of design §5 (roadmap E10): the Zilu/SV-COMP `loop-zilu`
benchmarks rewritten as `long` functions in the pfc subset, with per-loop provenance, a hand invariant (B0),
and the obfuscated stratum produced by `robustness.obfuscate`.

Each port is a structured record in `PORTS`; `render` turns it into `kernel.c` deterministically, so the
committed files can be regenerated and compared byte for byte. The transformations are mechanical and
listed per port: `int` becomes `long`, nondeterministic initial values become parameters, the entry
assumption becomes `REQUIRES`, the final `__VERIFIER_assert` becomes `ASSERT` after the loop, and a
nondeterministic loop guard becomes a counter bounded by a fresh `steps` parameter (one free iteration
count). Porting weakens the memorization test, as the design says; nothing here is a held-out kernel.

Run once, outside campaigns: `uv run --locked python -m pfc_eval.ports --output bench/external --seed 0`.
"""

import argparse
import hashlib
import json
import random
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pfc import ir
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.propose.invariants import admit
from pfc.target import read_target
from pfc.wp import walk
from pfc_eval import expressions, robustness

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = {
    "repository": "https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks",
    "commit": "f3feef51a07ffe7c6ef58f3da3b7fe041af4fd25",
    "directory": "c/loop-zilu",
    "license": "GPL-2.0-only (upstream LICENSE.GPLv2.txt, copied beside the ports)",
    "origin": "Li, Sun, Li, Le, Lin: Automatic loop-invariant generation and refinement through selective sampling, "
    "ASE 2017 (the 53 Zilu benchmarks, as packaged by Martin Spiessl for SV-COMP)",
}
SELECTION = (
    "benchmark01..53 in numeric order, skipping the *_abstracted duplicates; the first 30 whose loop is "
    "expressible in the pfc subset, whose assertion holds for every input the precondition admits, and whose "
    "hand invariant the frozen admit() accepts (the B0 ceiling exists); the rule was fixed before any proposer run"
)
SKIPPED = {
    "benchmark03_linear": "the per-iteration nondeterministic flag needs an array read whose guard removal depends on "
    "overflow proofs of unbounded accumulators (i += x, j += y); the frozen authorization admits no linear hand "
    "invariant, so no B0 ceiling exists",
    "benchmark07_linear": "k is unconstrained at entry, so the assertion k > n does not hold for every admitted input",
    "benchmark09_conjunctive": "break statement, outside the subset",
    "benchmark10_conjunctive": "break statement, outside the subset",
    "benchmark21_disjunctive": "loop condition `1` with an informal realloopcondition; not a program of the subset",
    "benchmark28_linear": "assertion j == i fails for negative j admitted by i * i < j * j",
    "benchmark31_disjunctive": "loop condition `1` with an informal realloopcondition; not a program of the subset",
}
T_LONG = "int -> long"
T_PARAMS = "nondeterministic initial values -> parameters"
T_REQUIRES = "entry assumption -> REQUIRES"
T_ASSERT = "__VERIFIER_assert -> ASSERT after the loop"
T_SIMPLE = "x++ / x-- / x += c -> simple assignment"
T_STEPS = "nondeterministic loop guard -> counter step < steps with REQUIRES 0 <= steps"
T_BRACES = "braces around single-statement branches"
T_RETURN = "main -> function returning a loop variable"
COMMON = (T_LONG, T_PARAMS, T_REQUIRES, T_ASSERT, T_SIMPLE, T_RETURN)
STEP = "long step = 0;"
TICK = "step = step + 1;"


@dataclass(frozen=True)
class Port:
    name: str
    upstream: str
    upstream_sha256: str
    parameters: tuple[str, ...]
    requires: str
    locals_: tuple[str, ...]
    guard: str
    body: tuple[str, ...]
    assertion: str
    invariant: str
    returns: str
    transformations: tuple[str, ...]


def _port(
    number: int,
    upstream: str,
    sha: str,
    parameters: Sequence[str],
    requires: str,
    guard: str,
    body: Sequence[str],
    assertion: str,
    invariant: str,
    returns: str,
    *,
    locals_: Sequence[str] = (),
    extra: Sequence[str] = (),
) -> Port:
    return Port(
        f"zilu{number:02d}",
        upstream,
        sha,
        tuple(parameters),
        requires,
        tuple(locals_),
        guard,
        tuple(body),
        assertion,
        invariant,
        returns,
        COMMON + tuple(extra),
    )


PORTS: tuple[Port, ...] = (
    _port(
        1,
        "benchmark01_conjunctive.c",
        "eb49260461cefbf5c3dd21d1ceae53b12fc63b5489fca03ef4e30dd6952fd124",
        ("long x", "long y", "long steps"),
        "x == 1 && y == 1 && 0 <= steps",
        "step < steps",
        ("x = x + y;", "y = x;", TICK),
        "y >= 1",
        "x >= 1 && y >= 1",
        "y",
        locals_=(STEP,),
        extra=(T_STEPS,),
    ),
    _port(
        2,
        "benchmark02_linear.c",
        "c2949f5ad2551a2aeb85c19bbf445f95c9c01300548babb6b1409de0a58ed81c",
        ("long n", "long l"),
        "l > 0",
        "i < n",
        ("i = i + 1;",),
        "l >= 1",
        "l > 0",
        "i",
        locals_=("long i = l;",),
    ),
    _port(
        4,
        "benchmark04_conjunctive.c",
        "0f05c589421d8e3d6b7f849625b36aa3dbfd97a1290fcfe4bd84327e66cb8d62",
        ("long k", "long j", "long n"),
        "n >= 1 && k >= n && j == 0",
        "j <= n - 1",
        ("j = j + 1;", "k = k - 1;"),
        "k >= 0",
        "k + j >= n && j <= n",
        "k",
    ),
    _port(
        5,
        "benchmark05_conjunctive.c",
        "72cf009be33d60e2c470b69dc42d818d6619db7abb4b0a513f3a81c79cad1468",
        ("long x", "long y", "long n"),
        "x >= 0 && x <= y && y < n",
        "x < n",
        ("x = x + 1;", "if (x > y) {\n    y = y + 1;\n}"),
        "y == n",
        "x <= y && y <= n",
        "y",
        extra=(T_BRACES,),
    ),
    _port(
        6,
        "benchmark06_conjunctive.c",
        "cf1e365b7b60392b5698ab50c03033caa30943dde7d78af03fc0b938aac9dbf5",
        ("long i", "long x", "long y", "long k", "long steps"),
        "x + y == k && 0 <= steps",
        "step < steps",
        (
            "if (j == i) {\n    x = x + 1;\n    y = y - 1;\n} else {\n    y = y + 1;\n    x = x - 1;\n}",
            "j = j + 1;",
            TICK,
        ),
        "x + y == k",
        "x + y == k",
        "x",
        locals_=("long j = 0;", STEP),
        extra=(T_STEPS,),
    ),
    _port(
        8,
        "benchmark08_conjunctive.c",
        "a61545396ebb848fdffe04b6e1f5afa32e37f478d3228c0dd9c2570235894120",
        ("long n", "long sum", "long i"),
        "n >= 0 && sum == 0 && i == 0",
        "i < n",
        ("sum = sum + i;", "i = i + 1;"),
        "sum >= 0",
        "sum >= 0 && i >= 0",
        "sum",
    ),
    _port(
        11,
        "benchmark11_linear.c",
        "c270ba82ea397321b76ed55010a3d18d0852eb532ffa77e735d1eed204156722",
        ("long x", "long n"),
        "x == 0 && n > 0",
        "x < n",
        ("x = x + 1;",),
        "x == n",
        "x <= n",
        "x",
    ),
    _port(
        12,
        "benchmark12_linear.c",
        "7510af0b50cb94a8692cbdc5db82d1414ae9014fadbae8c5f1fc70bccac2f719",
        ("long x", "long y", "long t", "long steps"),
        "x != y && y == t && 0 <= steps",
        "step < steps",
        ("if (x > 0) {\n    y = y + x;\n}", TICK),
        "y >= t",
        "y >= t",
        "y",
        locals_=(STEP,),
        extra=(T_STEPS, T_BRACES),
    ),
    _port(
        13,
        "benchmark13_conjunctive.c",
        "2003091f7614783d9877ad62c7b43caf4644405782bf387d3c63828eca160ad6",
        ("long i", "long j", "long k"),
        "i == 0 && j == 0",
        "i <= k",
        ("i = i + 1;", "j = j + 1;"),
        "j == i",
        "i == j",
        "j",
    ),
    _port(
        14,
        "benchmark14_linear.c",
        "6f16bb240ed1567067fa211cbe8089d52110d75f33ccdb83e72077197b25d2b3",
        ("long i",),
        "i >= 0 && i <= 200",
        "i > 0",
        ("i = i - 1;",),
        "i >= 0",
        "i >= 0",
        "i",
    ),
    _port(
        15,
        "benchmark15_conjunctive.c",
        "5f284310fb8232d8f502b0139e496a84794b1949a85d276a8048dc6f06c8abd5",
        ("long low", "long mid", "long high"),
        "low == 0 && mid >= 1 && high == 2 * mid",
        "mid > 0",
        ("low = low + 1;", "high = high - 1;", "mid = mid - 1;"),
        "low == high",
        "high == low + 2 * mid && mid >= 0",
        "low",
    ),
    _port(
        16,
        "benchmark16_conjunctive.c",
        "a1376ecc40fc824664ba8724836333114528b8e2f79ec447a2e677868159542c",
        ("long i", "long k", "long steps"),
        "0 <= k && k <= 1 && i == 1 && 0 <= steps",
        "step < steps",
        ("i = i + 1;", "k = k - 1;", TICK),
        "1 <= i + k && i + k <= 2 && i >= 1",
        "1 <= i + k && i + k <= 2 && i >= 1",
        "i",
        locals_=(STEP,),
        extra=(T_STEPS,),
    ),
    _port(
        17,
        "benchmark17_conjunctive.c",
        "f5b8e195704679ef9d4365b98deb99df2b7011e2df47d21915ce2fd370f3c41d",
        ("long i", "long k", "long n"),
        "i == 0 && k == 0",
        "i < n",
        ("i = i + 1;", "k = k + 1;"),
        "k >= n",
        "i == k",
        "k",
    ),
    _port(
        18,
        "benchmark18_conjunctive.c",
        "2dc2db50b0fd020db5bacebd62c36abda1395b203401481e547bf4c8b434a763",
        ("long i", "long k", "long n"),
        "i == 0 && k == 0 && n > 0",
        "i < n",
        ("i = i + 1;", "k = k + 1;"),
        "i == k && k == n",
        "i == k && i <= n",
        "k",
    ),
    _port(
        19,
        "benchmark19_conjunctive.c",
        "d7e91d1a87fe71bdf277fb79432441d9ce84ed86f41373204eb52911b4a16441",
        ("long j", "long k", "long n"),
        "j == n && k == n && n > 0",
        "j > 0 && n > 0",
        ("j = j - 1;", "k = k - 1;"),
        "k == 0",
        "j == k && j >= 0",
        "k",
    ),
    _port(
        20,
        "benchmark20_conjunctive.c",
        "54fb72fb73987d196630dc309681b0d7864602d7a91c0016bff42358443ec609",
        ("long i", "long n", "long sum"),
        "i == 0 && n >= 0 && n <= 100 && sum == 0",
        "i < n",
        ("sum = sum + i;", "i = i + 1;"),
        "sum >= 0",
        "sum >= 0 && i >= 0",
        "sum",
    ),
    _port(
        22,
        "benchmark22_conjunctive.c",
        "332149e5bab02f901d22a394d7fc8b75a68c916816faa3fd6dccce4a7c616f3c",
        ("long x", "long y", "long steps"),
        "x == 1 && y == 0 && 0 <= steps",
        "step < steps",
        ("x = x + y;", "y = y + 1;", TICK),
        "x >= y",
        "x >= y && y >= 0 && x >= 1",
        "x",
        locals_=(STEP,),
        extra=(T_STEPS,),
    ),
    _port(
        23,
        "benchmark23_conjunctive.c",
        "61ef2f8a78b1c2af8ddc8defea2954950b17db5e561108fd4a8b6f210c384bc2",
        ("long i", "long j"),
        "i == 0 && j == 0",
        "i < 100",
        ("j = j + 2;", "i = i + 1;"),
        "j == 200",
        "j == 2 * i && i <= 100",
        "j",
    ),
    _port(
        24,
        "benchmark24_conjunctive.c",
        "e6bbd99c2e724766636acbeb4089e9b1cfd2a5b383a9908ccea1cabd76f27733",
        ("long i", "long k", "long n"),
        "i == 0 && k == n && n >= 0",
        "i < n",
        ("k = k - 1;", "i = i + 2;"),
        "2 * k >= n - 1",
        "2 * k + i == 2 * n && i <= n + 1",
        "k",
    ),
    _port(
        25,
        "benchmark25_linear.c",
        "337487b68fe41b6586aa58ec77102e11ca1ce45fa1efbcb519b56c11efd2c505",
        ("long x",),
        "x < 0",
        "x < 10",
        ("x = x + 1;",),
        "x == 10",
        "x <= 10",
        "x",
    ),
    _port(
        26,
        "benchmark26_linear.c",
        "abebd5aed6ed173f0a4f88fc018b40de604ed7710fac0804896f4028412d26a2",
        ("long x", "long y"),
        "x < y",
        "x < y",
        ("x = x + 1;",),
        "x == y",
        "x <= y",
        "x",
    ),
    _port(
        27,
        "benchmark27_linear.c",
        "635607d3696f782e377f8d05ce57901700911de876e1f2f951d0c8cce1ebd6bb",
        ("long i", "long j", "long k"),
        "i < j && k > i - j",
        "i < j",
        ("k = k + 1;", "i = i + 1;"),
        "k > 0",
        "k > i - j",
        "k",
    ),
    _port(
        29,
        "benchmark29_linear.c",
        "f2ccd2e8342798117d6f951f0e6e512b2934173154d2a4e0c4722158a42769d5",
        ("long x", "long y"),
        "x < y",
        "x < y",
        ("x = x + 100;",),
        "x >= y && x <= y + 99",
        "x <= y + 99",
        "x",
    ),
    _port(
        30,
        "benchmark30_conjunctive.c",
        "8fd036e786e8fd1350246eade06863d982f10933fb11b9690cb8793be5e503e6",
        ("long x", "long y", "long steps"),
        "y == x && 0 <= steps",
        "step < steps",
        ("x = x + 1;", "y = y + 1;", TICK),
        "x == y",
        "x == y",
        "x",
        locals_=(STEP,),
        extra=(T_STEPS,),
    ),
    _port(
        32,
        "benchmark32_linear.c",
        "89cd19e25f65337e099b7d725458b715b9f3d55a74700211888983a08d344117",
        ("long x", "long steps"),
        "(x == 1 || x == 2) && 0 <= steps",
        "step < steps",
        ("if (x == 1) {\n    x = 2;\n} else {\n    if (x == 2) {\n        x = 1;\n    }\n}", TICK),
        "x <= 8",
        "x == 1 || x == 2",
        "x",
        locals_=(STEP,),
        extra=(T_STEPS, T_BRACES),
    ),
    _port(
        33,
        "benchmark33_linear.c",
        "bc608b4cc1ed64c7b39c466fabddfb7a0db4e2da8062d705d55bd8977f8adaf3",
        ("long x",),
        "x >= 0",
        "x < 100 && x >= 0",
        ("x = x + 1;",),
        "x >= 100",
        "x >= 0",
        "x",
    ),
    _port(
        34,
        "benchmark34_conjunctive.c",
        "24fed658cef7e0f7406dd7501097bb5f17639373766484233d05223858773d4b",
        ("long j", "long k", "long n"),
        "j == 0 && k == n && n > 0",
        "j < n && n > 0",
        ("j = j + 1;", "k = k - 1;"),
        "k == 0",
        "j + k == n && j <= n",
        "k",
    ),
    _port(
        35,
        "benchmark35_linear.c",
        "728c206902216657eb619c1850c6d83bfd390394a9c29cf730cdf89086341140",
        ("long x",),
        "x >= 0",
        "x >= 0 && x < 10",
        ("x = x + 1;",),
        "x >= 10",
        "x >= 0",
        "x",
    ),
    _port(
        36,
        "benchmark36_conjunctive.c",
        "8980d71ab696d54cee5ef1677bb72e05dead0845347f5a7233d8591ae36a4686",
        ("long x", "long y", "long steps"),
        "x == y && y == 0 && 0 <= steps",
        "step < steps",
        ("x = x + 1;", "y = y + 1;", TICK),
        "x == y && x >= 0",
        "x == y && x >= 0",
        "x",
        locals_=(STEP,),
        extra=(T_STEPS,),
    ),
    _port(
        37,
        "benchmark37_conjunctive.c",
        "0184fba4d7d3180e1c8de64eb13772bb582c3ec516dac091cefffbc15ca7e236",
        ("long x", "long y"),
        "x == y && x >= 0",
        "x > 0",
        ("x = x - 1;", "y = y - 1;"),
        "y >= 0",
        "x == y && x >= 0",
        "y",
    ),
)


def _indent(text: str, level: int) -> str:
    pad = "    " * level
    return "\n".join(pad + line if line else line for line in text.split("\n"))


def render(port: Port) -> str:
    """Deterministic kernel.c in the house style of `kernels/*/kernel.c`."""
    lines = ['#include "spec.h"', "", f"long {port.name}({', '.join(port.parameters)})", "{"]
    lines.append(_indent(f"REQUIRES({port.requires});", 1))
    lines.extend(_indent(local, 1) for local in port.locals_)
    lines.append(_indent(f"while ({port.guard}) {{", 1))
    lines.append(_indent(f"INVARIANT({port.invariant});", 2))
    lines.extend(_indent(statement, 2) for statement in port.body)
    lines.append(_indent("}", 1))
    lines.append(_indent(f"ASSERT({port.assertion});", 1))
    lines.append(_indent(f"return {port.returns};", 1))
    lines.append("}")
    return "\n".join(lines) + "\n"


_VAR = r"[A-Za-z_]\w*"
_B1_CONJUNCT = (
    re.compile(rf"^(-1|0|1) <= {_VAR}$"),
    re.compile(rf"^{_VAR} <= (-1|0|1)$"),
    re.compile(rf"^{_VAR} <= {_VAR}( [+-] 1)?$"),
    re.compile(rf"^{_VAR} == {_VAR}$"),
    re.compile(rf"^{_VAR} == (-1|0|1)$"),
)


def _normalize_conjunct(conjunct: str) -> str:
    """Rewrite >, >= and < into the <= forms of the B1 family (design §5): `v > w` is `w <= v - 1`."""
    conjunct = conjunct.strip("() ")
    rules: tuple[tuple[str, Callable[[str, str], str]], ...] = (
        (rf"^({_VAR}|-?\d+) >= ({_VAR}|-?\d+)$", lambda a, b: f"{b} <= {a}"),
        (rf"^({_VAR}) > (-?\d+)$", lambda a, b: f"{int(b) + 1} <= {a}"),
        (rf"^(-?\d+) < ({_VAR})$", lambda a, b: f"{int(a) + 1} <= {b}"),
        (rf"^({_VAR}) > ({_VAR})$", lambda a, b: f"{b} <= {a} - 1"),
        (rf"^({_VAR}) < ({_VAR})$", lambda a, b: f"{a} <= {b} - 1"),
        (rf"^({_VAR}) < (-?\d+)$", lambda a, b: f"{a} <= {int(b) - 1}"),
    )
    for pattern, rewrite in rules:
        match = re.fullmatch(pattern, conjunct)
        if match:
            return rewrite(match.group(1), match.group(2))
    return conjunct


def in_b1_family(invariant: str) -> bool:
    """Syntactic check: every conjunct is `c <= v`, `v <= c`, `v <= w + c`, `v == w` or `v == c`, c in {-1, 0, 1}."""
    if "||" in invariant:
        return False
    conjuncts = [_normalize_conjunct(part) for part in invariant.split("&&")]
    return all(any(pattern.fullmatch(conjunct) for pattern in _B1_CONJUNCT) for conjunct in conjuncts)


def hand_invariants(program: ir.Program) -> list[str]:
    """The INVARIANT text of every loop in preorder, rendered from the IR (so it survives renaming)."""
    found: list[str] = []
    for function in program.functions:
        for statement in walk(function.body):
            if isinstance(statement, (ir.While, ir.For)) and statement.invariant is not None:
                found.append(expressions.render(statement.invariant.condition))
    return found


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_ports(
    destination: Path, *, seed: int, compiler: str, ports: Sequence[Port] = PORTS, upstream: Path | None = None
) -> dict[str, object]:
    """Write `zilu/<name>/kernel.c`, `zilu-obfuscated/<name>/kernel.c` and PROVENANCE.json; validate B0 with admit()."""
    ported = destination / "zilu"
    obfuscated = destination / "zilu-obfuscated"
    for directory in (ported, obfuscated):
        if directory.exists():
            raise FileExistsError(f"{directory} exists; remove it deliberately before regenerating the ports")
    ported.mkdir(parents=True)
    obfuscated.mkdir(parents=True)
    target = read_target(compiler)
    entries: list[dict[str, object]] = []
    for port in ports:
        if upstream is not None:
            actual = sha256((upstream / port.upstream).read_bytes())
            if actual != port.upstream_sha256:
                raise ValueError(f"{port.upstream}: upstream digest {actual} differs from the recorded one")
        text = render(port)
        path = ported / port.name / "kernel.c"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        program = lower(parse_source(path))
        invariants = hand_invariants(program)
        admission = admit(program, json.dumps({"invariants": invariants}), target)
        variant = robustness.obfuscate(program, seed=seed)
        variant_text = ir.print_c(variant.program)
        variant_path = obfuscated / port.name / "kernel.c"
        variant_path.parent.mkdir()
        variant_path.write_text(variant_text, encoding="utf-8")
        variant_program = lower(parse_source(variant_path))
        variant_admission = admit(variant_program, json.dumps({"invariants": hand_invariants(variant_program)}), target)
        entries.append(
            {
                "name": port.name,
                "upstream_file": f"{UPSTREAM['directory']}/{port.upstream}",
                "source_url": f"{UPSTREAM['repository']}/-/blob/{UPSTREAM['commit']}/{UPSTREAM['directory']}/"
                + port.upstream,
                "upstream_sha256": port.upstream_sha256,
                "port": f"zilu/{port.name}/kernel.c",
                "port_sha256": sha256(text.encode("utf-8")),
                "obfuscated": f"zilu-obfuscated/{port.name}/kernel.c",
                "obfuscated_sha256": sha256(variant_text.encode("utf-8")),
                "obfuscation": variant.detail,
                "mapping": dict(variant.mapping),
                "transformations": list(port.transformations),
                "hand_invariant": port.invariant,
                "b1_family": in_b1_family(port.invariant),
                "b0_admitted": admission.accepted,
                "b0_problem": admission.problem,
                "obfuscated_b0_admitted": variant_admission.accepted,
                "required_obligations": (
                    sum(r.obligation.category == "required" for r in admission.proof.results)
                    if admission.proof
                    else None
                ),
            }
        )
    provenance = {
        "schema_version": 1,
        "upstream": UPSTREAM,
        "selection": SELECTION,
        "skipped": SKIPPED,
        "obfuscation": {
            "seed": seed,
            "method": "pfc_eval.robustness.obfuscate: alpha-renaming plus every commutative operand swap (ADR-0040)",
        },
        "generator": "pfc_eval/ports.py",
        "entries": entries,
    }
    (ported / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if upstream is not None and (upstream / "LICENSE.GPLv2.txt").is_file():
        (ported / "LICENSE.GPLv2.txt").write_bytes((upstream / "LICENSE.GPLv2.txt").read_bytes())
    return provenance


# --- composed stratum (post-E14 improvement, ADR-0046) ---------------------------------------------------

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


@dataclass(frozen=True)
class Composite:
    """Two ported loops in sequence inside one function; the second loop's variables are renamed."""

    name: str
    first: Port
    second: Port
    renaming: tuple[tuple[str, str], ...]

    @property
    def parts(self) -> tuple[str, str]:
        return (self.first.name, self.second.name)


def variable_names(port: Port) -> list[str]:
    """Parameter and local names of a port, in declaration order."""
    names = [parameter.split()[-1].strip("[]") for parameter in port.parameters]
    for local in port.locals_:
        match = re.match(r"\s*(?:const\s+)?long\s+([A-Za-z_]\w*)", local)
        if match:
            names.append(match.group(1))
    return names


def rename(text: str, mapping: Mapping[str, str]) -> str:
    """Rename whole identifiers only; keywords, numbers and macro names are never in `mapping`."""
    return _IDENTIFIER.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def pairs(ports: Sequence[Port], *, seed: int) -> tuple[tuple[Port, Port], ...]:
    """Seeded disjoint pairing of the ports; every port appears exactly once (an odd leftover is dropped)."""
    order = list(ports)
    random.Random(seed).shuffle(order)
    return tuple((order[i], order[i + 1]) for i in range(0, len(order) - 1, 2))


def compose(first: Port, second: Port, *, index: int) -> Composite:
    taken = set(variable_names(first)) | {first.name, second.name, "step"}
    mapping: dict[str, str] = {}
    for name in variable_names(second):
        candidate = name + "2"
        while candidate in taken:
            candidate += "2"
        taken.add(candidate)
        mapping[name] = candidate
    return Composite(f"zilupair{index:02d}", first, second, tuple(mapping.items()))


def render_composite(composite: Composite) -> str:
    """One function: REQUIRES of both parts, both loops in sequence, each goal asserted right after its loop.

    Asserting each part's goal immediately after its own loop keeps the proof of the first goal independent of
    the second loop's invariant; the task still needs both invariants and both assertions to be admitted.
    """
    a, b, mapping = composite.first, composite.second, dict(composite.renaming)
    parameters = list(a.parameters) + [rename(parameter, mapping) for parameter in b.parameters]
    lines = ['#include "spec.h"', "", f"long {composite.name}({', '.join(parameters)})", "{"]
    lines.append(_indent(f"REQUIRES({a.requires} && {rename(b.requires, mapping)});", 1))
    lines.extend(_indent(local, 1) for local in a.locals_)
    lines.extend(_indent(rename(local, mapping), 1) for local in b.locals_)
    lines.append(_indent(f"while ({a.guard}) {{", 1))
    lines.append(_indent(f"INVARIANT({a.invariant});", 2))
    lines.extend(_indent(statement, 2) for statement in a.body)
    lines.append(_indent("}", 1))
    lines.append(_indent(f"ASSERT({a.assertion});", 1))
    lines.append(_indent(f"while ({rename(b.guard, mapping)}) {{", 1))
    lines.append(_indent(f"INVARIANT({rename(b.invariant, mapping)});", 2))
    lines.extend(_indent(rename(statement, mapping), 2) for statement in b.body)
    lines.append(_indent("}", 1))
    lines.append(_indent(f"ASSERT({rename(b.assertion, mapping)});", 1))
    lines.append(_indent(f"return {a.returns};", 1))
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_composed(
    destination: Path, *, seed: int, compiler: str, pairs: Sequence[tuple[Port, Port]] | None = None
) -> dict[str, object]:
    """Write `zilu-composed/<name>/kernel.c` for every admissible composite and PROVENANCE.json for every pair."""
    directory = destination / "zilu-composed"
    if directory.exists():
        raise FileExistsError(f"{directory} exists; remove it deliberately before regenerating the composed stratum")
    directory.mkdir(parents=True)
    target = read_target(compiler)
    entries: list[dict[str, object]] = []
    for index, (first, second) in enumerate(pairs if pairs is not None else globals()["pairs"](PORTS, seed=seed), 1):
        composite = compose(first, second, index=index)
        text = render_composite(composite)
        path = directory / composite.name / "kernel.c"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        program = lower(parse_source(path))
        admission = admit(program, json.dumps({"invariants": hand_invariants(program)}), target)
        if not admission.accepted:
            path.unlink()
            path.parent.rmdir()
        entries.append(
            {
                "name": composite.name,
                "parts": list(composite.parts),
                "renaming": dict(composite.renaming),
                "composed_sha256": sha256(text.encode("utf-8")),
                "loops": 2,
                "hand_invariants": hand_invariants(program),
                "b1_family": all(in_b1_family(i) for i in hand_invariants(program)),
                "b0_admitted": admission.accepted,
                "b0_problem": admission.problem,
                "task": f"zilu-composed/{composite.name}/kernel.c" if admission.accepted else None,
            }
        )
    provenance = {
        "schema_version": 1,
        "upstream": UPSTREAM,
        "pairing": {
            "seed": seed,
            "method": "seeded shuffle of PORTS, consecutive disjoint pairs; second loop alpha-renamed with suffix 2",
            "rule": "a composite is a task only when both hand invariants are admitted together by the frozen admit()",
        },
        "generator": "pfc_eval/ports.py write_composed",
        "entries": entries,
    }
    (directory / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return provenance


def describe(port: Port) -> dict[str, object]:
    return asdict(port)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pfc_eval.ports",
        description="Regenerate the ported Zilu loops and their obfuscated stratum with provenance (roadmap E10).",
        epilog="Example: python -m pfc_eval.ports --output bench/external --seed 0 --upstream /checkout/c/loop-zilu",
    )
    parser.add_argument("--output", required=True, help="parent directory receiving zilu/ and zilu-obfuscated/")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--clang", default="clang-19")
    parser.add_argument(
        "--upstream", default=None, help="checkout of c/loop-zilu to verify digests and copy the license"
    )
    parser.add_argument("--composed", action="store_true", help="write only zilu-composed/ (pairs of ports; ADR-0046)")
    arguments = parser.parse_args(argv)
    if arguments.composed:
        try:
            composed = write_composed(Path(arguments.output), seed=arguments.seed, compiler=arguments.clang)
        except (FileExistsError, ValueError, OSError) as error:
            print(f"pfc_eval.ports: {error}", file=sys.stderr)
            return 1
        rows = composed["entries"]
        assert isinstance(rows, list)
        print(f"{len(rows)} pairs; {sum(bool(e['b0_admitted']) for e in rows)} admissible composites written")
        return 0
    try:
        provenance = write_ports(
            Path(arguments.output),
            seed=arguments.seed,
            compiler=arguments.clang,
            upstream=Path(arguments.upstream) if arguments.upstream else None,
        )
    except (FileExistsError, ValueError, OSError) as error:
        print(f"pfc_eval.ports: {error}", file=sys.stderr)
        return 1
    entries = provenance["entries"]
    assert isinstance(entries, list)
    admitted = sum(bool(e["b0_admitted"]) for e in entries)
    print(
        f"{len(entries)} ports written; B0 admitted for {admitted}; outside the B1 family: "
        f"{sum(not e['b1_family'] for e in entries)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
