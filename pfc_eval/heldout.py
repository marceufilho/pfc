"""Held-out corpus sealing and verification (roadmap E12, architecture D5, design §5 and §9).

`seal` runs once, after the held-out kernels were written by someone other than the developer: it checks
that every kernel is accepted by the frozen frontend (`pfc ir`), records the composition rule (at least
half of the hand invariants outside the B1 family, syntactically), embeds one random canary comment per
kernel, writes `canaries.json`, then hashes every file of the directory into `HELDOUT.sha256`. No source
change follows the hashing. `verify` recomputes the inventory and the canaries; every campaign that names a
directory under a sealed root calls it before any solver or service work and aborts on a mismatch.
"""

import hashlib
import json
import os
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pfc import ir
from pfc.config import CLANG_ENV_VAR, DEFAULT_CLANG
from pfc.diagnostics import FrontendError
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target, read_target
from pfc_eval import generate, ports

PROFILES_DIR = Path("pfc_eval") / "profiles-heldout"  # generation profiles of the held-out kernels (outside heldout/)
GUARD_RULE = 0.5  # at least this fraction of the kernels must carry an executable bounds or division guard site

NAMES = (
    "strided_sum",
    "compact",
    "partition",
    "argmax",
    "two_sum_sorted",
    "running_max",
    "transpose",
    "insertion_sort",
    "reverse",
    "count_if",
    "max_element",
    "pairwise_diff",
    "window_sum",
)
RESERVES = {"compact": "copy_if"}
INVENTORY = "HELDOUT.sha256"
CANARIES = "canaries.json"
SEALING = "SEALING.json"
CANARY_PREFIX = "pfc-heldout-"
UNTOUCHED_STATEMENT = (
    "No proposer, Houdini, oracle or differential run has read any file of this directory before sealing; "
    "only the frontend acceptance check, the checked emission that counts guard sites (no solver) and the "
    "profile binding recorded here preceded the hashing."
)
PRIOR_CHECKS_STATEMENT = (
    " The deterministic selection checks listed in prior_checks (recorded per kernel in PROVENANCE.json when the set "
    "was ported from an external benchmark) also preceded the hashing; none of them is a proposer, Houdini or "
    "differential run."
)
CompositionRule = Literal["required", "reported"]
_CANARY = re.compile(r"/\* (pfc-heldout-[0-9a-f]{32}) \*/")


@dataclass(frozen=True)
class Verification:
    ok: bool
    problems: tuple[str, ...]
    checked: int


def canary(*, seed_bytes: bytes | None = None) -> str:
    """A unique random identifier for one kernel; recorded in canaries.json for future contamination checks."""
    token = seed_bytes.hex() if seed_bytes is not None else secrets.token_hex(16)
    return CANARY_PREFIX + token[:32]


def find_canary(text: str) -> str | None:
    match = _CANARY.search(text)
    return match.group(1) if match else None


def embed(text: str, identifier: str) -> str:
    """Insert the canary comment right after the `#include "spec.h"` line; a kernel carries exactly one."""
    if find_canary(text) is not None:
        raise ValueError("kernel already carries a canary")
    lines = text.split("\n")
    try:
        index = next(i for i, line in enumerate(lines) if line.strip() == '#include "spec.h"')
    except StopIteration:
        raise ValueError('kernel must include "spec.h" on its own line') from None
    lines.insert(index + 1, f"/* {identifier} */")
    return "\n".join(lines)


def _kernels(root: Path) -> list[Path]:
    kernels = sorted(root.glob("*/kernel.c"))
    if not kernels:
        raise ValueError(f"{root} has no <name>/kernel.c")
    return kernels


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != INVENTORY and "__pycache__" not in p.parts)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def guard_sites(program: ir.Program, target: Target) -> dict[str, object]:
    """Executable bounds/division guard sites of a program from its checked emission (no solver is invoked)."""
    decisions = emit(program, target, mode="checked").decisions
    return {
        "bounds": sum(d.kind == "bounds" for d in decisions),
        "division": sum(d.kind == "division" for d in decisions),
        "sites": [f"{d.kind}@{d.location.file}:{d.location.line}:{d.location.column}" for d in decisions],
    }


def _array_kernel(program: ir.Program) -> bool:
    return any(p.length is not None for p in program.functions[0].parameters)


