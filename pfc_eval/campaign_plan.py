"""E14 campaign plan: every experiment once on the sealed held-out set, in the pre-registered configuration.

The plan is a fixed list of `python -m pfc_eval` commands with the pre-registered settings (seed 0, five
Houdini rounds, budgets 1–60 s, R = 5 proposer runs, the default runtime schedule) and dated output
directories. `render` prints it for review; `execute` runs it in order, once, refusing an unsealed set or an
existing output, and stops at the first failure so that no experiment is silently rerun. The user runs it at
`eval-v1` on the designated machine; nothing here was executed on held-out kernels in this clone.
"""

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pfc_eval import heldout

L3_BASE_URL = "http://localhost:8000/v1"  # the OpenAI-compatible local endpoint of design §5, recorded in evidence
ENV_FILE = "~/.config/pfc/openai.env"
PROFILES_DIR = (
    "pfc_eval/profiles-heldout"  # held-out generation profiles, sealed by digest (never the development ones)
)
PREFIX = ["uv", "run", "--locked", "python", "-m", "pfc_eval"]


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    output: str


def _step(name: str, arguments: Sequence[str], output: str, *, env_file: bool = False) -> Step:
    # The plan runs without a shell, so the tilde is expanded here; `uv run --env-file ~/...` exits 2 otherwise.
    expanded = str(Path(ENV_FILE).expanduser())
    prefix = ["uv", "run", "--locked", "--env-file", expanded, "python", "-m", "pfc_eval"] if env_file else PREFIX
    return Step(name, tuple(prefix + list(arguments) + ["--output", output]), output)


def plan(
    held_out: Path,
    *,
    date: str,
    acsl: Path | None,
    conditions: Sequence[str] = ("L1", "L2", "L3"),
    seed: int = 0,
    cvc5: Path | None = None,
) -> tuple[Step, ...]:
    """One step per experiment; the proposer gets one campaign per condition (design §5).

    Revision plan, phase 7: the differential step reads the sealed held-out profiles, B2 runs beside B1, and the
    advisory cvc5 cross-check is added when a binary is given (AMENDMENTS items 26, 28, 30).
    """
    target = str(held_out)
    suffix = f"heldout-{date}"
    steps = [
        _step(
            "differential",
            ["differential", target, "--seed", str(seed), "--profile", PROFILES_DIR],
            f"bench/eval/differential-{suffix}",
        ),
        _step("conjuncts", ["conjuncts", target], f"bench/eval/conjuncts-{suffix}"),
        _step("inject", ["inject", target, "--seed", str(seed)], f"bench/eval/inject-{suffix}"),
        _step("houdini", ["houdini", target], f"bench/eval/houdini-{suffix}"),
        _step(
            "houdini-b2",
            ["houdini", target, "--family", "b2", "--seed", str(seed), "--profile", PROFILES_DIR],
            f"bench/eval/houdini-b2-{suffix}",
        ),
        _step(
            "robustness",
            ["robustness", target, "--seed", str(seed), "--variants", "10"],
            f"bench/eval/robustness-{suffix}",
        ),
        _step(
            "sweep",
            ["sweep", target, *([str(acsl)] if acsl else []), "--budgets", "1,2,5,10,30,60", "--tools", "pfc,wp"],
            f"bench/eval/sweep-{suffix}",
        ),
    ]
    if cvc5 is not None:
        steps.append(
            _step("crosscheck", ["crosscheck", target, "--cvc5", str(cvc5)], f"bench/eval/crosscheck-{suffix}")
        )
    for condition in conditions:
        arguments = ["proposer", target, "--condition", condition, "--runs", "5", "--live", "--seed", str(seed)]
        if condition == "L3":
            arguments += ["--base-url", L3_BASE_URL]
        steps.append(
            _step(f"proposer-{condition}", arguments, f"bench/eval/proposer-{condition}-{suffix}", env_file=True)
        )
    steps.append(_step("runtime", ["runtime", target, "--seed", str(seed)], f"bench/eval/runtime-{suffix}"))
    return tuple(steps)


def render(steps: Sequence[Step]) -> str:
    lines = ["# E14 campaign plan (one command per experiment; run once at eval-v1)", ""]
    lines += [f"{step.name}: {' '.join(step.argv)}" for step in steps]
    return "\n".join(lines) + "\n"


def _sealed(root: Path, directory: Path) -> bool:
    candidate = directory if directory.is_absolute() else root / directory
    return heldout.sealed_root(candidate) is not None and heldout.verify(heldout.sealed_root(candidate) or candidate).ok


def execute(
    steps: Sequence[Step], *, root: Path, run: Callable[[list[str]], int] | None = None
) -> list[dict[str, object]]:
    """Run the steps in order from `root`; refuse an unsealed set or an existing output; stop at the first failure."""
    if not steps:
        raise ValueError("empty plan")
    held_out = Path(steps[0].argv[steps[0].argv.index("differential") + 1])
    if not _sealed(root, held_out):
        raise ValueError(f"{held_out} is not a verified sealed held-out set; seal and verify it first")
    for step in steps:
        if (root / step.output).exists():
            raise ValueError(f"{step.output} exists; a held-out experiment runs exactly once per condition")
    runner = run or (lambda argv: subprocess.run(argv, cwd=root, check=False).returncode)
    log: list[dict[str, object]] = []
    for step in steps:
        started = datetime.now(UTC).isoformat(timespec="seconds")
        code = runner(list(step.argv))
        log.append({**asdict(step), "started": started, "exit_code": code})
        if code != 0:
            break
    target = root / "bench" / "eval" / f"CAMPAIGN-{held_out.name}-{steps[0].output.rsplit('-', 1)[-1]}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    return log
