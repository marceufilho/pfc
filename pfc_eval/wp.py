"""Configurable WP campaign runner over the digest-pinned Frama-C image (roadmap E8, architecture §3.4 D8).

The fixed 14-function oracle in `tools/frama_oracle.py` stays untouched: it owns the baseline manifest and
a single ten-second budget. This runner reuses its reviewed goal parser and conditional assessment, reads
the same handwritten ACSL sources after verifying their recorded digests, and varies only the prover
budget (`-wp-timeout`) per run. Every container is fresh, offline, unprivileged and digest-pinned; nothing
imported from WP grants pfc proof authority.
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
WP_INPUT = ROOT / "validation" / "frama-c"
ORACLE_RUNNER = ROOT / "tools" / "frama_oracle.py"
GoalKind = str


_ORACLE_MODULE = "pfc_eval_frama_oracle"


def _oracle() -> ModuleType:
    """Load the frozen runner's parsers by path; `tools/` is not a package and must not become one here."""
    existing = sys.modules.get(_ORACLE_MODULE)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_ORACLE_MODULE, ORACLE_RUNNER)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load tools/frama_oracle.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ORACLE_MODULE] = module  # dataclasses resolve postponed annotations through sys.modules
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Case:
    name: str
    source: str
    sha256: str
    properties: tuple[str, ...]
    original: str | None


PROVERS = ("alt-ergo", "z3")  # WP prover names the derived image can offer; recorded when overridden
IMAGE_PATTERN = r"framac/frama-c@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64}"


@dataclass(frozen=True)
class Profile:
    image: str
    frama_c: str
    prover: str
    options: tuple[str, ...]  # the manifest options without -wp-timeout and its value
    smoke_timeout: str
    cases: tuple[Case, ...]
    baseline_image: str = ""
    baseline_prover: str = ""
    prover_override: str | None = None
    image_override: str | None = None


@dataclass(frozen=True)
class CaseOutcome:
    case: str
    budget_s: int
    returncode: int
    seconds: float
    rows: list[dict[str, object]]
    assessment: dict[str, object]


def wp_profile(
    manifest: Path = WP_INPUT / "manifest.json", *, prover: str | None = None, image: str | None = None
) -> Profile:
    """The positive cases and fixed options of the baseline manifest, with the budget made variable.

    `prover` and `image` override the baseline for an adjudication run (a derived image carrying Z3, pinned by
    its image id); both are recorded beside the baseline values so the campaign states what differs. The ACSL
    sources and every other option stay those of the sealed oracle.
    """
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not re.fullmatch(
        r"framac/frama-c@sha256:[0-9a-f]{64}", data.get("image", "")
    ):
        raise ValueError("oracle manifest must pin the image by digest")
    if image is not None and not re.fullmatch(IMAGE_PATTERN, image):
        raise ValueError("the image override must be pinned: a repo@sha256 digest or a sha256 image id")
    if prover is not None and prover not in PROVERS:
        raise ValueError(f"prover must be one of {PROVERS}")
    options = list(data["options"])
    if "-wp-timeout" not in options or "-wp-smoke-timeout" not in options or "-wp-prover" not in options:
        raise ValueError("manifest options must include -wp-timeout, -wp-smoke-timeout and -wp-prover")
    smoke = options[options.index("-wp-smoke-timeout") + 1]
    index = options.index("-wp-timeout")
    del options[index : index + 2]
    baseline_prover_option = options[options.index("-wp-prover") + 1]
    if prover is not None:
        options[options.index("-wp-prover") + 1] = prover
    cases = tuple(
        Case(c["name"], c["source"], c["sha256"], tuple(c.get("properties", ())), c.get("original"))
        for c in data["cases"]
    )
    overridden = prover is not None and prover != baseline_prover_option
    return Profile(
        image if image is not None else data["image"],
        data["frama_c"],
        f"{prover} (override)" if overridden else data["prover"],
        tuple(options),
        smoke,
        cases,
        baseline_image=data["image"],
        baseline_prover=data["prover"],
        prover_override=prover if overridden else None,
        image_override=image,
    )


def verify_case_source(case: Case, root: Path) -> Path:
    path = (root / case.source).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"source identity check failed for {case.name}: missing file")
    if hashlib.sha256(path.read_bytes()).hexdigest() != case.sha256:
        raise ValueError(f"source identity check failed for {case.name}: digest differs from the manifest")
    return path


def image_available(image: str, *, run: Callable[[list[str]], tuple[int, str]] | None = None) -> bool:
    """`docker image inspect` on the pinned reference; never pulls."""
    argv = ["docker", "image", "inspect", image, "--format", "{{.Id}}"]
    if run is not None:
        code, _ = run(argv)
        return code == 0
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def wp_outer_timeout(budget_s: int) -> int:
    """Process bound for one container: generous enough for every goal to exhaust the prover budget."""
    return 300 + 20 * budget_s