def profile_skeleton(program: ir.Program, name: str) -> dict[str, object]:
    """A valid `pfc-eval-inputs-v1` profile the kernel author edits: wide ranges, no boundary cases yet.

    Length parameters are derived by the generator; other scalars get a small range; arrays get short lengths and
    small values. `program_sha256` binds the profile to this program text (comments do not enter the digest).
    """
    function = program.functions[0]
    lengths = {p.length.name for p in function.parameters if p.length is not None}
    arrays = {
        p.symbol.name: {"length": [1, 64], "values": [-1000, 1000], "sorted": False}
        for p in function.parameters
        if p.length is not None
    }
    scalars: dict[str, object] = {}
    for parameter in function.parameters:
        symbol = parameter.symbol
        if parameter.length is not None or symbol.name in lengths:
            continue
        if symbol.type is ir.ScalarType.BOOL:
            scalars[symbol.name] = {"kind": "range", "low": 0, "high": 1}
        else:
            scalars[symbol.name] = {"kind": "range", "low": 0, "high": 64}
    return {
        "profile": generate.PROFILE_VERSION,
        "kernel": name,
        "function": function.name,
        "program_sha256": generate.program_digest(program),
        "cases": 1000,
        "arrays": arrays,
        "scalars": scalars,
        "boundary": [],
        "stress": 0.1,
    }


def _bound_profile(name: str, program: ir.Program, profiles_dir: Path) -> str:
    path = profiles_dir / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"{name}: array kernel without a generation profile at {path}")
    try:
        generate.bind(generate.load_profile(path), program)
    except ValueError as error:
        raise ValueError(f"{name}: profile {path} does not bind to the kernel: {error}") from None
    return _digest(path)


