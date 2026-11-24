"""Mutation runner: fixed operator family, seeded selection, minimal diffs, layer policy and score (roadmap E9)."""

import ast
import json
from pathlib import Path

import pytest

from pfc_eval import evidence, mutate

ROOT = Path(__file__).resolve().parents[2]

SNIPPET = """\
def guard(index, length, items):
    if index < length:
        total = items[index] + 1
        total -= 2
        return total
    raise ValueError("out of range")


def ratio(a, b):
    return a - b
"""


def test_operator_family_is_fixed_and_committed():
    assert mutate.OPERATORS == (
        "relational_boundary",
        "constant_shift",
        "negated_condition",
        "deleted_statement",
        "swapped_operands",
    )
    assert mutate.COMPILER_TARGETS == ("pfc/vc.py", "pfc/wp.py", "pfc/authorization.py", "pfc/emit.py")
    assert mutate.HARNESS_TARGETS == (
        "pfc_eval/interpret.py",
        "pfc_eval/stats.py",
        "pfc_eval/proposer.py",
        "pfc_eval/tables.py",
    )
    unit = mutate.HARNESS_LAYERS[0].files
    assert "tests/eval/test_eval_proposer.py" in unit
    assert mutate.LINK_PATHS == ("heldout",)
    assert 30 <= mutate.MUTANTS_PER_FILE * len(mutate.COMPILER_TARGETS) <= 50


def test_sites_cover_every_operator_and_each_applied_mutant_compiles_and_differs():
    sites = mutate.sites(SNIPPET)
    by_operator = {operator: [s for s in sites if s.operator == operator] for operator in mutate.OPERATORS}
    assert all(by_operator[operator] for operator in mutate.OPERATORS), {k: len(v) for k, v in by_operator.items()}
    relational = by_operator["relational_boundary"][0]
    assert (
        relational.line == 2 and relational.original == "index < length" and relational.replacement == "index <= length"
    )
    constants = {(s.original, s.replacement) for s in by_operator["constant_shift"]}
    assert {("1", "2"), ("1", "0"), ("2", "3"), ("2", "1")} <= constants
    negated = by_operator["negated_condition"][0]
    assert negated.replacement == "not (index < length)"
    deleted = {s.original for s in by_operator["deleted_statement"]}
    assert "total -= 2" in deleted and 'raise ValueError("out of range")' in deleted
    assert all(s.replacement == "pass" for s in by_operator["deleted_statement"])
    swapped = {s.original: s.replacement for s in by_operator["swapped_operands"]}
    assert swapped["a - b"] == "b - a" and swapped["index < length"] == "length < index"
    for site in sites:
        mutated = mutate.apply(SNIPPET, site)
        assert mutated != SNIPPET
        ast.parse(mutated)  # every mutant is syntactically valid Python


def test_deleting_a_lone_body_statement_or_a_definition_is_not_a_site():
    source = "def f(x):\n    return x\n\n\nLIMIT = 3\n"
    sites = mutate.sites(source)
    assert not [s for s in sites if s.operator == "deleted_statement"]
    source = "def f(x):\n    y = x\n    return y\n"
    deleted = [s for s in mutate.sites(source) if s.operator == "deleted_statement"]
    assert [s.original for s in deleted] == ["y = x"]  # return statements are kept
    source = 'def f(x):\n    """Docstring."""\n    y: int\n    y = x\n    return y\n'
    deleted = [s for s in mutate.sites(source) if s.operator == "deleted_statement"]
    assert [s.original for s in deleted] == ["y = x"]  # docstrings and bare annotations are equivalent mutants


def test_selection_is_seeded_distinct_and_bounded():
    first = mutate.select(SNIPPET, seed=0, count=4)
    again = mutate.select(SNIPPET, seed=0, count=4)
    other = mutate.select(SNIPPET, seed=1, count=4)
    assert first == again and len(first) == 4
    assert first != other
    texts = {mutate.apply(SNIPPET, s) for s in first}
    assert len(texts) == 4  # distinct mutated programs, never the same mutant twice
    assert len(mutate.select(SNIPPET, seed=0, count=1000)) == len(set(mutate.sites(SNIPPET)))
    with pytest.raises(ValueError):
        mutate.select(SNIPPET, seed=0, count=0)


