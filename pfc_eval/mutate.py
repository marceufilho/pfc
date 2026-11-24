"""Source mutants of the trusted files, isolated copies and the layered kill matrix (roadmap E9, design E1.4).

Mutants are generated from the Python AST with a fixed operator family and a committed seed, applied as
minimal textual edits to copies of the repository, and run through the test pyramid in layers: unit,
golden, differential, break-it, injection, and the independent oracle for the mutants that survive the
first five. Nothing here writes under the repository's `pfc/`; every mutant lives in its own temporary
copy, and the protected inventory is re-verified when the campaign ends. The harness mutates itself too
(`interpret.py`, `stats.py`) with its own two layers.
"""

import ast
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from random import Random

from pfc.config import DEFAULT_CPP, SOLVER_TIMEOUT_MS
from pfc_eval import campaign, evidence, stats

ROOT = Path(__file__).resolve().parents[1]
OPERATORS = ("relational_boundary", "constant_shift", "negated_condition", "deleted_statement", "swapped_operands")
# Disclosed extension (revision plan, phase 5): the pre-registered family above stays the default and keeps the
# historical campaigns reproducible; `--all` adds these operators and takes the whole distinct population.
EXTENDED_OPERATORS = ("arithmetic_operator", "boolean_operator", "equality_flip", "return_replacement")
ALL_OPERATORS = OPERATORS + EXTENDED_OPERATORS
COMPILER_TARGETS = ("pfc/vc.py", "pfc/wp.py", "pfc/authorization.py", "pfc/emit.py")
EXTENDED_TARGETS = ("pfc/smt.py", "pfc/propose/invariants.py", "pfc/lower.py")  # disclosed extension of the trusted set
HARNESS_TARGETS = ("pfc_eval/interpret.py", "pfc_eval/stats.py", "pfc_eval/proposer.py", "pfc_eval/tables.py")
TARGET_GROUPS = ("all", "compiler", "harness", "extended")
MUTANTS_PER_FILE = 10  # 40 compiler mutants, inside the pre-registered 30–50
HARNESS_PER_FILE = 5
TARGET_SCORE = 0.90
LAYER_TIMEOUT_S = 600
ORACLE_BASELINE = ROOT / "bench" / "oracle" / "wp32-v4-20261105"
COPY_PATHS = (
    "pfc",
    "pfc_eval",
    "tests",
    "kernels",
    "examples",
    "include",
    "tools",
    "validation",
    "bench/external",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "main.py",
    "Makefile",
)
LINK_PATHS = ("heldout",)  # read-only inputs; linked, never copied per mutant
Status = str  # passed | killed | crashed | timeout | not_run
_BOUNDARY = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}
_BOUNDARY_NODES: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}
_SWAPPABLE_BINARY = (ast.Sub, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.LShift, ast.RShift)
_ARITHMETIC: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Mult,
    ast.Mod: ast.FloorDiv,
}
_EQUALITY: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
_DELETABLE = (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Raise)


@dataclass(frozen=True)
class Site:
    operator: str
    line: int
    column: int
    end_line: int
    end_column: int
    original: str
    replacement: str


@dataclass(frozen=True)
class Mutant:
    identifier: str
    target: str
    operator: str
    line: int
    description: str
    diff: str
    mutated_source: str


@dataclass(frozen=True)
class Layer:
    name: str
    files: tuple[str, ...]
    keyword: str | None = None


@dataclass(frozen=True)
class LayerResult:
    status: Status
    seconds: float
    detail: str


@dataclass(frozen=True)
class Outcome:
    killed: bool
    killing_layer: str | None
    golden_only: bool
    survivor: bool


@dataclass(frozen=True)
class OracleCase:
    name: str
    original: str
    pfc: dict[str, object]


