"""EV-R1: no frozen module imports the evaluation harness."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "pfc"
HARNESS = ROOT / "pfc_eval"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_frozen_modules_never_import_the_harness():
    frozen = sorted(PACKAGE.rglob("*.py"))
    assert len(frozen) >= 30
    offenders = {str(p) for p in frozen if any(n == "pfc_eval" or n.startswith("pfc_eval.") for n in _imports(p))}
    assert offenders == set()


def test_harness_is_a_separate_root_package_outside_pfc():
    assert (HARNESS / "__init__.py").is_file() and (HARNESS / "__main__.py").is_file()
    assert not (PACKAGE / "eval").exists()


def test_import_walker_detects_an_offending_module(tmp_path):
    offender = tmp_path / "module.py"
    offender.write_text("from pfc_eval import evidence\n", encoding="utf-8")
    assert "pfc_eval.evidence" in _imports(offender)
