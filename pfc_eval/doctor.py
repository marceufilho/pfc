"""`doctor`: preflight of the designated machine before the E14 campaign (post-E14 improvement, ADR-0046).

The campaign plan discovers a missing prerequisite one failure at a time; this command checks them all at
once and writes nothing. Every probe is injected so the checks are pure functions: governor, instruction
counter, taskset, Docker and the digest-pinned Frama-C image, presence (never the value) of the API key, the
freeze and evaluation tags, both freeze inventories, the sealed held-out set and the solver version.
"""

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pfc.config import OPENAI_KEY_ENV
from pfc_eval import evidence, heldout, runtime, wp

ROOT = Path(__file__).resolve().parents[1]
INVENTORIES = {"protected": "validation/freeze/protected.sha256", "baseline": "validation/freeze/baseline.sha256"}
REQUIRED_TAGS = ("freeze-v4", "eval-v1")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _tags() -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "tag", "--list"], cwd=ROOT, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _inventories() -> dict[str, bool]:
    return {name: evidence.verify_inventory(ROOT, ROOT / path).verified for name, path in INVENTORIES.items()}


def _heldout(path: Path) -> tuple[bool, str]:
    root = heldout.sealed_root(path if path.is_absolute() else Path.cwd() / path)
    if root is None:
        return False, f"{path} is not inside a sealed held-out set (HELDOUT.sha256 missing)"
    verification = heldout.verify(root)
    return verification.ok, "sealed and verified" if verification.ok else "; ".join(verification.problems)


def _sealing(path: Path) -> Mapping[str, object] | None:
    root = heldout.sealed_root(path if path.is_absolute() else Path.cwd() / path)
    if root is None:
        return None
    try:
        data = json.loads((root / heldout.SEALING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _names(value: object) -> list[str]:
    return [str(k) for k in value] if isinstance(value, list) else []


def _heldout_rows(record: Mapping[str, object] | None) -> list[Check]:
    """Profiles, drivers and guard sites recorded at sealing (revision plan, phase 7)."""
    if record is None:
        return [
            Check("held-out profiles", False, "no SEALING.json"),
            Check("held-out drivers", False, "no SEALING.json"),
            Check("held-out guard sites", False, "no SEALING.json"),
        ]
    accepted = _names(record.get("accepted"))
    arrays = _names(record.get("array_kernels"))
    profiles_value = record.get("profiles")
    profiles: set[str] = set(profiles_value) if isinstance(profiles_value, dict) else set()
    drivers = set(_names(record.get("drivers")))
    sites_value = record.get("guard_sites")
    sites: dict[str, object] = dict(sites_value) if isinstance(sites_value, dict) else {}
    guarded = 0
    for kernel in accepted:
        entry = sites.get(kernel)
        if isinstance(entry, dict) and entry.get("sites"):
            guarded += 1
    fraction = float(str(record.get("guard_rule_fraction", 0.0)))
    return [
        Check(
            "held-out profiles",
            all(k in profiles for k in arrays),
            f"{sum(k in profiles for k in arrays)}/{len(arrays)} array kernels have a bound profile",
        ),
        Check(
            "held-out drivers",
            all(k in drivers for k in accepted),
            f"{sum(k in drivers for k in accepted)}/{len(accepted)} kernels have main.c",
        ),
        Check(
            "held-out guard sites",
            fraction >= heldout.GUARD_RULE,
            f"{guarded}/{len(accepted)} kernels carry an executable guard site (at least half required)",
        ),
    ]


def _solver() -> str:
    try:
        import z3

        return str(z3.get_version_string())
    except ImportError:  # pragma: no cover - z3 is a locked dependency
        return "unavailable"


def _image_reference() -> str:
    try:
        return wp.wp_profile().image
    except (OSError, ValueError, KeyError) as problem:
        return f"unreadable manifest: {problem}"


def checks(
    held_out: Path,
    *,
    governor: Callable[[], str] = runtime.governor,
    which: Callable[[str], str | None] = shutil.which,
    image: Callable[[str], bool] = wp.image_available,
    environment: Mapping[str, str] = os.environ,
    tags: Callable[[], Sequence[str]] = _tags,
    inventories: Callable[[], Mapping[str, bool]] = _inventories,
    heldout: Callable[[Path], tuple[bool, str]] = _heldout,
    solver: Callable[[], str] = _solver,
    sealing: Callable[[Path], Mapping[str, object] | None] = _sealing,
) -> list[Check]:
    """Every prerequisite of the E14 plan as one row; the key's value never enters a detail."""
    current = governor()
    counter = runtime.counter_tool(which=which)
    reference = _image_reference()
    present = tuple(tags())
    verified = inventories()
    sealed, reason = heldout(held_out)
    report = [
        Check("governor", current == "performance", f"{current} (performance required for a controlled label)"),
        Check("instruction counter", counter != "unavailable", f"{counter} (perf or valgrind required)"),
        Check("taskset", which("taskset") is not None, "needed by --cpu pinning"),
        Check("docker", which("docker") is not None, "needed by the WP oracle layer"),
        Check("frama-c image", image(reference), f"{reference} present locally (never pulled by the harness)"),
        Check(
            OPENAI_KEY_ENV,
            bool(environment.get(OPENAI_KEY_ENV)),
            f"{OPENAI_KEY_ENV}: {'set' if environment.get(OPENAI_KEY_ENV) else 'unset'}",
        ),
    ]
    report += [Check(f"tag {tag}", tag in present, "present" if tag in present else "absent") for tag in REQUIRED_TAGS]
    report += [
        Check(
            f"{name} inventory", bool(verified.get(name)), "verified" if verified.get(name) else "drifted or unreadable"
        )
        for name in INVENTORIES
    ]
    report.append(Check("held-out set", sealed, reason))
    report.extend(_heldout_rows(sealing(held_out)))
    report.append(Check("solver", True, f"z3 {solver()}"))
    cvc5 = environment.get("PFC_EVAL_CVC5") or which("cvc5")
    report.append(Check("cvc5 (optional)", True, f"{cvc5 or 'absent'} (second-solver cross-check is advisory)"))
    derived = _derived_image_id()
    report.append(
        Check(
            "frama-c-z3 image (optional)",
            True,
            f"{derived} {'present' if derived and image(derived) else 'absent'} (WP adjudication with Z3)",
        )
    )
    return report


def _derived_image_id() -> str:
    """Image id recorded by the user in validation/frama-c-z3/manifest.json after building the derived image."""
    try:
        data = json.loads((ROOT / "validation" / "frama-c-z3" / "manifest.json").read_text(encoding="utf-8"))
        return str(data.get("image_id") or "")
    except (OSError, ValueError):
        return ""


def render(report: Sequence[Check]) -> str:
    lines = ["# E14 preflight", ""]
    lines += [f"[{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}" for check in report]
    failed = sum(not check.ok for check in report)
    lines += ["", f"{len(report) - failed} ok, {failed} failing; the plan runs only when everything is ok."]
    return "\n".join(lines) + "\n"


def as_json(report: Sequence[Check]) -> list[dict[str, object]]:
    return [asdict(check) for check in report]


def exit_code(report: Sequence[Check]) -> int:
    return 0 if all(check.ok for check in report) else 1


def command(arguments: object) -> int:
    namespace = vars(arguments)
    report = checks(Path(str(namespace.get("heldout") or "heldout")))
    print(json.dumps(as_json(report), indent=2) if namespace.get("json") else render(report), end="")
    return exit_code(report)
