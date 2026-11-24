"""Independent check-retention oracle; no execution of an unchecked invalid input."""

from pathlib import Path

import pytest

from pfc import emit as emission
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import Target

ROOT = Path(__file__).resolve().parents[1]


def require_retained_bounds(code):
    expected = "if (!(0 <= i && i < a_len)) __builtin_trap();"
    assert expected in code, "missing required bounds guard without proof"
    assert code.index(expected) < code.index("= a[i];"), "guard must precede the load"
    assert "// proved: obligation" not in code


def no_proof_output():
    program = lower(parse_source(ROOT / "kernels/sum/kernel.c"))
    return emission.emit(program, Target("test clang", "test-target", 32, 64), mode="verified").c


def test_no_proof_retention_independent_of_report_flags():
    require_retained_bounds(no_proof_output())


def test_delete_without_proof_mutant_is_detected(monkeypatch):
    original = emission._Emitter.guard

    def delete_without_proof(self, *args, **kwargs):
        start = len(self.lines)
        original(self, *args, **kwargs)
        del self.lines[start:]  # Deliberate bug: metadata still says retained, emitted guard is gone.

    monkeypatch.setattr(emission._Emitter, "guard", delete_without_proof)
    with pytest.raises(AssertionError, match="missing required bounds guard without proof"):
        require_retained_bounds(no_proof_output())
