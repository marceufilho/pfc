"""The independent validator must fail closed on missing or misleading WP evidence."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from pycparser import c_ast, c_generator, c_parser

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def oracle():
    spec = importlib.util.spec_from_file_location("frama_oracle", ROOT / "tools/frama_oracle.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def goal(name="typed_f_assert_bounds_a", verdict="valid", smoke=False):
    return {
        "goal": name,
        "verdict": verdict,
        "smoke": smoke,
        "passed": (verdict != "valid") if smoke else verdict == "valid",
    }


@pytest.mark.parametrize("payload", [[], {}, [None], [goal(verdict="invented")], [goal(), goal()]])
def test_invalid_or_empty_wp_evidence_is_rejected(oracle, payload):
    with pytest.raises(ValueError):
        oracle.parse_goals(payload)


def test_smoke_valid_is_inconsistency_not_success(oracle):
    goals = oracle.parse_goals([goal(), goal("typed_f_smoke", smoke=True)])
    assert oracle.assess(goals, ("bounds_a",), returncode=0)["status"] == "inconsistent"


def test_unproved_smoke_is_inconclusive_about_consistency(oracle):
    goals = oracle.parse_goals([goal(), goal("typed_f_smoke", verdict="unknown", smoke=True)])
    result = oracle.assess(goals, ("bounds_a",), returncode=0)
    assert result["smoke"] == "no-inconsistency-detected"
    assert result["status"] == "conditional-agreement"


def test_missing_named_comparison_is_error(oracle):
    result = oracle.assess(oracle.parse_goals([goal()]), ("bounds_a", "division_b"), returncode=0)
    assert result["status"] == "error"


def test_overflow_is_separate_from_bounds_and_support(oracle):
    goals = oracle.parse_goals([goal(), goal("typed_f_assert_rte_signed_overflow", "timeout")])
    result = oracle.assess(goals, ("bounds_a",), returncode=0)
    assert result["status"] == "conditional-agreement"
    assert result["overflow"]["timeout"] == 1


@pytest.mark.parametrize("name", ["typed_f_loop_invariant_preserved", "typed_f_assert_rte_mem_access"])
def test_unproved_support_or_memory_is_not_agreement(oracle, name):
    goals = oracle.parse_goals([goal(), goal(name, "unknown")])
    assert oracle.assess(goals, ("bounds_a",), returncode=0)["status"] == "inconclusive"


def test_nonzero_tool_exit_is_error_even_with_valid_goals(oracle):
    assert oracle.assess(oracle.parse_goals([goal()]), ("bounds_a",), returncode=1)["status"] == "error"


KERNELS = ("sum", "dot", "saxpy", "prefix_sum", "matvec", "binary_search")
PORTS = ("accumulate", "copy", "count", "fill", "find", "replace", "reverse", "swap_ranges")


@pytest.mark.parametrize("name", KERNELS + PORTS)
def test_independent_acsl_preserves_the_original_c_function(name):
    base = ROOT / ("kernels" if name in KERNELS else "bench/external/acsl")
    paths = (base / name / "kernel.c", ROOT / "validation/frama-c/sources" / f"{name}.c")
    bodies = []
    for path in paths:
        source = subprocess.run(
            ["cpp", "-P", "-I", str(ROOT / "include"), str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        ast = c_parser.CParser().parse(source)
        functions = [item for item in ast.ext if isinstance(item, c_ast.FuncDef)]
        assert len(functions) == 1

        class RemoveEmpty(c_ast.NodeVisitor):
            def visit_Compound(self, node):
                node.block_items = [x for x in (node.block_items or []) if not isinstance(x, c_ast.EmptyStatement)]
                self.generic_visit(node)

        # Contract erasure leaves no-op semicolons; ignore only those empty statements.
        RemoveEmpty().visit(functions[0])
        bodies.append(c_generator.CGenerator().visit(functions[0]))
    assert bodies[0] == bodies[1]


def test_malformed_field_types_are_not_uncaught_type_errors(oracle):
    for key, value in (("verdict", []), ("smoke", 1), ("passed", "true")):
        item = goal()
        item[key] = value
        with pytest.raises(ValueError):
            oracle.parse_goals([item])


def test_complete_run_requires_memory_and_smoke_evidence(oracle):
    # Aggregate assessor can handle pure scalar controls, but a corpus run needs RTE and smoke coverage.
    with pytest.raises(ValueError):
        oracle.validate_positive(oracle.parse_goals([goal()]), ("bounds_a",), bounds=1, divisions=0)


def test_case_labels_match_expected_pfc_safety_inventory(oracle):
    with pytest.raises(ValueError):
        oracle.validate_positive(oracle.parse_goals([goal()]), ("bounds_a",), bounds=2, divisions=0)


def test_runner_dry_run_writes_nothing_and_calls_no_service(oracle, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("dry-run launched a process"))
    destination = tmp_path / "results"
    assert oracle.main(["--output", str(destination), "--dry-run"]) == 0
    assert not destination.exists()
    assert "14" in capsys.readouterr().out


def test_runner_refuses_existing_destination_before_running_tools(oracle, tmp_path, monkeypatch, capsys):
    marker = tmp_path / "keep"
    marker.write_text("unchanged")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("existing output launched a process"))
    assert oracle.main(["--output", str(tmp_path)]) == 2
    assert marker.read_text() == "unchanged"
    assert "existing" in capsys.readouterr().err
