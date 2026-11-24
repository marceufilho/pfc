"""Offline defense demonstration: real G0 and scripted, explicitly non-LLM proposals."""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pfc import ir, report, vc
from pfc import slice as gate
from pfc.build import require_new_directory
from pfc.config import DEFAULT_CPP, DIFF_SEED, SOLVER_TIMEOUT_MS
from pfc.digest import sha256_text
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.propose.invariants import synthesize
from pfc.propose.llm import Exchange, Request, Settings


@dataclass(frozen=True)
class DemoReport:
    passed: bool
    g0_passed: bool
    replay_passed: bool
    attempts: int
    accepted: int
    rejected: int
    dry_run: bool = False
    mode: Literal["scripted"] = "scripted"
    g2: Literal["not-run"] = "not-run"


@dataclass(frozen=True)
class Implementation:
    """Execution identity, not a certificate or release tag."""

    python_sources: tuple[tuple[str, str], ...]
    input_sha256: str
    model: str
    encoding: str


def _scripted(request: Request) -> Exchange:
    """Fixed teaching examples, never attributed to model generation or independent sampling."""
    first = request.sample_id.endswith("/1")
    expression = "0 <= i && i < n" if first else "0 <= i && i <= n"
    return Exchange(json.dumps({"invariants": [expression]}), request.settings.model, "scripted-demo", 0, 0, "test")


def _execute(source: Path, output: Path, *, compiler: str, cpp: str, seed: int, timeout_ms: int) -> DemoReport:
    program = lower(parse_source(source, cpp=cpp))
    evidence = gate.run(program, compiler=compiler, seed=seed, timeout_ms=timeout_ms)
    output.mkdir(parents=True, exist_ok=False)
    gate.save(evidence, output / "g0")
    target = evidence.differential.target
    # Typed explicit arguments keep the same request identity for the offline replay below.
    proposed = synthesize(
        program,
        target,
        settings=Settings(),
        sample_id="scripted-demo/run-1/sum",
        cache=output / "cache",
        log=output / "logs/proposals.jsonl",
        live=True,
        send=_scripted,
        timeout_ms=timeout_ms,
    )
    replayed = synthesize(
        program,
        target,
        settings=Settings(),
        sample_id="scripted-demo/run-1/sum",
        cache=output / "cache",
        log=output / "logs/replay.jsonl",
        live=False,
        timeout_ms=timeout_ms,
    )
    rejected = [a for a in proposed.attempts if a.admission and not a.admission.accepted]
    accepted = [a for a in proposed.attempts if a.admission and a.admission.accepted]
    witnessed = any(
        r.status == "failed" and r.counterexample is not None
        for a in rejected
        if a.admission and a.admission.proof
        for r in a.admission.proof.results
    )
    replay_passed = replayed.found and all(a.exchange and a.exchange.origin == "replay" for a in replayed.attempts)
    passed = evidence.passed and proposed.found and witnessed and replay_passed
    result = DemoReport(passed, evidence.passed, replay_passed, len(proposed.attempts), len(accepted), len(rejected))
    if accepted:
        admission = accepted[-1].admission
        assert admission and admission.program and admission.proof
        (output / "proposed.c").write_text(ir.print_c(admission.program), encoding="utf-8")
        generated = emit(admission.program, target, mode="verified", proof=admission.proof)
        (output / "proposed-verified.c").write_text(generated.c, encoding="utf-8")
    package = Path(__file__).parent
    report.write_json(
        output / "implementation.json",
        Implementation(
            tuple(
                (p.relative_to(package).as_posix(), sha256_text(p.read_text(encoding="utf-8")))
                for p in sorted(package.rglob("*.py"))
            ),
            sha256_text(source.read_text(encoding="utf-8")),
            vc.MODEL,
            evidence.breakit.original.proof.encoding,
        ),
    )
    report.write_json(output / "demo.json", result)
    (output / "README.txt").write_text(render(result), encoding="utf-8")
    original = evidence.breakit.original
    summary = report.Summary(
        "test",
        (
            report.row(
                "sum-scripted-demo",
                original.proof,
                emission=original.verified,
                passed=passed,
                compared=evidence.differential.compared,
                agreed=evidence.differential.agreed,
                breakit=evidence.breakit.passed,
            ),
        ),
        passed,
    )
    report.seal(output, summary)
    return result


def run(
    source: Path,
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = DIFF_SEED,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
) -> DemoReport:
    """Recheck everything; write only to a new output, or to disposable storage in dry-run."""
    require_new_directory(output, dry_run=dry_run)
    if dry_run:
        with TemporaryDirectory(prefix="pfc-demo-") as temporary:
            result = _execute(
                source, Path(temporary) / "demo", compiler=compiler, cpp=cpp, seed=seed, timeout_ms=timeout_ms
            )
        return replace(result, dry_run=True)
    return _execute(source, output, compiler=compiler, cpp=cpp, seed=seed, timeout_ms=timeout_ms)


def render(result: DemoReport) -> str:
    status = "passed" if result.passed else "failed"
    return (
        f"Demo: {status}; G0={result.g0_passed}; mode=scripted (human-supplied teaching candidates); "
        f"attempts={result.attempts}; rejected={result.rejected}; accepted={result.accepted}; "
        f"offline-replay={result.replay_passed}; G2: not-run.\n"
        "Inspect g0/, logs/proposals.jsonl and logs/replay.jsonl. "
        "No network or model sampling occurred. Stored verdicts are rechecked, never imported as proof.\n"
    )