COMPILER_LAYERS = (
    Layer(
        "unit",
        (
            "tests/test_vc.py",
            "tests/test_general_vc.py",
            "tests/test_read_domain.py",
            "tests/test_emit.py",
            "tests/test_general_emit.py",
            "tests/test_review_followup.py",
            "tests/test_review_revisions.py",
            "tests/test_assessment_adjustments.py",
            "tests/test_check_summary.py",
            "tests/test_smt.py",
            "tests/test_mutations.py",
            "tests/test_replay.py",
            "tests/test_solver_cache.py",
            "tests/test_authorization_fixtures.py",
            "tests/eval/test_eval_mutation_gaps.py",
        ),
        "not golden",
    ),
    Layer("golden", ("tests/test_emit.py", "tests/test_general_emit.py", "tests/test_ir.py"), "golden"),
    Layer(
        "differential",
        (
            "tests/test_difftest.py",
            "tests/test_difftest_integration.py",
            "tests/test_difftest_mutation.py",
            "tests/test_original_differential.py",
            "tests/test_optimized_differential.py",
            "tests/test_six_kernels.py",
        ),
    ),
    Layer("breakit", ("tests/test_breakit.py", "tests/test_kernel_breakit.py", "tests/test_slice.py")),
    Layer("injection", ("tests/test_soundness.py", "tests/test_proposal_validation.py", "tests/test_proposal_loop.py")),
    Layer("oracle", ()),
)
HARNESS_LAYERS = (
    Layer(
        "unit",
        (
            "tests/eval/test_eval_interpret.py",
            "tests/eval/test_eval_stats.py",
            "tests/eval/test_eval_mutation_gaps.py",
            "tests/eval/test_eval_proposer.py",
        ),
    ),
    Layer(
        "integration",
        (
            "tests/eval/test_eval_interpret_integration.py",
            "tests/eval/test_eval_differential_integration.py",
            "tests/eval/test_eval_conjuncts.py",
            "tests/eval/test_eval_inject.py",
            "tests/eval/test_eval_proposer_integration.py",  # live-path campaign in test mode (survivor proposer-05)
        ),
    ),
)
EXTENDED_LAYERS = (
    Layer(
        "unit",
        COMPILER_LAYERS[0].files
        + (
            "tests/test_ir.py",
            "tests/test_subset.py",
            "tests/test_parse.py",
            "tests/test_target.py",
            "tests/test_check_cli.py",
            "tests/test_proposal_validation.py",
            "tests/test_proposal_loop.py",
        ),
        "not golden",
    ),
) + COMPILER_LAYERS[1:]
SCHEMA = evidence.Schema(
    (
        evidence.Column("group", "string", allowed=("compiler", "harness", "compiler-extended")),
        evidence.Column("mutant_id", "string"),
        evidence.Column("target", "string"),
        evidence.Column("operator", "string", allowed=ALL_OPERATORS),
        evidence.Column("line", "integer"),
        evidence.Column("description", "string"),
        evidence.Column("layer", "string"),
        evidence.Column("status", "string", allowed=("passed", "killed", "crashed", "timeout", "not_run")),
        evidence.Column("seconds", "number", nullable=True),
        evidence.Column("killed", "boolean"),
        evidence.Column("killing_layer", "string", nullable=True),
        evidence.Column("golden_only", "boolean"),
        evidence.Column("survivor", "boolean"),
        evidence.Column("equivalent", "boolean", nullable=True),
    )
)


# --- mutant generation -------------------------------------------------------------------------------


def _segment(source: str, node: ast.AST) -> str | None:
    return ast.get_source_segment(source, node)


def _site(source: str, node: ast.expr | ast.stmt, operator: str, replacement: str) -> Site | None:
    original = _segment(source, node)
    if original is None or original == replacement or node.end_lineno is None or node.end_col_offset is None:
        return None
    return Site(operator, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, original, replacement)


def _constant_sites(source: str, node: ast.Constant) -> list[Site]:
    found: list[Site] = []
    if isinstance(node.value, bool) or not isinstance(node.value, int):
        if isinstance(node.value, str) and node.value in _BOUNDARY:
            original = _segment(source, node)
            if original and len(original) >= 2:
                replacement = original[0] + _BOUNDARY[node.value] + original[-1]
                site = _site(source, node, "relational_boundary", replacement)
                found.extend([site] if site else [])
        return found
    for shift in (1, -1):
        site = _site(source, node, "constant_shift", str(node.value + shift))
        found.extend([site] if site else [])
    return found


def _compare_sites(source: str, node: ast.Compare) -> list[Site]:
    if len(node.ops) != 1 or type(node.ops[0]) not in _BOUNDARY_NODES:
        return []
    found: list[Site] = []
    flipped = ast.Compare(left=node.left, ops=[_BOUNDARY_NODES[type(node.ops[0])]()], comparators=node.comparators)
    site = _site(source, node, "relational_boundary", ast.unparse(flipped))
    found.extend([site] if site else [])
    swapped = ast.Compare(left=node.comparators[0], ops=[type(node.ops[0])()], comparators=[node.left])
    if _segment(source, node.left) != _segment(source, node.comparators[0]):
        site = _site(source, node, "swapped_operands", ast.unparse(swapped))
        found.extend([site] if site else [])
    return found


def _binary_sites(source: str, node: ast.BinOp) -> list[Site]:
    if not isinstance(node.op, _SWAPPABLE_BINARY) or _segment(source, node.left) == _segment(source, node.right):
        return []
    swapped = ast.BinOp(left=node.right, op=node.op, right=node.left)
    site = _site(source, node, "swapped_operands", ast.unparse(swapped))
    return [site] if site else []


def _condition_sites(source: str, node: ast.If | ast.While) -> list[Site]:
    original = _segment(source, node.test)
    if original is None:
        return []
    site = _site(source, node.test, "negated_condition", f"not ({original})")
    return [site] if site else []


def _deletion_sites(source: str, body: Sequence[ast.stmt]) -> list[Site]:
    if len(body) < 2:
        return []
    found: list[Site] = []
    for statement in body:
        if not isinstance(statement, _DELETABLE) or _is_declaration_only(statement):
            continue
        site = _site(source, statement, "deleted_statement", "pass")
        found.extend([site] if site else [])
    return found


def _is_declaration_only(statement: ast.stmt) -> bool:
    """Docstrings and bare annotations have no behaviour; deleting them yields an equivalent mutant."""
    if isinstance(statement, ast.AnnAssign) and statement.value is None:
        return True
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _arithmetic_sites(source: str, node: ast.BinOp) -> list[Site]:
    replacement = _ARITHMETIC.get(type(node.op))
    if replacement is None:
        return []
    site = _site(source, node, "arithmetic_operator", ast.unparse(ast.BinOp(node.left, replacement(), node.right)))
    return [site] if site else []


