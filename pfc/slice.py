"""Execute and archive the sum G0 experiment, without conferring proof authority."""

import json
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pfc import breakit, difftest, ir, smt, vc
from pfc.config import C_FLAGS, DIFF_CASES, DIFF_SEED, SOLVER_TIMEOUT_MS
from pfc.digest import sha256_text
from pfc.emit import emit
from pfc.environment import resolve_compiler

NEGATIVE_DRIVER = """/* Fixed negative experiment, not a replay of an SMT model. */
#include <sys/resource.h>
long sum(const long[], long, long);
int main(void) {
    struct rlimit core_limit = {0, 0};
    if (setrlimit(RLIMIT_CORE, &core_limit) != 0) return 2;
    long a[2] = {7, 9};
    if (sum(a, 2, 2) != 16) return 3;
    return sum(a, 1, 2) != 16;
}
"""
Runner = Callable[[tuple[str, ...], Path], difftest.ProcessResult]


@dataclass(frozen=True)
class MutationReport:
    original_c: str
    mutant_c: str
    baseline_retained: bool
    mutant_detected: bool


@dataclass(frozen=True)
class TrapReport:
    name: str
    source_id: str
    compilation: difftest.ProcessResult | None
    execution: difftest.ProcessResult | None
    passed: bool
    reason: str


@dataclass(frozen=True)
class SliceReport:
    breakit: breakit.BreakReport
    differential: difftest.DiffReport
    mutation: MutationReport
    negative: tuple[TrapReport, ...]

    @property
    def passed(self) -> bool:
        """Aggregate only the complete in-process evidence; never a deserialized certificate."""
        diff = self.differential
        original = self.breakit.original
        negative_sources = {
            "original-checked": sha256_text(original.checked.c),
            "without-requires-verified": sha256_text(self.breakit.altered.verified.c),
        }
        return (
            self.breakit.passed
            and diff.status == "passed"
            and diff.cases == diff.compared == diff.agreed == DIFF_CASES
            and not diff.failures
            and diff.context_id == original.proof.context_id
            and diff.target == original.proof.target
            and diff.checked_c_id == sha256_text(original.checked.c)
            and diff.verified_c_id == sha256_text(original.verified.c)
            and self.mutation.baseline_retained
            and self.mutation.mutant_detected
            and len(self.negative) == 2
            and {r.name for r in self.negative} == {"original-checked", "without-requires-verified"}
            and all(r.passed and r.source_id == negative_sources[r.name] for r in self.negative)
        )


def probe_trap(name: str, code: str, *, compiler: str, runner: Runner = difftest.invoke) -> TrapReport:
    """Compile a guarded binary and require SIGILL on the supported Linux/x86 experiment.

    Never run an unchecked invalid call. Other signals, timeout, positive exits and
    compiler failures are failures, not evidence that the intended guard trapped.
    """
    identity = sha256_text(code)
    if not breakit.retains_sum_bounds(code):
        return TrapReport(name, identity, None, None, False, "expected guard missing; negative execution skipped")
    with TemporaryDirectory(prefix="pfc-breakit-") as temporary:
        directory = Path(temporary)
        source, driver, binary = (directory / filename for filename in ("kernel.c", "main.c", "negative"))
        source.write_text(code, encoding="utf-8")
        driver.write_text(NEGATIVE_DRIVER, encoding="utf-8")
        compiled = runner((compiler, *C_FLAGS, str(source), str(driver), "-o", str(binary)), directory)
        if compiled.status != "ok":
            return TrapReport(name, identity, compiled, None, False, "negative binary did not compile")
        result = runner((str(binary),), directory)
    passed = result.status == "error" and result.returncode == -signal.SIGILL
    return TrapReport(
        name, identity, compiled, result, passed, "SIGILL observed" if passed else "expected SIGILL absent"
    )


def run(
    program: ir.Program,
    *,
    compiler: str,
    seed: int = DIFF_SEED,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    runner: Runner = difftest.invoke,
) -> SliceReport:
    """Run original differential, ablation, independent deletion control and guarded negatives."""
    differential = difftest.run_sum(program, compiler=compiler, seed=seed, timeout_ms=timeout_ms, runner=runner)
    ablation = breakit.check_sum(program, differential.target, timeout_ms=timeout_ms)
    no_proof = emit(program, differential.target, mode="verified").c
    mutant = no_proof.replace(breakit.SUM_GUARD, "", 1)
    mutation = MutationReport(
        no_proof,
        mutant,
        breakit.retains_sum_bounds(no_proof),
        mutant != no_proof and not breakit.retains_sum_bounds(mutant),
    )
    executable = resolve_compiler(compiler)
    negative = tuple(
        probe_trap(name, code, compiler=executable, runner=runner)
        for name, code in (
            ("original-checked", ablation.original.checked.c),
            ("without-requires-verified", ablation.altered.verified.c),
        )
    )
    return SliceReport(ablation, differential, mutation, negative)


def summary(report: SliceReport) -> dict[str, object]:
    """Small CLI/manifest summary; complete evidence lives in separate artifacts."""
    return {
        "schema_version": 2,
        "vc_model": vc.MODEL,
        "smt_encoding": smt.ENCODING,
        "gate": "G0",
        "passed": report.passed,
        "breakit_passed": report.breakit.passed,
        "differential_status": report.differential.status,
        "compared": report.differential.compared,
        "agreed": report.differential.agreed,
        "seed": report.differential.seed,
        "baseline_retained": report.mutation.baseline_retained,
        "delete_mutant_detected": report.mutation.mutant_detected,
        "negative_passed": all(item.passed for item in report.negative) and len(report.negative) == 2,
    }


def _json(value: object) -> str:
    return json.dumps(ir.to_data(value), indent=2, sort_keys=True) + "\n"


def save(report: SliceReport, output: Path) -> None:
    """Create an exclusive artifact directory; write manifest last, never overwrite.

    A partial write leaves an incomplete directory without a final manifest. It is
    intentionally preserved for inspection, and cannot be silently reused.
    """
    files = {
        "original.json": _json(report.breakit.original),
        "without-requires.json": _json(report.breakit.altered),
        "removed-contract.json": _json(report.breakit.removed),
        "differential.json": _json(report.differential),
        "mutation.json": _json(report.mutation),
        "negative.json": _json(report.negative),
        "original.c": report.breakit.original.source,
        "without-requires.c": report.breakit.altered.source,
        "original-checked.c": report.breakit.original.checked.c,
        "original-verified.c": report.breakit.original.verified.c,
        "without-requires-checked.c": report.breakit.altered.checked.c,
        "without-requires-verified.c": report.breakit.altered.verified.c,
        "no-proof.c": report.mutation.original_c,
        "delete-mutant.c": report.mutation.mutant_c,
        "negative-driver.c": NEGATIVE_DRIVER,
    }
    manifest = {**summary(report), "files": {name: sha256_text(text) for name, text in files.items()}}
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    output.mkdir(parents=True, exist_ok=False)
    for name, text in (*files.items(), ("manifest.json", manifest_text)):
        with (output / name).open("x", encoding="utf-8") as stream:
            stream.write(text)


def render_report(report: SliceReport) -> str:
    """Report gate result without turning an expected SAT ablation into a proof claim."""
    status = "passed" if report.passed else "failed"
    return (
        f"G0: {status}; break-it={report.breakit.passed}; "
        f"differential={report.differential.agreed}/{report.differential.compared}; "
        f"delete-mutant-detected={report.mutation.mutant_detected}; "
        f"negative-traps={sum(r.passed for r in report.negative)}/2; seed={report.differential.seed}\n"
    )