def seal(
    root: Path,
    *,
    untouched: bool,
    cpp: str = "cpp",
    compiler: str | None = None,
    profiles_dir: Path = PROFILES_DIR,
    composition: CompositionRule = "required",
    prior_checks: Sequence[str] = (),
) -> dict[str, object]:
    """Accept, count guard sites, bind profiles, check composition, embed canaries, record, hash; sealed once.

    Beyond ADR-0045: every array kernel needs a bound generation profile (outside this directory, digests
    recorded) and a host driver `main.c`, and at least half of the kernels must carry an executable guard site,
    so that the sealed study measures check elimination and not only assertion proving (revision plan, phase 7).
    `composition="reported"` records the B1-family fraction without enforcing it (AMENDMENTS 34: a set selected
    mechanically from an external benchmark cannot be tuned to the rule without biasing the selection), and
    `prior_checks` names the deterministic selection checks that preceded sealing (frontend acceptance, B0 admission).
    """
    if composition not in ("required", "reported"):
        raise ValueError("composition must be 'required' or 'reported'")
    if (root / INVENTORY).exists() or (root / CANARIES).exists():
        raise FileExistsError(f"{root} is already sealed; a held-out set is sealed once")
    if not untouched:
        raise ValueError("sealing requires the explicit --untouched statement that no run read the set before")
    target = read_target(compiler or os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG))
    kernels = _kernels(root)
    accepted: list[str] = []
    outside: list[str] = []
    invariants: dict[str, list[str]] = {}
    sites: dict[str, object] = {}
    profiles: dict[str, str] = {}
    drivers: list[str] = []
    array_kernels: list[str] = []
    for kernel in kernels:
        name = kernel.parent.name
        if find_canary(kernel.read_text(encoding="utf-8")) is not None:
            raise ValueError(f"{name} already carries a canary; sealing embeds them itself")
        try:
            program = lower(parse_source(kernel, cpp=cpp))
        except FrontendError as error:
            raise ValueError(f"{name} failed the pfc ir acceptance check: {error}") from None
        accepted.append(name)
        sites[name] = guard_sites(program, target)
        if (kernel.parent / "main.c").is_file():
            drivers.append(name)
        if _array_kernel(program):
            array_kernels.append(name)
            if name not in drivers:
                raise ValueError(
                    f"{name}: array kernel without a host driver main.c (needed by differential and runtime)"
                )
            profiles[name] = _bound_profile(name, program, profiles_dir)
        invariants[name] = ports.hand_invariants(program)
        if invariants[name] and all(not ports.in_b1_family(text) for text in invariants[name]):
            outside.append(name)
        elif any(not ports.in_b1_family(text) for text in invariants[name]):
            outside.append(name)
    with_loops = [name for name in accepted if invariants[name]]
    fraction = len(outside) / len(with_loops) if with_loops else 0.0
    if composition == "required" and fraction < 0.5:
        raise ValueError(
            f"composition rule failed: {len(outside)}/{len(with_loops)} kernels have a hand invariant outside the "
            "B1 family; at least half are required"
        )
    guarded = [name for name in accepted if sites[name]["sites"]]  # type: ignore[index]
    guard_fraction = len(guarded) / len(accepted) if accepted else 0.0
    if guard_fraction < GUARD_RULE:
        raise ValueError(
            f"guard rule failed: {len(guarded)}/{len(accepted)} kernels carry an executable bounds or division "
            "guard site; at least half are required so the study measures check elimination"
        )
    canaries = {kernel.parent.name: canary() for kernel in kernels}
    for kernel in kernels:
        text = kernel.read_text(encoding="utf-8")
        kernel.write_text(embed(text, canaries[kernel.parent.name]), encoding="utf-8")
    (root / CANARIES).write_text(json.dumps(canaries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    present = set(accepted)
    record: dict[str, object] = {
        "schema_version": 1,
        "sealed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "accepted": accepted,
        "fixed_names": list(NAMES),
        "reserves": RESERVES,
        "missing_names": [name for name in NAMES if name not in present and RESERVES.get(name) not in present],
        "extra_names": [name for name in accepted if name not in NAMES and name not in RESERVES.values()],
        "hand_invariants": invariants,
        "outside_b1": outside,
        "b1_fraction_outside": fraction,
        "composition_rule": composition,
        "prior_checks": list(prior_checks),
        "guard_sites": sites,
        "guard_rule_fraction": guard_fraction,
        "array_kernels": array_kernels,
        "drivers": drivers,
        "profiles": profiles,
        "profiles_dir": str(profiles_dir),
        "target": {
            "compiler": target.compiler,
            "triple": target.triple,
            "int_bits": target.int_bits,
            "long_bits": target.long_bits,
        },
        "untouched_statement": UNTOUCHED_STATEMENT + (PRIOR_CHECKS_STATEMENT if prior_checks else ""),
        "acceptance_check": (
            "lower(parse_source(kernel.c)) for every kernel, checked emission for guard sites (no solver), profile "
            "binding for array kernels, before canaries and hashing"
        ),
    }
    (root / SEALING).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [f"{_digest(path)}  {path.relative_to(root).as_posix()}" for path in _files(root)]
    (root / INVENTORY).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return record


def verify(root: Path) -> Verification:
    """Recompute HELDOUT.sha256 and check canaries; any difference, extra file or missing file is a problem."""
    inventory = root / INVENTORY
    if not inventory.is_file():
        return Verification(False, (f"{root} is not sealed ({INVENTORY} missing)",), 0)
    recorded: dict[str, str] = {}
    for line in inventory.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, _, name = line.partition("  ")
            recorded[name] = digest
    problems: list[str] = []
    actual = {path.relative_to(root).as_posix(): _digest(path) for path in _files(root)}
    for name, digest in recorded.items():
        if name not in actual:
            problems.append(f"{name}: missing")
        elif actual[name] != digest:
            problems.append(f"{name}: digest differs")
    problems.extend(f"{name}: not in the inventory" for name in sorted(set(actual) - set(recorded)))
    try:
        canaries = json.loads((root / CANARIES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        canaries = None
    if not isinstance(canaries, dict):
        problems.append(f"{CANARIES}: unreadable")
    else:
        for kernel in sorted(root.glob("*/kernel.c")):
            name = kernel.parent.name
            found = find_canary(kernel.read_text(encoding="utf-8"))
            if found is None or canaries.get(name) != found:
                problems.append(f"{name}/kernel.c: canary missing or different from {CANARIES}")
    problems.extend(_profile_problems(root))
    return Verification(not problems, tuple(problems), len(recorded))


def _profile_problems(root: Path) -> list[str]:
    """The profiles recorded at sealing live outside the directory; their digests are part of the identity."""
    try:
        record = json.loads((root / SEALING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []  # a set sealed before profiles existed (ADR-0045) records none; nothing to check
    profiles = record.get("profiles") if isinstance(record, dict) else None
    directory = record.get("profiles_dir") if isinstance(record, dict) else None
    if not isinstance(profiles, dict) or not isinstance(directory, str):
        return []
    problems = []
    for name, digest in profiles.items():
        path = Path(directory) / f"{name}.json"
        if not path.is_file():
            problems.append(f"{path}: recorded profile missing")
        elif _digest(path) != digest:
            problems.append(f"{path}: profile digest differs from {SEALING}")
    return problems


def sealed_root(directory: Path) -> Path | None:
    """The nearest ancestor (or the directory itself) holding HELDOUT.sha256, if any."""
    current = directory.resolve()
    for candidate in (current, *current.parents):
        if (candidate / INVENTORY).is_file():
            return candidate
    return None


def require_intact(directories: Sequence[Path]) -> None:
    """Abort before any solver or service call when a named directory lies under a tampered sealed set."""
    for directory in directories:
        root = sealed_root(Path(directory))
        if root is None:
            continue
        result = verify(root)
        if not result.ok:
            raise ValueError(f"held-out set {root} failed verification: {'; '.join(result.problems[:5])}")


def skeletons(root: Path, *, cpp: str = "cpp") -> dict[str, dict[str, object]]:
    """Profile skeletons for the array kernels of a directory (reads kernels only; writes nothing, no solver)."""
    found: dict[str, dict[str, object]] = {}
    for kernel in _kernels(root):
        program = lower(parse_source(kernel, cpp=cpp))
        if _array_kernel(program):
            found[kernel.parent.name] = profile_skeleton(program, kernel.parent.name)
    return found


def _skeleton_command(root: Path, namespace: Mapping[str, object]) -> int:
    try:
        found = skeletons(root, cpp=str(namespace.get("cpp") or "cpp"))
    except FrontendError as error:
        print(f"pfc_eval: {error}")
        return 1
    target = Path(str(namespace["profiles"])) if namespace.get("profiles") else None
    if target is None:
        for name, skeleton in found.items():
            print(f"# {name}")
            print(json.dumps(skeleton, indent=2))
        return 0
    existing = [name for name in found if (target / f"{name}.json").exists()]
    if existing:
        print(f"pfc_eval: refusing to overwrite existing profile(s): {', '.join(existing)}")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    for name, skeleton in found.items():
        (target / f"{name}.json").write_text(json.dumps(skeleton, indent=2) + "\n", encoding="utf-8")
    print(f"{len(found)} profile skeleton(s) written to {target}; edit ranges and boundary cases before sealing.")
    return 0


def command(arguments: object) -> int:
    namespace = vars(arguments)
    root = Path(str(namespace["directory"]))
    if namespace["action"] == "skeleton":
        return _skeleton_command(root, namespace)
    if namespace["action"] == "verify":
        result = verify(root)
        if result.ok:
            print(f"{root}: {result.checked} file(s) verified; canaries present.")
            return 0
        for problem in result.problems:
            print(f"pfc_eval: {problem}")
        return 1
    try:
        record = seal(
            root,
            untouched=bool(namespace.get("untouched")),
            cpp=str(namespace.get("cpp") or "cpp"),
            compiler=namespace.get("clang"),
            profiles_dir=Path(str(namespace["profiles"])) if namespace.get("profiles") else PROFILES_DIR,
            composition="reported" if namespace.get("composition") == "reported" else "required",
            prior_checks=tuple(str(c) for c in (namespace.get("prior_check") or ())),
        )
    except (FileExistsError, ValueError, OSError, FrontendError) as error:
        print(f"pfc_eval: {error}")
        return 1
    accepted = record["accepted"]
    outside = record["outside_b1"]
    invariants = record["hand_invariants"]
    missing = record["missing_names"]
    if not (
        isinstance(accepted, list)
        and isinstance(outside, list)
        and isinstance(invariants, dict)
        and isinstance(missing, list)
    ):
        raise AssertionError("sealing record shape")
    with_loops = sum(bool(v) for v in invariants.values())
    print(
        f"{root}: {len(accepted)} kernel(s) sealed; outside the B1 family: {len(outside)}/{with_loops} "
        f"(composition rule {record['composition_rule']})."
    )
    if missing:
        print(f"fixed names without a kernel or reserve: {', '.join(str(m) for m in missing)}")
    return 0
