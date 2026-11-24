"""Mutate isolated compiler copies; require assertion failures, never collection errors."""

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MUTANTS = [
    ("emit.py", 'self.line(f"if (!({condition})) __builtin_trap();")', 'self.line("/* missing check */")'),
    (
        "wp.py",
        "tuple(resolve(item.id) for item in self.obligations)",
        'tuple(resolve(item.id) for item in self.obligations if item.kind != "preservation")',
    ),
    (
        "wp.py",
        'op("<", index_value, state.lengths[expression.array.id])',
        'op("<=", index_value, state.lengths[expression.array.id])',
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "filename,original,mutated", MUTANTS, ids=["delete-without-proof", "missing-preservation", "off-by-one"]
)
def test_real_code_mutant_is_killed(filename, original, mutated, tmp_path):
    for directory in ("pfc", "kernels", "include", "tools"):
        shutil.copytree(ROOT / directory, tmp_path / directory, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "tests").mkdir()
    for name in ("test_vc.py", "test_general_emit.py"):
        shutil.copyfile(ROOT / "tests" / name, tmp_path / "tests" / name)
    shutil.copytree(ROOT / "tests/golden", tmp_path / "tests/golden")
    path = tmp_path / "pfc" / filename
    source = path.read_text()
    assert source.count(original) == 1
    path.write_text(source.replace(original, mutated))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--tb=no", "--junitxml=result.xml"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    suites = ET.parse(tmp_path / "result.xml").getroot().findall("testsuite")
    assert sum(int(s.attrib["failures"]) for s in suites) > 0
    assert all(int(s.attrib["errors"]) == 0 for s in suites)