def wp_command(profile: Profile, case: Case, budget_s: int, output: Path, *, container: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--name",
        container,
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=2",
        "--memory=2g",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={WP_INPUT},dst=/input,readonly",
        "--mount",
        f"type=bind,src={output.resolve()},dst=/output",  # Docker requires absolute bind paths
        profile.image,
        "frama-c",
        "/input/" + case.source,
        *profile.options,
        "-wp-timeout",
        str(budget_s),
        "-wp-report-json",
        "/output/wp.json",
    ]


def _goal_kind(name: str, smoke: bool, expected: Sequence[str]) -> GoalKind:
    if smoke:
        return "smoke"
    if "_rte_signed_overflow" in name:
        return "overflow"
    if any(re.search(r"_assert_" + re.escape(label) + r"(?:_\d+)?$", name) for label in expected):
        return "property"
    if "_rte_" in name:
        return "rte"
    return "support"


def wp_rows(kernel: str, budget_s: int, payload: object, *, expected: Sequence[str]) -> list[dict[str, object]]:
    """One tidy row per WP goal, classified as smoke, property, rte, overflow or support."""
    goals = _oracle().parse_goals(payload)
    rows: list[dict[str, object]] = []
    for goal in goals:
        kind = _goal_kind(goal.name, goal.smoke, expected)
        rows.append(
            {
                "tool": "wp",
                "kernel": kernel,
                "budget_s": budget_s,
                "unit": "goal",
                "identifier": goal.name,
                "kind": kind,
                "category": kind,
                "status": goal.verdict,
                "solver_ms": None,
                "query_id": None,
                "smoke": goal.smoke,
            }
        )
    return rows


def wp_assessment(
    kernel: str, budget_s: int, payload: object, *, expected: Sequence[str], returncode: int
) -> dict[str, object]:
    oracle = _oracle()
    assessment: dict[str, object] = dict(
        oracle.assess(oracle.parse_goals(payload), tuple(expected), returncode=returncode)
    )
    assessment.update(kernel=kernel, budget_s=budget_s)
    return assessment


def run_wp_case(profile: Profile, case: Case, budget_s: int, directory: Path) -> CaseOutcome:
    """One fresh container for one case and budget; argv, streams and exit status are recorded."""
    verify_case_source(case, WP_INPUT)
    directory.mkdir(parents=True, exist_ok=True)
    container = "pfc-sweep-" + uuid.uuid4().hex
    argv = wp_command(profile, case, budget_s, directory, container=container)
    started = time.monotonic()
    with (
        (directory / "wp.stdout").open("x", encoding="utf-8") as out,
        (directory / "wp.stderr").open("x", encoding="utf-8") as err,
    ):
        try:
            code = subprocess.run(
                argv, stdout=out, stderr=err, timeout=wp_outer_timeout(budget_s), check=False
            ).returncode
        except subprocess.TimeoutExpired:
            err.write("Outer process timeout; no validity conclusion.\n")
            code = 124
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30, check=False)
    seconds = time.monotonic() - started
    (directory / "wp.command.json").write_text(
        json.dumps({"argv": argv, "exit_code": code, "seconds": seconds, "budget_s": budget_s}, indent=2) + "\n",
        encoding="utf-8",
    )
    report = directory / "wp.json"
    if not report.is_file():
        return CaseOutcome(case.name, budget_s, code, seconds, [], {"status": "error", "problem": "no WP report"})
    payload = json.loads(report.read_text(encoding="utf-8"))
    try:
        rows = wp_rows(case.name, budget_s, payload, expected=case.properties)
        assessment = wp_assessment(case.name, budget_s, payload, expected=case.properties, returncode=code)
    except (ValueError, TypeError) as error:
        return CaseOutcome(case.name, budget_s, code, seconds, [], {"status": "error", "problem": str(error)})
    return CaseOutcome(case.name, budget_s, code, seconds, rows, assessment)


def case_for(profile: Profile, kernel: str) -> Case | None:
    return next((c for c in profile.cases if c.name == kernel), None)


def describe(profile: Profile) -> Mapping[str, object]:
    return {
        "image": profile.image,
        "frama_c": profile.frama_c,
        "prover": profile.prover,
        "baseline_image": profile.baseline_image,
        "baseline_prover": profile.baseline_prover,
        "prover_override": profile.prover_override,
        "image_override": profile.image_override,
        "options": list(profile.options),
        "smoke_timeout_s": profile.smoke_timeout,
        "cases": [c.name for c in profile.cases],
    }
