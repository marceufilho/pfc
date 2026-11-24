"""Shared scaffolding for every experiment: toolchain capture, provenance, sealing and CLI error mapping.

An experiment module supplies its schema, its settings and its rows; this module owns the common parts
so that provenance columns, inventory checks and exit codes are identical across experiments.
"""

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pfc import smt, vc
from pfc.config import CLANG_ENV_VAR, CPP_ENV_VAR, DEFAULT_CLANG, DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc.diagnostics import FrontendError
from pfc.environment import Environment, ToolchainError, collect_environment, resolve_compiler
from pfc.target import Target, read_target
from pfc_eval import evidence, heldout

ROOT = Path(__file__).resolve().parents[1]
INVENTORIES = ("protected", "baseline")


@dataclass(frozen=True)
class Run:
    evidence: evidence.Evidence
    target: Target
    toolchain: Environment
    compiler: str


def start(
    experiment: str,
    output: Path,
    *,
    compiler: str,
    cpp: str,
    seed: int | None,
    settings: Mapping[str, object],
    schema: evidence.Schema,
) -> Run:
    """Create the exclusive evidence directory with shared provenance, environment and inventory checks."""
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"evidence directory {output} exists; choose a new path")
    target = read_target(compiler)
    toolchain = collect_environment(compiler)
    identity = evidence.repository_identity(ROOT)
    inventories = [
        (name, evidence.verify_inventory(ROOT, ROOT / "validation" / "freeze" / f"{name}.sha256"))
        for name in INVENTORIES
    ]
    provenance = evidence.Provenance(
        experiment,
        identity.tag,
        identity.commit,
        seed,
        datetime.now(UTC).isoformat(timespec="seconds"),
        vc.MODEL,
        smt.solver_version(),
        toolchain.clang,
    )
    configuration = {
        "seed": seed,
        "compiler": resolve_compiler(compiler),
        "cpp": cpp,
        "smt_encoding": smt.ENCODING,
        **dict(settings),
    }
    run = evidence.create(
        output,
        provenance=provenance,
        configuration=configuration,
        environment=evidence.environment_record(asdict(toolchain), identity, inventories),
        schema=schema,
    )
    return Run(run, target, toolchain, compiler)


def finish(run: Run, summary: Mapping[str, object], table: str, derive: evidence.Deriver) -> None:
    """Write summary.json and tables.md, then derive the tidy table and seal."""
    run.evidence.write_text("summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    run.evidence.write_text("tables.md", table)
    run.evidence.complete(derive)


def progress(text: str) -> None:
    print(text, file=sys.stderr)


def run_command(namespace: Mapping[str, object], runner: Callable[..., bool]) -> int:
    """Argparse handler body shared by every experiment: defaults, keyword mapping and exit codes."""
    arguments: dict[str, object] = {
        "directories": [Path(str(d)) for d in _sequence(namespace.get("directories"))],
        "output": Path(str(namespace["output"])),
        "compiler": namespace.get("clang") or os.environ.get(CLANG_ENV_VAR, DEFAULT_CLANG),
        "cpp": namespace.get("cpp") or os.environ.get(CPP_ENV_VAR, DEFAULT_CPP),
        "timeout_ms": namespace.get("timeout_ms") if namespace.get("timeout_ms") is not None else SOLVER_TIMEOUT_MS,
        "dry_run": bool(namespace.get("dry_run", False)),
        "progress": progress,
    }
    if "seed" in namespace:
        arguments["seed"] = namespace["seed"]
    if namespace.get("profile"):
        arguments["profiles_dir"] = Path(str(namespace["profile"]))
    if namespace.get("reconcile"):
        arguments["reconcile"] = Path(str(namespace["reconcile"]))
    if namespace.get("per_file") is not None:
        arguments["per_file"] = namespace["per_file"]
    if namespace.get("harness_per_file") is not None:
        arguments["harness_per_file"] = namespace["harness_per_file"]
    if namespace.get("all"):
        arguments["select_all"] = True
    if namespace.get("cvc5"):
        arguments["cvc5"] = Path(str(namespace["cvc5"]))
    if namespace.get("logic"):
        arguments["logic"] = namespace["logic"]
    if namespace.get("max_rounds") is not None:
        arguments["max_rounds"] = namespace["max_rounds"]
    if namespace.get("trap_ablation"):
        arguments["trap_ablation"] = True
    if namespace.get("variants") is not None:
        arguments["count"] = namespace["variants"]
    for key in (
        "budgets",
        "tools",
        "condition",
        "runs",
        "base_url",
        "model",
        "temperature",
        "elements",
        "repetitions",
        "blocks",
        "warmups",
        "cpu",
        "targets",
        "family",
        "prover",
        "image",
    ):
        if namespace.get(key) is not None:
            arguments[key] = namespace[key]
    if "live" in namespace:
        arguments["live"] = bool(namespace["live"])
        arguments["replay"] = Path(str(namespace["replay"])) if namespace.get("replay") else None
    if namespace.get("reasoning_effort") is not None:
        effort = namespace["reasoning_effort"]
        arguments["effort"] = None if effort == "omit" else effort
    try:
        heldout.require_intact(arguments["directories"])  # type: ignore[arg-type]
        passed = runner(**arguments)
    except FileExistsError as error:
        print(f"pfc_eval: {error}", file=sys.stderr)
        return 1
    except (FrontendError, ToolchainError, OSError, ValueError) as error:
        print(f"pfc_eval: {error}", file=sys.stderr)
        return 1
    return 0 if passed else 1


def _sequence(value: object) -> Sequence[object]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return value
    raise ValueError("directories must be a list")