def test_diff_is_a_minimal_unified_diff_of_the_target():
    site = next(s for s in mutate.sites(SNIPPET) if s.operator == "relational_boundary")
    text = mutate.diff("pfc/wp.py", SNIPPET, mutate.apply(SNIPPET, site))
    lines = text.splitlines()
    assert lines[0] == "--- a/pfc/wp.py" and lines[1] == "+++ b/pfc/wp.py"
    assert [line for line in lines if line.startswith("-") and not line.startswith("---")] == [
        "-    if index < length:"
    ]
    assert [line for line in lines if line.startswith("+") and not line.startswith("+++")] == [
        "+    if index <= length:"
    ]


def test_generated_mutants_carry_identity_operator_location_and_diff(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text(SNIPPET, encoding="utf-8")
    mutants = mutate.generate(root, ("pkg/mod.py",), seed=3, per_file=5)
    assert len(mutants) == 5 and [m.identifier for m in mutants] == [f"mod-{i:02d}" for i in range(1, 6)]
    assert all(m.target == "pkg/mod.py" and m.operator in mutate.OPERATORS and m.line >= 1 for m in mutants)
    assert all(m.diff.startswith("--- a/pkg/mod.py\n") for m in mutants)
    assert len({m.mutated_source for m in mutants}) == 5
    again = mutate.generate(root, ("pkg/mod.py",), seed=3, per_file=5)
    assert [m.diff for m in again] == [m.diff for m in mutants]
    assert mutate.candidate_digest(mutants) == mutate.candidate_digest(again)


def test_rows_record_into_a_real_evidence_directory(tmp_path):
    mutant = mutate.Mutant("wp-01", "pfc/wp.py", "constant_shift", 10, "1 -> 2", "--- a/pfc/wp.py\n", "x = 2\n")
    results = {"unit": mutate.LayerResult("killed", 2.5, "1 failed")}
    rows = mutate.rows("compiler", mutant, mutate.COMPILER_LAYERS, results)
    assert [r["layer"] for r in rows] == [layer.name for layer in mutate.COMPILER_LAYERS]
    assert rows[0]["status"] == "killed" and rows[1]["status"] == "not_run" and rows[1]["seconds"] is None
    assert all(r["killed"] and r["killing_layer"] == "unit" and not r["survivor"] for r in rows)
    provenance = evidence.Provenance("mutate", None, "abc", 0, "2026-11-05T00:00:00+00:00", "vc", "z3", "clang")
    run = evidence.create(
        tmp_path / "out", provenance=provenance, configuration={}, environment={}, schema=mutate.SCHEMA
    )
    for row in rows:
        run.record("layer", "wp", mutate.record_id(mutant, str(row["layer"])), row)
    run.complete(mutate.derive_rows)
    assert evidence.status(tmp_path / "out").complete


def test_layers_are_ordered_unit_golden_differential_breakit_injection_oracle():
    assert [layer.name for layer in mutate.COMPILER_LAYERS] == [
        "unit",
        "golden",
        "differential",
        "breakit",
        "injection",
        "oracle",
    ]
    assert [layer.name for layer in mutate.HARNESS_LAYERS] == ["unit", "integration"]
    golden = mutate.COMPILER_LAYERS[1]
    assert golden.keyword == "golden" and all(
        "golden" not in (layer.keyword or "") for layer in mutate.COMPILER_LAYERS[2:]
    )
    unit = mutate.COMPILER_LAYERS[0]
    assert unit.keyword == "not golden" and "tests/test_vc.py" in unit.files and "tests/test_mutations.py" in unit.files
    for layer in mutate.COMPILER_LAYERS[:-1] + mutate.HARNESS_LAYERS:
        for name in layer.files:
            assert (ROOT / name).is_file(), name


def test_layer_status_maps_pytest_exit_codes_and_timeouts():
    assert mutate.layer_status(0, timed_out=False) == "passed"
    assert mutate.layer_status(1, timed_out=False) == "killed"
    assert mutate.layer_status(2, timed_out=False) == "crashed"
    assert mutate.layer_status(1, timed_out=True) == "timeout"
    for code in (3, 4, 5):
        with pytest.raises(ValueError):
            mutate.layer_status(code, timed_out=False)
    assert mutate.kills("killed") and mutate.kills("crashed") and mutate.kills("timeout") and not mutate.kills("passed")


def test_next_layer_stops_at_the_first_kill_except_a_golden_kill_which_continues_the_semantic_layers():
    layers = mutate.COMPILER_LAYERS
    assert mutate.next_layer(layers, {}) is layers[0]
    assert mutate.next_layer(layers, {"unit": "killed"}) is None
    assert mutate.next_layer(layers, {"unit": "passed"}) is layers[1]
    # a golden kill alone says nothing about semantics: keep going to learn whether another layer kills
    assert mutate.next_layer(layers, {"unit": "passed", "golden": "killed"}) is layers[2]
    assert mutate.next_layer(layers, {"unit": "passed", "golden": "killed", "differential": "passed"}) is layers[3]
    assert mutate.next_layer(layers, {"unit": "passed", "golden": "killed", "differential": "killed"}) is None
    sequence = {
        "unit": "passed",
        "golden": "killed",
        "differential": "passed",
        "breakit": "passed",
        "injection": "passed",
    }
    assert mutate.next_layer(layers, sequence) is None  # the oracle runs only for survivors of the first five
    survivors = {name: "passed" for name in ("unit", "golden", "differential", "breakit", "injection")}
    assert mutate.next_layer(layers, survivors) is layers[5]
    assert mutate.next_layer(layers, {**survivors, "oracle": "passed"}) is None


def test_outcome_classifies_golden_only_kills_and_survivors():
    killed_by_unit = mutate.outcome({"unit": "killed"})
    assert killed_by_unit == mutate.Outcome(killed=True, killing_layer="unit", golden_only=False, survivor=False)
    golden_only = mutate.outcome(
        {"unit": "passed", "golden": "killed", "differential": "passed", "breakit": "passed", "injection": "passed"}
    )
    assert golden_only.killed and golden_only.golden_only and golden_only.killing_layer == "golden"
    both = mutate.outcome({"unit": "passed", "golden": "killed", "differential": "killed"})
    assert both.killed and not both.golden_only and both.killing_layer == "golden"
    survivor = mutate.outcome(
        {name: "passed" for name in ("unit", "golden", "differential", "breakit", "injection", "oracle")}
    )
    assert survivor == mutate.Outcome(killed=False, killing_layer=None, golden_only=False, survivor=True)
    with pytest.raises(ValueError):
        mutate.outcome({"unit": "passed"})  # an unfinished stack is neither killed nor a survivor


def test_oracle_comparison_uses_the_sealed_pfc_side_and_kills_on_any_site_or_verdict_change():
    baseline = {
        "required_passed": True,
        "sites": [
            {
                "id": "bounds:aaa",
                "kind": "bounds",
                "location": {"file": "/x/k.c", "line": 12, "column": 17},
                "status": "proved",
            },
            {
                "id": "division:bbb",
                "kind": "division",
                "location": {"file": "/x/k.c", "line": 9, "column": 3},
                "status": "proved",
            },
        ],
    }

    def report(sites, required=True):
        return {
            "required_passed": required,
            "results": [
                {
                    "obligation": {
                        "id": "other",
                        "kind": kind,
                        "location": {"file": "/y/k.c", "line": line, "column": col},
                    },
                    "status": status,
                }
                for kind, line, col, status in sites
            ]
            + [
                {
                    "obligation": {"id": "o", "kind": "overflow", "location": {"line": 1, "column": 1}},
                    "status": "failed",
                }
            ],
        }

    same = report([("bounds", 12, 17, "proved"), ("division", 9, 3, "proved")])
    assert mutate.oracle_compare(baseline, same) == (True, "")
    failed = report([("bounds", 12, 17, "failed"), ("division", 9, 3, "proved")])
    agree, detail = mutate.oracle_compare(baseline, failed)
    assert not agree and "bounds" in detail and "12" in detail
    dropped = report([("bounds", 12, 17, "proved")])
    assert mutate.oracle_compare(baseline, dropped)[0] is False
    assert (
        mutate.oracle_compare(baseline, report([("bounds", 12, 17, "proved"), ("division", 9, 3, "proved")], False))[0]
        is False
    )


def test_isolated_copy_contains_only_the_listed_paths_and_the_applied_mutant(tmp_path):
    root = tmp_path / "repo"
    for name in ("pkg/mod.py", "tests/test_x.py", "secret/notes.txt", "pyproject.toml"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(SNIPPET if name.endswith("mod.py") else "x\n", encoding="utf-8")
    (root / "pkg" / "__pycache__").mkdir()
    (root / "pkg" / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\x00")
    mutant = mutate.generate(root, ("pkg/mod.py",), seed=0, per_file=1)[0]
    copy = mutate.isolated_copy(root, tmp_path / "copy", mutant, paths=("pkg", "tests", "pyproject.toml"))
    assert (copy / "pkg" / "mod.py").read_text(encoding="utf-8") == mutant.mutated_source
    assert (root / "pkg" / "mod.py").read_text(encoding="utf-8") == SNIPPET
    assert not (copy / "secret").exists() and not (copy / "pkg" / "__pycache__").exists()
    assert (copy / "tests" / "test_x.py").is_file() and (copy / "pyproject.toml").is_file()
    with pytest.raises(FileExistsError):
        mutate.isolated_copy(root, tmp_path / "copy", mutant, paths=("pkg",))


def test_pytest_command_runs_the_copy_with_its_own_python_path(tmp_path):
    layer = mutate.COMPILER_LAYERS[0]
    argv, env = mutate.pytest_command(tmp_path, layer, python="/venv/bin/python")
    assert argv[:3] == ["/venv/bin/python", "-m", "pytest"] and "-x" in argv and "-k" in argv
    assert argv[argv.index("-k") + 1] == "not golden" and "-p" in argv and "no:cacheprovider" in argv
    assert all(name in argv for name in layer.files)
    assert env["PYTHONPATH"] == str(tmp_path) and "PYTHONDONTWRITEBYTECODE" in env


def test_summary_reports_score_with_wilson_interval_golden_only_column_and_survivors():
    def row(mutant, group, layer, status, **outcome):
        base = {
            "group": group,
            "mutant_id": mutant,
            "target": "pfc/wp.py" if group == "compiler" else "pfc_eval/stats.py",
            "operator": "relational_boundary",
            "line": 10,
            "description": "x",
            "layer": layer,
            "status": status,
            "seconds": 1.0,
            "killed": outcome.get("killed", status != "passed"),
            "killing_layer": outcome.get("killing_layer"),
            "golden_only": outcome.get("golden_only", False),
            "survivor": outcome.get("survivor", False),
            "equivalent": None,
        }
        return base

    rows = [
        row("wp-01", "compiler", "unit", "killed", killing_layer="unit"),
        row("wp-02", "compiler", "unit", "passed", killed=True, killing_layer="golden", golden_only=True),
        row("wp-02", "compiler", "golden", "killed", killing_layer="golden", golden_only=True),
        row("wp-02", "compiler", "differential", "passed", killed=True, killing_layer="golden", golden_only=True),
        row("wp-02", "compiler", "breakit", "passed", killed=True, killing_layer="golden", golden_only=True),
        row("wp-02", "compiler", "injection", "passed", killed=True, killing_layer="golden", golden_only=True),
        *[
            row("wp-03", "compiler", layer, "passed", killed=False, survivor=True)
            for layer in ("unit", "golden", "differential", "breakit", "injection", "oracle")
        ],
        row("wp-04", "compiler", "unit", "crashed", killing_layer="unit"),
        row("stats-01", "harness", "unit", "killed", killing_layer="unit"),
    ]
    summary = mutate.summarize(rows)
    compiler = summary["groups"]["compiler"]
    assert compiler["mutants"] == 4 and compiler["killed"] == 3 and compiler["survivors"] == ["wp-03"]
    assert compiler["golden_only"] == ["wp-02"] and compiler["crashed"] == 1
    assert compiler["score"]["estimate"] == pytest.approx(0.75)
    assert 0.3 < compiler["score"]["wilson_low"] < 0.75 < compiler["score"]["wilson_high"] <= 1.0
    assert compiler["score"]["equivalent"] == 0  # hand classification happens in the step document
    assert compiler["kills_by_layer"] == {"unit": 2, "golden": 1}
    assert compiler["score_without_golden_only"]["estimate"] == pytest.approx(0.5)
    assert summary["groups"]["harness"]["score"]["estimate"] == 1.0
    assert summary["passed"] is False  # below the 0.90 target with survivors
    table = mutate.render_table(summary)
    assert "golden-only" in table and "wp-03" in table and "Wilson" in table
    json.dumps(summary)


def test_isolated_copy_links_the_sealed_evidence_read_only(tmp_path):
    root = tmp_path / "root"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (root / "bench" / "eval" / "sealed").mkdir(parents=True)
    (root / "bench" / "eval" / "sealed" / "COMPLETE").write_text("", encoding="utf-8")
    copy = mutate.isolated_copy(root, tmp_path / "copy", None, paths=("pkg",), links=("bench/eval",))
    assert (copy / "bench" / "eval").is_symlink() and (copy / "bench" / "eval" / "sealed" / "COMPLETE").is_file()
    assert (copy / "bench" / "eval").resolve() == (root / "bench" / "eval").resolve()


def test_run_can_select_only_the_harness_mutants(capsys, tmp_path):
    assert mutate.run([], tmp_path / "out", compiler="clang-19", dry_run=True, targets="harness")
    out = capsys.readouterr().out
    assert "compiler " not in out.split("compiler layers")[0] and "harness " in out
    with pytest.raises(ValueError, match="targets"):
        mutate.run([], tmp_path / "out", compiler="clang-19", dry_run=True, targets="everything")


EXTENDED_SNIPPET = """\
def combine(a, b, flag, items):
    total = a + b
    if flag and total == 3:
        return total * 2
    return not (a in items)
"""


def test_extended_operators_produce_compiling_distinct_mutants():
    assert mutate.EXTENDED_OPERATORS == (
        "arithmetic_operator",
        "boolean_operator",
        "equality_flip",
        "return_replacement",
    )
    assert mutate.ALL_OPERATORS == mutate.OPERATORS + mutate.EXTENDED_OPERATORS
    sites = mutate.sites(EXTENDED_SNIPPET, operators=mutate.ALL_OPERATORS)
    by_operator = {op: [s for s in sites if s.operator == op] for op in mutate.EXTENDED_OPERATORS}
    assert all(by_operator[op] for op in mutate.EXTENDED_OPERATORS), {k: len(v) for k, v in by_operator.items()}
    replacements = {s.replacement for s in sites}
    assert "a - b" in replacements and "total / 2" in replacements and "flag or total == 3" in replacements
    assert "total != 3" in replacements and "a not in items" in replacements and "return None" in replacements
    assert "return not (not (a in items))" not in replacements and "return a in items" in replacements
    for site in sites:
        mutated = mutate.apply(EXTENDED_SNIPPET, site)
        ast.parse(mutated)
        assert mutated != EXTENDED_SNIPPET
    assert mutate.sites(EXTENDED_SNIPPET) == mutate.sites(EXTENDED_SNIPPET, operators=mutate.OPERATORS)
    assert not any(s.operator in mutate.EXTENDED_OPERATORS for s in mutate.sites(EXTENDED_SNIPPET))


def test_all_selects_the_whole_population_and_records_its_size(tmp_path):
    population = mutate.select(EXTENDED_SNIPPET, seed=0, count=None, operators=mutate.ALL_OPERATORS)
    distinct = {
        mutate.apply(EXTENDED_SNIPPET, s) for s in mutate.sites(EXTENDED_SNIPPET, operators=mutate.ALL_OPERATORS)
    }
    assert len(population) == len(distinct) > 5
    assert population == mutate.select(EXTENDED_SNIPPET, seed=99, count=None, operators=mutate.ALL_OPERATORS)
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text(EXTENDED_SNIPPET, encoding="utf-8")
    mutants = mutate.generate(root, ("pkg/mod.py",), seed=0, per_file=None, operators=mutate.ALL_OPERATORS)
    assert len(mutants) == len(population) and len({m.identifier for m in mutants}) == len(mutants)
    assert all(len(m.identifier.rsplit("-", 1)[1]) == len(str(len(mutants))) or len(mutants) < 10 for m in mutants)
    assert mutate.population_size(root, ("pkg/mod.py",), operators=mutate.ALL_OPERATORS) == {
        "pkg/mod.py": len(population)
    }


def test_extended_targets_form_their_own_disclosed_group(capsys, tmp_path):
    assert mutate.EXTENDED_TARGETS == ("pfc/smt.py", "pfc/propose/invariants.py", "pfc/lower.py")
    assert mutate.TARGET_GROUPS == ("all", "compiler", "harness", "extended")
    assert (
        "tests/test_smt.py" in mutate.EXTENDED_LAYERS[0].files
        and "tests/test_subset.py" in mutate.EXTENDED_LAYERS[0].files
    )
    assert [layer.name for layer in mutate.EXTENDED_LAYERS] == [layer.name for layer in mutate.COMPILER_LAYERS]
    assert mutate.run([], tmp_path / "x", compiler="cc", dry_run=True, targets="extended", per_file=1) is True
    out = capsys.readouterr().out
    assert "compiler-extended smt-01 pfc/smt.py" in out
    assert not any(line.split(" ")[0] in ("compiler", "harness") and "-0" in line for line in out.splitlines())
    allowed = {c.name: c.allowed for c in mutate.SCHEMA.columns}
    assert "compiler-extended" in allowed["group"] and set(mutate.ALL_OPERATORS) <= set(allowed["operator"])


def test_survivor_index_and_diffs_are_written_for_every_survivor():
    mutants = [
        mutate.Mutant(
            "wp-01", "pfc/wp.py", "constant_shift", 3, "1 -> 2", "--- a/pfc/wp.py\n+++ b/pfc/wp.py\n@@\n", "x"
        ),
        mutate.Mutant("wp-02", "pfc/wp.py", "negated_condition", 9, "c -> not c", "--- a\n+++ b\n", "y"),
    ]
    rows = [
        {"mutant_id": "wp-01", "group": "compiler", "layer": "oracle", "survivor": True, "status": "passed"},
        {"mutant_id": "wp-01", "group": "compiler", "layer": "unit", "survivor": True, "status": "passed"},
        {"mutant_id": "wp-02", "group": "compiler", "layer": "unit", "survivor": False, "status": "killed"},
    ]
    index, diffs = mutate.survivor_index(mutants, rows)
    assert set(diffs) == {"survivors/wp-01.diff"} and diffs["survivors/wp-01.diff"].startswith("--- a/pfc/wp.py")
    assert "| wp-01 | compiler | pfc/wp.py:3 | constant_shift | 1 -> 2 | oracle, unit |" in index
    assert "wp-02" not in index and index.startswith("# Survivors")