def _boolean_sites(source: str, node: ast.BoolOp) -> list[Site]:
    flipped = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    site = _site(source, node, "boolean_operator", ast.unparse(ast.BoolOp(flipped, node.values)))
    return [site] if site else []


def _equality_sites(source: str, node: ast.Compare) -> list[Site]:
    if len(node.ops) != 1 or type(node.ops[0]) not in _EQUALITY:
        return []
    flipped = ast.Compare(left=node.left, ops=[_EQUALITY[type(node.ops[0])]()], comparators=node.comparators)
    site = _site(source, node, "equality_flip", ast.unparse(flipped))
    return [site] if site else []


def _return_sites(source: str, node: ast.Return) -> list[Site]:
    value = node.value
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return []
    found: list[Site] = []
    site = _site(source, node, "return_replacement", "return None")
    found.extend([site] if site else [])
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not):
        inner = ast.unparse(value.operand)
        site = _site(source, node, "return_replacement", f"return {inner}")
    elif isinstance(value, (ast.Compare, ast.BoolOp)) or (
        isinstance(value, ast.Constant) and isinstance(value.value, bool)
    ):
        site = _site(source, node, "return_replacement", f"return not ({ast.unparse(value)})")
    else:
        site = None
    found.extend([site] if site else [])
    return found


def sites(source: str, *, operators: Sequence[str] = OPERATORS) -> tuple[Site, ...]:
    """Every mutation site of the chosen operators, in source order; each applies to a valid program."""
    unknown = set(operators) - set(ALL_OPERATORS)
    if unknown:
        raise ValueError(f"unknown mutation operators: {', '.join(sorted(unknown))}")
    chosen = set(operators)
    tree = ast.parse(source)
    found: list[Site] = []

    def visit(node: ast.AST, in_function: bool) -> None:
        if isinstance(node, ast.Constant):
            found.extend(_constant_sites(source, node))
        elif isinstance(node, ast.Compare):
            found.extend(_compare_sites(source, node))
            found.extend(_equality_sites(source, node))
        elif isinstance(node, ast.BinOp):
            found.extend(_binary_sites(source, node))
            found.extend(_arithmetic_sites(source, node))
        elif isinstance(node, ast.BoolOp):
            found.extend(_boolean_sites(source, node))
        elif isinstance(node, ast.Return):
            found.extend(_return_sites(source, node))
        elif isinstance(node, (ast.If, ast.While)):
            found.extend(_condition_sites(source, node))
        inside = in_function or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if inside:
            for field in ("body", "orelse", "finalbody"):
                statements = getattr(node, field, None)
                if isinstance(statements, list) and statements and isinstance(statements[0], ast.stmt):
                    found.extend(_deletion_sites(source, statements))
        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    visit(tree, False)
    selected = {s for s in found if s.operator in chosen}
    ordered = sorted(selected, key=lambda s: (s.line, s.column, ALL_OPERATORS.index(s.operator), s.replacement))
    return tuple(s for s in ordered if _compiles(apply(source, s)))


def _compiles(text: str) -> bool:
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    return True


def _offset(lines: Sequence[bytes], line: int, column: int) -> int:
    return sum(len(text) for text in lines[: line - 1]) + column


def apply(source: str, site: Site) -> str:
    """Replace exactly the site's segment; AST columns are UTF-8 byte offsets."""
    data = source.encode("utf-8")
    lines = source.encode("utf-8").splitlines(keepends=True)
    start = _offset(lines, site.line, site.column)
    end = _offset(lines, site.end_line, site.end_column)
    if data[start:end].decode("utf-8") != site.original:
        raise ValueError("site does not match the source")
    return (data[:start] + site.replacement.encode("utf-8") + data[end:]).decode("utf-8")


def _population(source: str, operators: Sequence[str]) -> list[Site]:
    distinct: dict[str, Site] = {}
    for site in sites(source, operators=operators):
        distinct.setdefault(apply(source, site), site)
    return list(distinct.values())


def select(source: str, *, seed: int, count: int | None, operators: Sequence[str] = OPERATORS) -> tuple[Site, ...]:
    """Seeded sample of distinct mutants (distinct mutated texts), at most `count`; `None` takes them all."""
    if count is not None and count < 1:
        raise ValueError("count must be at least 1")
    population = _population(source, operators)
    chosen = population if count is None else Random(seed).sample(population, k=min(count, len(population)))
    return tuple(sorted(chosen, key=lambda s: (s.line, s.column, ALL_OPERATORS.index(s.operator), s.replacement)))


def population_size(root: Path, targets: Sequence[str], *, operators: Sequence[str] = OPERATORS) -> dict[str, int]:
    """Distinct mutants per target file for the chosen operators (what `--all` would run)."""
    return {t: len(_population((root / t).read_text(encoding="utf-8"), operators)) for t in targets}


def diff(label: str, original: str, mutated: str) -> str:
    lines = difflib.unified_diff(
        original.splitlines(keepends=True), mutated.splitlines(keepends=True), f"a/{label}", f"b/{label}", n=3
    )
    return "".join(lines)


def generate(
    root: Path, targets: Sequence[str], *, seed: int, per_file: int | None, operators: Sequence[str] = OPERATORS
) -> tuple[Mutant, ...]:
    """`per_file` seeded mutants of each target (`None`: the whole population), read from `root`; nothing is written."""
    mutants: list[Mutant] = []
    for target in targets:
        source = (root / target).read_text(encoding="utf-8")
        stem = Path(target).stem
        chosen = select(source, seed=seed, count=per_file, operators=operators)
        width = max(2, len(str(len(chosen))))
        for index, site in enumerate(chosen, 1):
            mutated = apply(source, site)
            mutants.append(
                Mutant(
                    f"{stem}-{index:0{width}d}",
                    target,
                    site.operator,
                    site.line,
                    f"{site.original} -> {site.replacement}",
                    diff(target, source, mutated),
                    mutated,
                )
            )
    return tuple(mutants)


def candidate_digest(mutants: Sequence[Mutant]) -> str:
    return hashlib.sha256("".join(m.target + "\n" + m.diff for m in mutants).encode("utf-8")).hexdigest()


# --- isolated copies and layers ----------------------------------------------------------------------


def isolated_copy(
    root: Path,
    destination: Path,
    mutant: Mutant | None,
    *,
    paths: Sequence[str] = COPY_PATHS,
    links: Sequence[str] = LINK_PATHS,
) -> Path:
    """Copy the listed repository paths into a new directory and apply the mutant there, never in `root`.

    `links` are symlinked to the originals: sealed evidence is read-only for every layer and too large to copy
    once per mutant; a mutant that wrote into it would be an operational error, not a kill.
    """
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"{destination} exists")
    destination.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache")
    for name in paths:
        source = root / name
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=ignore, symlinks=False)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for name in links:
        source = root / name
        if source.is_dir():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve(), target_is_directory=True)
    if mutant is not None:
        path = destination / mutant.target
        if not path.is_file():
            raise ValueError(f"mutant target {mutant.target} is not part of the copy")
        path.write_text(mutant.mutated_source, encoding="utf-8")
    return destination


def pytest_command(copy: Path, layer: Layer, *, python: str) -> tuple[list[str], dict[str, str]]:
    argv = [python, "-m", "pytest", *layer.files, "-q", "-x", "-p", "no:cacheprovider", "--tb=line"]
    if layer.keyword:
        argv += ["-k", layer.keyword]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = str(copy)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return argv, env


def layer_status(returncode: int, *, timed_out: bool) -> Status:
    """pytest exit codes: 0 all passed, 1 failures (killed), 2 interrupted/collection error (crashed)."""
    if timed_out:
        return "timeout"
    if returncode == 0:
        return "passed"
    if returncode == 1:
        return "killed"
    if returncode == 2:
        return "crashed"
    raise ValueError(f"pytest exit code {returncode} is an operational error, not a verdict")


def kills(status: Status) -> bool:
    return status in ("killed", "crashed", "timeout")


def _summary_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def run_layer(copy: Path, layer: Layer, *, python: str, timeout_s: int = LAYER_TIMEOUT_S) -> LayerResult:
    argv, env = pytest_command(copy, layer, python=python)
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=copy, env=env, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return LayerResult("timeout", time.monotonic() - started, f"layer exceeded {timeout_s} s")
    seconds = time.monotonic() - started
    status = layer_status(result.returncode, timed_out=False)
    return LayerResult(status, seconds, _summary_line(result.stdout) or _summary_line(result.stderr))


def next_layer(layers: Sequence[Layer], statuses: Mapping[str, Status]) -> Layer | None:
    """Stop at the first kill, except a lone golden kill, which still runs the semantic layers (not the oracle)."""
    killing = [name for name, status in statuses.items() if kills(status)]
    for layer in layers:
        if layer.name in statuses:
            continue
        if not killing:
            return layer
        if killing == ["golden"] and layer.name != "oracle":
            return layer
        return None
    return None


def outcome(statuses: Mapping[str, Status], layers: Sequence[Layer] = COMPILER_LAYERS) -> Outcome:
    names = [layer.name for layer in layers]
    killing = [name for name in names if kills(statuses.get(name, "not_run"))]
    semantic = [name for name in names if name not in ("golden", "oracle")]
    if killing:
        golden_only = killing == ["golden"] and all(statuses.get(name) == "passed" for name in semantic)
        if killing == ["golden"] and not all(name in statuses for name in semantic):
            raise ValueError("a golden kill needs the semantic layers before it can be classified")
        return Outcome(True, killing[0], golden_only, False)
    if all(statuses.get(name) == "passed" for name in names):
        return Outcome(False, None, False, True)
    raise ValueError("an unfinished layer stack is neither killed nor a survivor")


# --- oracle layer -----------------------------------------------------------------------------------


def oracle_baseline(directory: Path = ORACLE_BASELINE) -> tuple[OracleCase, ...]:
    """The sealed pfc side of the fourteen positive oracle cases; WP verdicts do not depend on pfc."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("oracle manifest must map file names to digests")
    configuration = json.loads((directory / "configuration.json").read_text(encoding="utf-8"))
    cases: list[OracleCase] = []
    for case in configuration["cases"]:
        if "original" not in case:
            continue
        name = f"{case['name']}/comparison.json"
        data = (directory / name).read_bytes()
        if manifest.get(name) != hashlib.sha256(data).hexdigest():
            raise ValueError(f"sealed comparison {name} does not match the oracle manifest")
        comparison = json.loads(data.decode("utf-8"))
        cases.append(OracleCase(case["name"], case["original"], dict(comparison["pfc"])))
    return tuple(cases)


def _inventory(sites_: Sequence[Mapping[str, object]]) -> Counter[tuple[str, int, int, str]]:
    inventory: Counter[tuple[str, int, int, str]] = Counter()
    for site in sites_:
        location = site["location"]
        if not isinstance(location, Mapping):
            raise ValueError("site without location")
        inventory[
            (str(site["kind"]), int(str(location["line"])), int(str(location["column"])), str(site["status"]))
        ] += 1
    return inventory


def _mappings(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("expected a list of records")
    return list(value)


def _obligation(result: Mapping[str, object]) -> Mapping[str, object]:
    obligation = result["obligation"]
    if not isinstance(obligation, Mapping):
        raise ValueError("result without obligation")
    return obligation


def oracle_compare(baseline: Mapping[str, object], report: Mapping[str, object]) -> tuple[bool, str]:
    """Agreement cell unchanged: same required verdict and the same (kind, line, column, status) inventory."""
    expected = _inventory(_mappings(baseline["sites"]))
    actual = _inventory(
        [
            {"kind": _obligation(r)["kind"], "location": _obligation(r)["location"], "status": r["status"]}
            for r in _mappings(report["results"])
            if _obligation(r)["kind"] in ("bounds", "division")
        ]
    )
    problems: list[str] = []
    if bool(baseline["required_passed"]) != bool(report["required_passed"]):
        problems.append(f"required_passed {baseline['required_passed']} -> {report['required_passed']}")
    for key in sorted((expected - actual) + (actual - expected)):
        kind, line, column, status = key
        problems.append(f"{kind} at {line}:{column} {status}: baseline {expected[key]}, mutant {actual[key]}")
    return (not problems, "; ".join(problems))


def pfc_check_command(copy: Path, source: Path, *, python: str, compiler: str) -> tuple[list[str], dict[str, str]]:
    argv = [
        python,
        "-c",
        "import sys; from pfc.cli import main; sys.exit(main(sys.argv[1:]))",
        "check",
        str(source),
        "--clang",
        compiler,
        "--json",
    ]
    _, env = pytest_command(copy, COMPILER_LAYERS[0], python=python)
    return argv, env


def run_oracle_layer(
    copy: Path, cases: Sequence[OracleCase], *, python: str, compiler: str, timeout_s: int = LAYER_TIMEOUT_S
) -> LayerResult:
    """Run the copy's `pfc check` on every positive oracle program and compare with the sealed pfc side."""
    if not cases:
        return LayerResult("not_run", 0.0, "no recorded oracle comparison in this tree")
    started = time.monotonic()
    disagreements: list[str] = []
    for case in cases:
        argv, env = pfc_check_command(copy, copy / case.original, python=python, compiler=compiler)
        try:
            result = subprocess.run(
                argv, cwd=copy, env=env, capture_output=True, text=True, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired:
            return LayerResult("timeout", time.monotonic() - started, f"{case.name}: pfc check exceeded {timeout_s} s")
        if result.returncode not in (0, 1):
            return LayerResult("crashed", time.monotonic() - started, f"{case.name}: {_summary_line(result.stderr)}")
        try:
            report = json.loads(result.stdout)
            agree, detail = oracle_compare(case.pfc, report)
        except (ValueError, KeyError, TypeError) as error:
            return LayerResult("crashed", time.monotonic() - started, f"{case.name}: unreadable report ({error})")
        if not agree:
            disagreements.append(f"{case.name}: {detail}")
    seconds = time.monotonic() - started
    if disagreements:
        return LayerResult("killed", seconds, "; ".join(disagreements)[:1000])
    return LayerResult("passed", seconds, f"{len(cases)} case(s) agree with the sealed pfc side")


# --- campaign ---------------------------------------------------------------------------------------


def _run_stack(
    copy: Path,
    layers: Sequence[Layer],
    *,
    python: str,
    compiler: str,
    cases: Sequence[OracleCase],
    timeout_s: int,
) -> dict[str, LayerResult]:
    results: dict[str, LayerResult] = {}
    while (layer := next_layer(layers, {name: r.status for name, r in results.items()})) is not None:
        if layer.name == "oracle":
            results[layer.name] = run_oracle_layer(copy, cases, python=python, compiler=compiler, timeout_s=timeout_s)
        else:
            results[layer.name] = run_layer(copy, layer, python=python, timeout_s=timeout_s)
    return results


def rows(
    group: str, mutant: Mutant, layers: Sequence[Layer], results: Mapping[str, LayerResult]
) -> list[dict[str, object]]:
    verdict = outcome({name: r.status for name, r in results.items()}, layers)
    table: list[dict[str, object]] = []
    for layer in layers:
        result = results.get(layer.name)
        table.append(
            {
                "group": group,
                "mutant_id": mutant.identifier,
                "target": mutant.target,
                "operator": mutant.operator,
                "line": mutant.line,
                "description": mutant.description,
                "layer": layer.name,
                "status": result.status if result else "not_run",
                "seconds": round(result.seconds, 3) if result else None,
                "killed": verdict.killed,
                "killing_layer": verdict.killing_layer,
                "golden_only": verdict.golden_only,
                "survivor": verdict.survivor,
                "equivalent": None,
            }
        )
    return table


def record_id(mutant: Mutant, layer: str) -> str:
    """Raw record identity `<mutant>.<layer>`; evidence names allow letters, digits, dot, dash, underscore."""
    return f"{mutant.identifier}.{layer}"


def _score(killed: int, total: int, equivalent: int = 0) -> dict[str, object]:
    denominator = total - equivalent
    interval = stats.wilson(killed, denominator) if denominator else stats.Interval(0.0, 1.0)
    return {
        "killed": killed,
        "total": total,
        "equivalent": equivalent,
        "estimate": killed / denominator if denominator else None,
        "wilson_low": interval.low,
        "wilson_high": interval.high,
    }


def summarize(rows_: Sequence[Mapping[str, object]]) -> dict[str, object]:
    groups: dict[str, dict[str, object]] = {}
    for group in sorted({str(r["group"]) for r in rows_}):
        mine = [r for r in rows_ if r["group"] == group]
        by_mutant: dict[str, list[Mapping[str, object]]] = {}
        for r in mine:
            by_mutant.setdefault(str(r["mutant_id"]), []).append(r)
        killed = [m for m, rs in by_mutant.items() if rs[0]["killed"]]
        golden_only = [m for m, rs in by_mutant.items() if rs[0]["golden_only"]]
        survivors = [m for m, rs in by_mutant.items() if rs[0]["survivor"]]
        statuses = {m: {str(r["layer"]): str(r["status"]) for r in rs} for m, rs in by_mutant.items()}
        kills_by_layer = Counter(str(rs[0]["killing_layer"]) for m, rs in by_mutant.items() if rs[0]["killed"])
        operators = Counter(str(rs[0]["operator"]) for rs in by_mutant.values())
        killed_operators = Counter(str(rs[0]["operator"]) for rs in by_mutant.values() if rs[0]["killed"])
        targets = Counter(str(rs[0]["target"]) for rs in by_mutant.values())
        killed_targets = Counter(str(rs[0]["target"]) for rs in by_mutant.values() if rs[0]["killed"])
        total = len(by_mutant)
        groups[group] = {
            "mutants": total,
            "killed": len(killed),
            "survivors": survivors,
            "golden_only": golden_only,
            "crashed": sum(any(s == "crashed" for s in st.values()) for st in statuses.values()),
            "timeouts": sum(any(s == "timeout" for s in st.values()) for st in statuses.values()),
            "kills_by_layer": dict(sorted(kills_by_layer.items())),
            "by_operator": {op: {"mutants": n, "killed": killed_operators[op]} for op, n in sorted(operators.items())},
            "by_target": {t: {"mutants": n, "killed": killed_targets[t]} for t, n in sorted(targets.items())},
            "score": _score(len(killed), total),
            "score_without_golden_only": _score(len(killed) - len(golden_only), total),
            "matrix": {m: statuses[m] for m in sorted(statuses)},
        }
    passed = all((g["score"]["estimate"] or 0.0) >= TARGET_SCORE for g in groups.values())  # type: ignore[index]
    return {"target_score": TARGET_SCORE, "groups": groups, "passed": passed}


def render_table(summary: Mapping[str, object]) -> str:
    groups: Mapping[str, Mapping[str, object]] = summary["groups"]  # type: ignore[assignment]
    lines = [
        "# Mutation kill matrix (E1.4)",
        "",
        "| group | mutants | killed | golden-only kills | survivors | score (Wilson 95%) | score without golden-only |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for name, g in groups.items():
        score: Mapping[str, object] = g["score"]  # type: ignore[assignment]
        without: Mapping[str, object] = g["score_without_golden_only"]  # type: ignore[assignment]
        survivors = ", ".join(str(s) for s in _strings(g["survivors"])) or "none"
        lines.append(
            f"| {name} | {g['mutants']} | {g['killed']} | {len(g['golden_only'])} | {survivors} "  # type: ignore[arg-type]
            f"| {_fmt(score)} | {_fmt(without)} |"
        )
    lines += [
        "",
        f"Target: score ≥ {summary['target_score']}; equivalent mutants are hand-classified in the step report.",
        "",
    ]
    for name, g in groups.items():
        matrix: Mapping[str, Mapping[str, str]] = g["matrix"]  # type: ignore[assignment]
        layers = sorted({layer for statuses in matrix.values() for layer in statuses}, key=_layer_order)
        lines += [f"## {name}", "", "| mutant | " + " | ".join(layers) + " |", "| --- |" + " --- |" * len(layers)]
        for mutant, statuses in matrix.items():
            lines.append(f"| {mutant} | " + " | ".join(statuses.get(layer, "not_run") for layer in layers) + " |")
        lines.append("")
    return "\n".join(lines)


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _layer_order(name: str) -> int:
    order = [layer.name for layer in COMPILER_LAYERS] + ["integration"]
    return order.index(name) if name in order else len(order)


def _fmt(score: Mapping[str, object]) -> str:
    if score["estimate"] is None:
        return "n/a"
    estimate, low, high = (float(str(score[key])) for key in ("estimate", "wilson_low", "wilson_high"))
    return f"{estimate:.3f} [{low:.3f}, {high:.3f}]"


def derive_rows(records: tuple[evidence.Record, ...]) -> list[Mapping[str, object]]:
    return [record.data for record in records if record.unit == "layer"]


def survivor_index(mutants: Sequence[Mutant], rows_: Sequence[Mapping[str, object]]) -> tuple[str, dict[str, str]]:
    """`survivors/INDEX.md` and one diff per surviving mutant, for hand classification after the campaign."""
    by_id = {m.identifier: m for m in mutants}
    survivors: dict[str, dict[str, object]] = {}
    for row in rows_:
        if row.get("survivor"):
            entry = survivors.setdefault(str(row["mutant_id"]), {"group": row["group"], "layers": []})
            layers = entry["layers"]
            assert isinstance(layers, list)
            layers.append(str(row["layer"]))
    lines = [
        "# Survivors",
        "",
        "Mutants no layer killed. Classify each by hand in `pfc_eval/equivalents.json` (diff digest, class,"
        " justification, reviewer, date); the tables report the score with and without the classified equivalents.",
        "",
        "| mutant | group | site | operator | description | layers passed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    diffs: dict[str, str] = {}
    for identifier in sorted(survivors):
        mutant = by_id[identifier]
        layers = survivors[identifier]["layers"]
        assert isinstance(layers, list)
        lines.append(
            f"| {identifier} | {survivors[identifier]['group']} | {mutant.target}:{mutant.line} | {mutant.operator} "
            f"| {mutant.description} | {', '.join(sorted(layers))} |"
        )
        diffs[f"survivors/{identifier}.diff"] = mutant.diff
    return "\n".join(lines) + "\n", diffs


def _layer_names(layers: Sequence[Layer]) -> str:
    return " -> ".join(layer.name for layer in layers)


def run(
    directories: Sequence[Path],
    output: Path,
    *,
    compiler: str,
    cpp: str = DEFAULT_CPP,
    seed: int = 0,
    timeout_ms: int = SOLVER_TIMEOUT_MS,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    per_file: int | None = MUTANTS_PER_FILE,
    harness_per_file: int | None = HARNESS_PER_FILE,
    python: str = sys.executable,
    layer_timeout_s: int = LAYER_TIMEOUT_S,
    baseline: Path = ORACLE_BASELINE,
    work_dir: Path | None = None,
    targets: str = "all",
    select_all: bool = False,
) -> bool:
    """Generate, isolate and run every mutant through its layers; seal the kill matrix.

    `select_all` is the disclosed extension of the pre-registered campaign: the whole distinct population of
    every target under the extended operator family (`ALL_OPERATORS`) instead of a seeded sample of the
    pre-registered one. `targets="extended"` runs the three additional trusted files as their own group.
    """
    del directories, timeout_ms  # mutation targets are fixed files, not kernel directories
    if targets not in TARGET_GROUPS:
        raise ValueError(f"targets must be one of {', '.join(TARGET_GROUPS)}")
    operators: tuple[str, ...] = ALL_OPERATORS if select_all else OPERATORS
    if select_all:
        per_file = harness_per_file = None
    compiler_mutants = (
        generate(ROOT, COMPILER_TARGETS, seed=seed, per_file=per_file, operators=operators)
        if targets in ("all", "compiler")
        else ()
    )
    harness_mutants = (
        generate(ROOT, HARNESS_TARGETS, seed=seed, per_file=harness_per_file, operators=operators)
        if targets in ("all", "harness")
        else ()
    )
    extended_mutants = (
        generate(ROOT, EXTENDED_TARGETS, seed=seed, per_file=per_file, operators=operators)
        if targets == "extended"
        else ()
    )
    plan = (
        [("compiler", m, COMPILER_LAYERS) for m in compiler_mutants]
        + [("harness", m, HARNESS_LAYERS) for m in harness_mutants]
        + [("compiler-extended", m, EXTENDED_LAYERS) for m in extended_mutants]
    )
    if dry_run:
        for group, mutant, _ in plan:
            print(f"{group} {mutant.identifier} {mutant.target}:{mutant.line} {mutant.operator}: {mutant.description}")
        print(f"compiler layers: {_layer_names(COMPILER_LAYERS)}; harness layers: {_layer_names(HARNESS_LAYERS)}")
        print(f"operators: {', '.join(operators)}; selection: {'whole population' if select_all else 'seeded sample'}")
        print(f"Preview: seed {seed}, {len(plan)} mutants, layer timeout {layer_timeout_s} s.")
        print("No files were written.")
        return True
    cases = oracle_baseline(baseline) if (baseline / "manifest.json").is_file() else ()
    campaign_run = campaign.start(
        "mutate",
        output,
        compiler=compiler,
        cpp=cpp,
        seed=seed,
        settings={
            "operators": list(operators),
            "preregistered_operators": list(OPERATORS),
            "select_all": select_all,
            "compiler_targets": list(COMPILER_TARGETS),
            "harness_targets": list(HARNESS_TARGETS),
            "extended_targets": list(EXTENDED_TARGETS),
            "mutants_per_file": per_file,
            "harness_mutants_per_file": harness_per_file,
            "population": {
                group: population_size(ROOT, files, operators=operators)
                for group, files in (
                    ("compiler", COMPILER_TARGETS),
                    ("harness", HARNESS_TARGETS),
                    ("compiler-extended", EXTENDED_TARGETS),
                )
                if any(g == group for g, _, _ in plan)
            },
            "candidate_digest": candidate_digest([*compiler_mutants, *harness_mutants, *extended_mutants]),
            "compiler_layers": [asdict(layer) for layer in COMPILER_LAYERS],
            "harness_layers": [asdict(layer) for layer in HARNESS_LAYERS],
            "extended_layers": [asdict(layer) for layer in EXTENDED_LAYERS],
            "layer_timeout_s": layer_timeout_s,
            "oracle_baseline": str(baseline.relative_to(ROOT)) if baseline.is_relative_to(ROOT) else str(baseline),
            "copied_paths": list(COPY_PATHS),
            "linked_paths": list(LINK_PATHS),
            "targets": targets,
            "python": python,
            "target_score": TARGET_SCORE,
        },
        schema=SCHEMA,
    )
    work = Path(tempfile.mkdtemp(prefix="pfc-mutate-", dir=work_dir))
    try:
        if progress:
            progress("mutate: baseline (unmutated copy)")
        clean = isolated_copy(ROOT, work / "baseline", None)
        groups = {"compiler": COMPILER_LAYERS, "harness": HARNESS_LAYERS, "compiler-extended": EXTENDED_LAYERS}
        present = {g for g, _, _ in plan}
        baseline_results = {
            group: _run_all(clean, layers, python, compiler, cases, layer_timeout_s)
            for group, layers in groups.items()
            if group in present
        }
        shutil.rmtree(clean)
        campaign_run.evidence.write_text(
            "baseline.json",
            json.dumps({g: {n: asdict(r) for n, r in rs.items()} for g, rs in baseline_results.items()}, indent=2)
            + "\n",
        )
        failing = [f"{g}/{n}" for g, rs in baseline_results.items() for n, r in rs.items() if r.status != "passed"]
        if failing:
            raise ValueError(f"layers fail on the unmutated copy, no kill is meaningful: {', '.join(failing)}")
        all_rows: list[dict[str, object]] = []
        for group, mutant, layers in plan:
            if progress:
                progress(f"mutate: {group} {mutant.identifier} ({mutant.target}:{mutant.line} {mutant.operator})")
            copy = isolated_copy(ROOT, work / mutant.identifier, mutant)
            results = _run_stack(copy, layers, python=python, compiler=compiler, cases=cases, timeout_s=layer_timeout_s)
            shutil.rmtree(copy)
            campaign_run.evidence.write_text(f"mutants/{mutant.identifier}.diff", mutant.diff)
            campaign_run.evidence.write_text(
                f"mutants/{mutant.identifier}.json",
                json.dumps({name: asdict(r) for name, r in results.items()}, indent=2) + "\n",
            )
            for row in rows(group, mutant, layers, results):
                campaign_run.evidence.record(
                    "layer", Path(mutant.target).stem, record_id(mutant, str(row["layer"])), row
                )
                all_rows.append(row)
        index, diffs = survivor_index([m for _, m, _ in plan], all_rows)
        campaign_run.evidence.write_text("survivors/INDEX.md", index)
        for name, text in diffs.items():
            campaign_run.evidence.write_text(name, text)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    protected = evidence.verify_inventory(ROOT, ROOT / "validation" / "freeze" / "protected.sha256")
    if not protected.verified:
        raise ValueError(f"protected inventory drifted during the campaign: {', '.join(protected.mismatched)}")
    summary = summarize(all_rows)
    summary["repository_untouched"] = True
    summary["baseline_passed"] = True
    campaign.finish(campaign_run, summary, render_table(summary), derive_rows)
    return bool(summary["passed"])


def _run_all(
    copy: Path, layers: Sequence[Layer], python: str, compiler: str, cases: Sequence[OracleCase], timeout_s: int
) -> dict[str, LayerResult]:
    results: dict[str, LayerResult] = {}
    for layer in layers:
        if layer.name == "oracle":
            results[layer.name] = run_oracle_layer(copy, cases, python=python, compiler=compiler, timeout_s=timeout_s)
        else:
            results[layer.name] = run_layer(copy, layer, python=python, timeout_s=timeout_s)
    return results


def command(arguments: object) -> int:
    return campaign.run_command(vars(arguments), run)
