"""Paper tables and figure data from sealed evidence directories (roadmap E13, architecture D9, EV-R8).

Everything is derived from `observations.jsonl` of sealed directories listed in `paper/evidence.json`
(validated by `evidence.load`, so a tampered or incomplete directory cannot feed a table) plus the sealed WP
oracle summary. Outputs are plain Markdown and CSV texts, sorted and rounded, so two consecutive builds are
byte-identical. No table reads `tables.md` of a campaign; those renderings are for humans.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pfc_eval import evidence, runtime, stats

STRATA = ("development", "ported", "obfuscated", "composed", "heldout")
CONDITIONS = ("L1", "L2", "L3")
REFERENCE_BUDGET_S = 10
ATTEMPTS = 5
Row = Mapping[str, object]


@dataclass(frozen=True)
class Entry:
    path: str
    stratum: str


@dataclass(frozen=True)
class Config:
    seed: int
    directories: dict[str, tuple[Entry, ...]]
    oracle: str | None


def load_config(path: Path) -> Config:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("seed")) is not int or not isinstance(data.get("evidence"), dict):
        raise ValueError("paper/evidence.json needs an integer seed and an evidence mapping")
    directories: dict[str, tuple[Entry, ...]] = {}
    for experiment, items in data["evidence"].items():
        entries = []
        for item in items:
            if isinstance(item, str):
                entries.append(Entry(item, "development"))
            else:
                stratum = str(item.get("stratum", "development"))
                if stratum not in STRATA:
                    raise ValueError(f"unknown stratum {stratum!r}")
                entries.append(Entry(str(item["path"]), stratum))
        directories[str(experiment)] = tuple(entries)
    oracle = data.get("oracle")
    return Config(data["seed"], directories, str(oracle) if oracle else None)


# --- rendering helpers -------------------------------------------------------------------------------


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def to_csv(rows: Sequence[Row]) -> str:
    if not rows:
        return "\n"
    columns = list(rows[0])
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(_cell(row.get(column)).replace(",", ";") for column in columns))
    return "\n".join(lines) + "\n"


def to_markdown(rows: Sequence[Row], *, title: str, note: str = "") -> str:
    if not rows:
        return f"# {title}\n\n(no rows)\n"
    columns = list(rows[0])
    lines = [f"# {title}", "", "| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)]
    lines += ["| " + " | ".join(_cell(row.get(column)) for column in columns) + " |" for row in rows]
    if note:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def _interval(value: Mapping[str, object], low: str = "low", high: str = "high") -> str:
    return f"{float(str(value['estimate'])):.3f} [{float(str(value[low])):.3f}, {float(str(value[high])):.3f}]"


# --- loading ------------------------------------------------------------------------------------------


def load_rows(root: Path, entries: Sequence[Entry]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entry in entries:
        loaded = evidence.load(root / entry.path)
        for row in loaded.observations:
            rows.append({**row, "stratum": entry.stratum, "source_dir": entry.path})
    return rows


def _kernel_stratum(config: Config, root: Path) -> dict[str, str]:
    strata: dict[str, str] = {}
    for entries in config.directories.values():
        for entry in entries:
            for row in evidence.load(root / entry.path).observations:
                if "kernel" in row:
                    strata.setdefault(str(row["kernel"]), entry.stratum)
    return strata


# --- proposer analysis ---------------------------------------------------------------------------------


def check_protocols(rows: Sequence[Row]) -> None:
    """Every (condition, stratum) cell of Table IV must come from one prompt protocol (AMENDMENTS item 8).

    Mixing protocol versions inside a cell would average two different instructions; the build refuses it
    instead of averaging silently. Different cells may legitimately use different protocols, which the
    `protocol` column of every row keeps visible.
    """
    seen: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        seen.setdefault((str(row["condition"]), str(row["stratum"])), set()).add(str(row.get("protocol", "?")))
    mixed = {cell: sorted(protocols) for cell, protocols in seen.items() if len(protocols) > 1}
    if mixed:
        detail = "; ".join(f"{c}/{s}: {', '.join(p)}" for (c, s), p in sorted(mixed.items()))
        raise ValueError(f"mixed proposer protocols inside one condition and stratum: {detail}")


def _runs(rows: Sequence[Row]) -> int:
    return max((int(str(r["run"])) for r in rows), default=1)


def success_at_k(rows: Sequence[Row], *, runs: int) -> dict[tuple[str, str], dict[str, float]]:
    """success@R per task for every (condition, stratum): fraction of runs with an accepted attempt."""
    result: dict[tuple[str, str], dict[str, float]] = {}
    per_run: dict[tuple[str, str, str, int], bool] = {}
    for r in rows:
        key = (str(r["condition"]), str(r["stratum"]), str(r["kernel"]), int(str(r["run"])))
        per_run[key] = per_run.get(key, False) or bool(r["success"])
    for condition, stratum, kernel, _ in sorted(per_run):
        group = result.setdefault((condition, stratum), {})
        if kernel not in group:
            group[kernel] = sum(per_run.get((condition, stratum, kernel, n), False) for n in range(1, runs + 1)) / runs
    return result


def success_within_k(
    rows: Sequence[Row], *, runs: int, attempts: int = ATTEMPTS
) -> dict[tuple[str, str], dict[int, float]]:
    """Figure 3: fraction of (task, run) pairs solved within the first k attempts."""
    first: dict[tuple[str, str, str, int], int | None] = {}
    for r in rows:
        key = (str(r["condition"]), str(r["stratum"]), str(r["kernel"]), int(str(r["run"])))
        first.setdefault(key, None)
        if r["success"]:
            attempt = int(str(r["attempt"]))
            current = first[key]
            first[key] = attempt if current is None else min(current, attempt)
    result: dict[tuple[str, str], dict[int, float]] = {}
    groups: dict[tuple[str, str], list[int | None]] = {}
    for (condition, stratum, _, _), value in first.items():
        groups.setdefault((condition, stratum), []).append(value)
    for group, values in sorted(groups.items()):
        total = len(values)
        result[group] = {k: sum(1 for v in values if v is not None and v <= k) / total for k in range(1, attempts + 1)}
    return result


# RQ3 tests only these contrasts, and only on the sealed held-out stratum (experimental-design.md §E3 / §5.5;
# AMENDMENTS 25 may add "L1 - B2"). Every other (contrast, stratum) pair is exploratory and is Holm-corrected
# as its own family; `p_holm_all` corrects over every pair as a sensitivity column.
PREREGISTERED_CONTRASTS: tuple[str, ...] = ("L1 - B1", "L1 - B2", "L1 - L2", "L1 - L3")  # AMENDMENTS 32 adds B2
PRIMARY_STRATUM = "heldout"


def _proportion(successes: int, trials: int) -> dict[str, object]:
    """Wilson and Clopper-Pearson intervals of a raw proportion, reported beside a bootstrap at the ceiling."""
    wilson = stats.wilson(successes, trials)
    exact = stats.clopper_pearson(successes, trials)
    return {
        "successes": successes,
        "trials": trials,
        "wilson_low": wilson.low,
        "wilson_high": wilson.high,
        "cp_low": exact.low,
        "cp_high": exact.high,
    }


def proposer_table(
    per_task: Mapping[tuple[str, str], Mapping[str, float]],
    proposer: Sequence[Row],
    b1: Mapping[str, Mapping[str, float]],
    *,
    runs: int,
    seed: int,
    b2: Mapping[str, Mapping[str, float]] | None = None,
) -> list[dict[str, object]]:
    """Table IV: success@R per condition and stratum.

    The cluster bootstrap over tasks is the primary interval; when every task sits at 0 or 1 it degenerates
    to a point (`ceiling`), so the raw proportion's Wilson and Clopper-Pearson intervals are reported beside
    it. The proportion is over tasks for every condition: for L1/L2 a task counts as a success only when every
    run admitted an invariant (runs of one task are not independent, so (task, run) pairs are not pooled).
    """
    table: list[dict[str, object]] = []
    baselines = [("B1", b1)] + ([("B2", b2)] if b2 else [])
    for stratum in sorted({s for _, by_stratum in baselines for s in by_stratum}):
        for condition, by_stratum in baselines:
            outcomes = by_stratum.get(stratum)
            if not outcomes:
                continue
            bootstrap = stats.cluster_bootstrap({k: [v] for k, v in outcomes.items()}, stats.mean, seed=seed)
            table.append(
                {
                    "condition": condition,
                    "stratum": stratum,
                    "tasks": len(outcomes),
                    "runs": 1,
                    "mean_success": bootstrap.estimate,
                    "low": bootstrap.interval.low,
                    "high": bootstrap.interval.high,
                    "attempts_to_first_success": None,
                    "tokens_per_success": None,
                    **_proportion(int(round(sum(outcomes.values()))), len(outcomes)),
                    "ceiling": bootstrap.interval.low == bootstrap.interval.high,
                }
            )
    for (condition, stratum), values in sorted(per_task.items()):
        mine = [r for r in proposer if r["condition"] == condition and r["stratum"] == stratum]
        bootstrap = stats.cluster_bootstrap({k: [v] for k, v in values.items()}, stats.mean, seed=seed)
        successes = sum(bool(r["success"]) for r in mine)
        tokens = sum(int(str(r["tokens_in"] or 0)) + int(str(r["tokens_out"] or 0)) for r in mine)
        table.append(
            {
                "condition": condition,
                "stratum": stratum,
                "tasks": len(values),
                "runs": runs,
                "mean_success": bootstrap.estimate,
                "low": bootstrap.interval.low,
                "high": bootstrap.interval.high,
                "attempts_to_first_success": (
                    stats.mean([int(str(r["attempt"])) for r in mine if r["success"]]) if successes else None
                ),
                "tokens_per_success": tokens / successes if successes else None,
                **_proportion(sum(v >= 1.0 for v in values.values()), len(values)),
                "ceiling": bootstrap.interval.low == bootstrap.interval.high,
            }
        )
    return table


def baseline_by_stratum(rows: Sequence[Row], family: str) -> dict[str, dict[str, float]]:
    """Task outcome of one non-LLM baseline family (b1 templates, b2 dynamic inference) per campaign stratum.

    Rows sealed before the `family` column existed are B1 rows.
    """
    result: dict[str, dict[str, float]] = {}
    for r in rows:
        if str(r.get("family", "b1")) != family:
            continue
        result.setdefault(str(r["stratum"]), {})[str(r["kernel"])] = 1.0 if r["task_accepted"] else 0.0
    return result


def b1_by_stratum(rows: Sequence[Row]) -> dict[str, dict[str, float]]:
    """B1 (Houdini) task outcome per stratum of the campaign that produced it: 1.0 admitted, 0.0 otherwise."""
    return baseline_by_stratum(rows, "b1")


def contrasts(
    per_task: Mapping[tuple[str, str], Mapping[str, float]],
    b1: Mapping[str, Mapping[str, float]],
    *,
    seed: int,
    b2: Mapping[str, Mapping[str, float]] | None = None,
) -> list[dict[str, object]]:
    """Contrasts L1 − B1, L1 − B2, L1 − L2, L1 − L3 on common tasks, cluster bootstrap, sign test, Holm by family.

    `b1` is keyed by stratum, so a baseline run on the ported loops is compared only with the ported L1 cell.
    A pair is `preregistered` when its contrast is in PREREGISTERED_CONTRASTS and its stratum is the primary
    (held-out) one; Holm runs inside that family (`p_holm`, size `holm_m`), separately inside the exploratory
    family, and over every pair as the sensitivity column `p_holm_all`. `ceiling` marks a degenerate bootstrap.
    """
    results: list[dict[str, object]] = []
    groups = sorted({stratum for _, stratum in per_task})
    pending: list[tuple[str, str, dict[str, float]]] = []
    for stratum in groups:
        l1 = per_task.get(("L1", stratum))
        if not l1:
            continue
        others: list[tuple[str, Mapping[str, float]]] = [("B1", b1.get(stratum, {}))]
        if b2:
            others.append(("B2", b2.get(stratum, {})))
        others += [(c, per_task.get((c, stratum), {})) for c in ("L2", "L3")]
        for name, values in others:
            common = sorted(set(l1) & set(values))
            if common:
                pending.append((f"L1 - {name}", stratum, {task: l1[task] - values[task] for task in common}))
    p_values = []
    for name, stratum, differences in pending:
        clusters = {task: [difference] for task, difference in differences.items()}
        bootstrap = stats.cluster_bootstrap(clusters, stats.mean, seed=seed)
        sign = stats.sign_test(list(differences.values()))
        p_values.append(sign.p_value)
        results.append(
            {
                "contrast": name,
                "stratum": stratum,
                "tasks": len(differences),
                "estimate": bootstrap.estimate,
                "low": bootstrap.interval.low,
                "high": bootstrap.interval.high,
                "positive": sign.positive,
                "negative": sign.negative,
                "ties": sign.ties,
                "p_sign": sign.p_value,
                "family": (
                    "preregistered" if name in PREREGISTERED_CONTRASTS and stratum == PRIMARY_STRATUM else "exploratory"
                ),
                "ceiling": bootstrap.interval.low == bootstrap.interval.high,
            }
        )
    for family in ("preregistered", "exploratory"):
        members = [r for r in results if r["family"] == family]
        corrected = stats.holm([float(str(r["p_sign"])) for r in members]) if members else ()
        for result, p in zip(members, corrected, strict=True):
            result["holm_m"] = len(members)
            result["p_holm"] = p
    for result, p in zip(results, stats.holm(p_values) if p_values else (), strict=True):
        result["p_holm_all"] = p
    return results


# --- mutation ------------------------------------------------------------------------------------------


EQUIVALENTS_PATH = Path("pfc_eval") / "equivalents.json"


def load_equivalents(root: Path) -> dict[str, dict[str, str]]:
    """Hand-classified survivors keyed `<campaign>/<mutant>`; the `_comment` key is documentation."""
    path = root / EQUIVALENTS_PATH
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("equivalents.json must be an object")
    return {str(k): {str(a): str(b) for a, b in v.items()} for k, v in data.items() if k != "_comment"}


def mutation_scores(
    rows: Sequence[Row], equivalents: Mapping[str, Mapping[str, str]], *, root: Path
) -> list[dict[str, object]]:
    """Score per (campaign, group) with and without hand-classified equivalents.

    An equivalents entry must name a mutant of that campaign and carry the SHA-256 of its sealed diff; a stale or
    unknown entry raises instead of silently lowering a denominator.
    """
    scores: list[dict[str, object]] = []
    campaigns = sorted({str(r["source_dir"]) for r in rows})
    for source_dir in campaigns:
        name = Path(source_dir).name
        mine = [r for r in rows if r["source_dir"] == source_dir]
        known = {str(r["mutant_id"]) for r in mine}
        classified: set[str] = set()
        for key, entry in equivalents.items():
            campaign, _, mutant = key.partition("/")
            if campaign != name:
                continue
            if mutant not in known:
                raise ValueError(f"equivalents.json names {mutant} which is not a mutant of {name}")
            diff = (root / source_dir / "mutants" / f"{mutant}.diff").read_bytes()
            if hashlib.sha256(diff).hexdigest() != entry.get("diff_sha256"):
                raise ValueError(f"equivalents.json digest of {name}/{mutant} does not match the sealed diff")
            classified.add(mutant)
        for group in sorted({str(r["group"]) for r in mine}):
            mutants = {str(r["mutant_id"]): bool(r["killed"]) for r in mine if r["group"] == group}
            killed = sum(mutants.values())
            equivalent = sum(1 for m in mutants if m in classified and not mutants[m])
            golden_only = len({str(r["mutant_id"]) for r in mine if r["group"] == group and r["golden_only"]})
            interval = stats.wilson(killed, len(mutants))
            denominator = len(mutants) - equivalent
            excluding = stats.wilson(killed, denominator) if denominator else stats.Interval(0.0, 1.0)
            scores.append(
                {
                    "campaign": name,
                    "group": group,
                    "mutants": len(mutants),
                    "killed": killed,
                    "golden_only": golden_only,
                    "equivalent": equivalent,
                    "score": killed / len(mutants),
                    "wilson_low": interval.low,
                    "wilson_high": interval.high,
                    "score_excluding_equivalent": killed / denominator if denominator else None,
                    "wilson_low_excluding": excluding.low,
                    "wilson_high_excluding": excluding.high,
                }
            )
    return scores


# --- oracle --------------------------------------------------------------------------------------------


def oracle_cells(cases: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """2 × 2 agreement per function and kind; pfc sites and WP properties are paired by kind, not by label."""
    cells: list[dict[str, object]] = []
    for case in cases:
        if case.get("kind") != "positive" or not isinstance(case.get("pfc"), Mapping):
            continue
        compared = case["compared"]
        sites = case["pfc"]["sites"]  # type: ignore[index]
        if not isinstance(compared, Mapping) or not isinstance(sites, list):
            continue
        for kind in ("bounds", "division"):
            pfc = sum(1 for s in sites if s["kind"] == kind and s["status"] == "proved")
            pfc_total = sum(1 for s in sites if s["kind"] == kind)
            wp_verdicts = [v for label, verdicts in compared.items() if str(label).startswith(kind) for v in verdicts]
            wp = sum(1 for v in wp_verdicts if v == "valid")
            if pfc_total == 0 and not wp_verdicts:
                continue
            both = min(pfc, wp)
            cells.append(
                {
                    "function": str(case["name"]),
                    "kind": kind,
                    "both": both,
                    "pfc_only": pfc - both,
                    "wp_only": wp - both,
                    "neither": max(pfc_total - pfc - (wp - both), 0),
                    "assessment": str(case.get("status")),
                }
            )
    return cells


def oracle_adjudication(cells: Sequence[Row], wp_rows: Sequence[Row]) -> list[dict[str, object]]:
    """Baseline 2 × 2 cells beside the cells recomputed with a second WP prover run (adjudication).

    For every function that the adjudication run covered, the WP side is replaced by the `property` goals of
    the run at its largest budget: a `pfc_only` cell moves to `both` only when the matching goal is `valid`.
    Functions without adjudication rows keep their baseline numbers. The baseline is never rewritten.
    """
    properties = [r for r in wp_rows if r.get("tool") == "wp" and r.get("kind") == "property"]
    result: list[dict[str, object]] = []
    for cell in cells:
        function, kind = str(cell["function"]), str(cell["kind"])
        mine = [r for r in properties if r["kernel"] == function and f"_assert_{kind}" in str(r["identifier"])]
        both, pfc_only, wp_only = int(str(cell["both"])), int(str(cell["pfc_only"])), int(str(cell["wp_only"]))
        row: dict[str, object] = {
            **cell,
            "both_adjudicated": both,
            "pfc_only_adjudicated": pfc_only,
            "wp_only_adjudicated": wp_only,
            "adjudicated": False,
            "budget_s": None,
        }
        if mine:
            budget = max(int(str(r["budget_s"])) for r in mine)
            # The baseline prover's proofs stand; the second prover can only add. Without per-goal identity in
            # the cells the union is approximated by the larger count (exact when one run's proofs include the other's).
            # Several adjudication campaigns are alternatives, never summed: the best one at the largest budget counts.
            campaigns = sorted({str(r.get("evidence_dir")) for r in mine})
            proved = max(
                sum(
                    r["status"] == "valid"
                    for r in mine
                    if str(r.get("evidence_dir")) == campaign and int(str(r["budget_s"])) == budget
                )
                for campaign in campaigns
            )
            valid = max(both + wp_only, proved)
            pfc_proved = both + pfc_only
            both_adjudicated = min(pfc_proved, valid)
            row.update(
                both_adjudicated=both_adjudicated,
                pfc_only_adjudicated=pfc_proved - both_adjudicated,
                wp_only_adjudicated=valid - both_adjudicated,
                adjudicated=both_adjudicated != both or valid - both_adjudicated != wp_only,
                budget_s=budget,
            )
        result.append(row)
    return result


def crosscheck_table(rows: Sequence[Row]) -> list[dict[str, object]]:
    """Table III (c): second-solver agreement per stratum and obligation category (advisory)."""
    table: list[dict[str, object]] = []
    for stratum in STRATA:
        for category in ("required", "reported", "entry"):
            mine = [r for r in rows if r["stratum"] == stratum and r["category"] == category]
            if mine:
                table.append(
                    {
                        "stratum": stratum,
                        "category": category,
                        "n": len(mine),
                        "agree": sum(r["agreement"] is True for r in mine),
                        "disagree": sum(r["agreement"] is False for r in mine),
                        "inconclusive": sum(r["agreement"] is None for r in mine),
                    }
                )
    return table


def negative_table(rows: Sequence[Row]) -> list[dict[str, object]]:
    """Table VII: expected-trap campaign per stratum, class and build (ADR-0058).

    `expected_traps` counts cases whose pre-registered expectation is a trap; `skipped_conjunct_cases` are precondition
    violations whose false conjuncts were all skipped by the entry check (expected not to trap: the disclosed gap);
    against `checked` the precondition class has no expectation and contributes no agreement.
    """
    table: list[dict[str, object]] = []
    for stratum in STRATA:
        for kind in ("precondition", "overflow"):
            for build in ("checked", "verified"):
                mine = [r for r in rows if r["stratum"] == stratum and r["class"] == kind and r["build"] == build]
                if not mine:
                    continue
                table.append(
                    {
                        "stratum": stratum,
                        "class": kind,
                        "build": build,
                        "cases": len(mine),
                        "expected_traps": sum(r["expected_trap"] is True for r in mine),
                        "skipped_conjunct_cases": sum(r["expected_trap"] is False for r in mine),
                        "traps": sum(r["observed"] == "sigill" for r in mine),
                        "agree": sum(r["agreement"] is True for r in mine),
                        "disagree": sum(r["agreement"] is False for r in mine),
                        "operational_failures": sum(r["observed"] not in ("sigill", "completed") for r in mine),
                    }
                )
    return table


def guards_tables(
    rows: Mapping[str, Sequence[Row]], *, seed: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Table VI: the guards endpoint of the sealed stratum only (pre-registered there; AMENDMENTS item 31)."""
    from pfc_eval import guards

    def held_out(experiment: str) -> list[Row]:
        return [
            r for r in rows.get(experiment, []) if r.get("stratum") == "heldout" and r.get("guards_total") is not None
        ]

    proposer, houdini, differential = held_out("proposer"), held_out("houdini"), held_out("differential")
    if not proposer and not houdini and not differential:
        return [], []
    table = guards.endpoint(proposer, houdini, differential, seed=seed)
    kernel_rows, contrast_rows = table["kernels"], table["contrasts"]
    assert isinstance(kernel_rows, list) and isinstance(contrast_rows, list)
    kernels = [{"stratum": "heldout", **r} for r in kernel_rows]
    contrasts = [dict(c) for c in contrast_rows]
    return kernels, contrasts


# --- build ---------------------------------------------------------------------------------------------


def budget_curve(sweep: Sequence[Row]) -> list[dict[str, object]]:
    """Figure 2: proved fraction against solver budget per tool and stratum (required pfc goals, non-smoke WP)."""
    curve: list[dict[str, object]] = []
    for tool in ("pfc", "wp"):
        for stratum in STRATA:
            for budget in sorted({int(str(r["budget_s"])) for r in sweep}):
                selected = [
                    r
                    for r in sweep
                    if r["tool"] == tool
                    and r["stratum"] == stratum
                    and int(str(r["budget_s"])) == budget
                    and not r.get("smoke")
                    and (tool == "wp" or r["category"] == "required")
                ]
                if selected:
                    proved = sum(r["status"] in ("proved", "valid") for r in selected)
                    curve.append(
                        {
                            "tool": tool,
                            "stratum": stratum,
                            "budget_s": budget,
                            "proved": proved,
                            "total": len(selected),
                            "fraction": proved / len(selected),
                        }
                    )
    return curve


def _sum(rows: Sequence[Row], column: str) -> int:
    return sum(int(str(r[column])) for r in rows)


def corpus_by_stratum(corpus: Sequence[Row]) -> list[dict[str, object]]:
    """Table I (b): the per-kernel corpus rows pooled by stratum."""
    pooled: list[dict[str, object]] = []
    for stratum in STRATA:
        mine = [r for r in corpus if r["stratum"] == stratum]
        if mine:
            pooled.append(
                {
                    "stratum": stratum,
                    "kernels": len(mine),
                    **{c: _sum(mine, c) for c in ("required_obligations", "reported_obligations")},
                    **{c: _sum(mine, c) for c in ("differential_cases", "rejected_cases")},
                }
            )
    return pooled


def soundness_by_stratum(soundness: Sequence[Row]) -> list[dict[str, object]]:
    """Table II (b): the per-kernel soundness rows pooled by stratum, with the bound recomputed on pooled N."""
    pooled: list[dict[str, object]] = []
    for stratum in STRATA:
        mine = [r for r in soundness if r["stratum"] == stratum]
        if not mine:
            continue
        cases, failures = _sum(mine, "N"), _sum(mine, "failures")
        pooled.append(
            {
                "stratum": stratum,
                "kernels": len(mine),
                "N": cases,
                "failures": failures,
                "failure_bound_95": stats.binomial_upper_bound(failures, cases) if cases else None,
                "breakit_conjuncts": _sum(mine, "breakit_conjuncts"),
                "guard_pairs": _sum_optional(mine, "guard_pairs"),
                "guard_distinct_sites": _sum_optional(mine, "guard_distinct_sites"),
                "sensitivity": _ratio(_sum(mine, "guards_restored"), _sum(mine, "guard_conjuncts")),
                "specificity": _ratio(_sum(mine, "none_identical"), _sum(mine, "none_conjuncts")),
                **{
                    c: _sum(mine, c)
                    for c in (
                        "restrict_withdrawn",
                        "injected",
                        "false_injected",
                        "false_rejected",
                        "entry_models",
                        "reproduced",
                        "unreconstructible",
                        "variants",
                        "brittle",
                        "divergent",
                    )
                },
            }
        )
    return pooled


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _sum_optional(rows: Sequence[Row], column: str) -> int | None:
    """Sum of an optional integer column; None when no row carries a value."""
    values = [int(str(r[column])) for r in rows if r.get(column) is not None]
    return sum(values) if values else None


def _site_tokens(rows: Sequence[Row]) -> list[str]:
    tokens: list[str] = []
    for row in rows:
        text = row.get("restored_sites")
        if isinstance(text, str):
            tokens.extend(token for token in text.split(";") if token)
    return tokens


def guard_sites(conjunct_rows: Sequence[Row], site_rows: Sequence[Row], kernel: str) -> dict[str, object]:
    """Conjunct–site pairs and distinct restored sites over the guard conjuncts of one kernel.

    A sealed `conjunct_sites` reconciliation (bound variants only) takes precedence; otherwise the
    `restored_sites` column of the conjuncts rows is used; campaigns predating both report None.
    """
    reconciled = [
        r for r in site_rows if r["kernel"] == kernel and r.get("expected") == "guard" and r.get("sealed_c_identical")
    ]
    if reconciled:
        tokens = _site_tokens(reconciled)
        return {"guard_pairs": len(tokens), "guard_distinct_sites": len(set(tokens)), "sites_source": "reconciled"}
    mine = [r for r in conjunct_rows if r["kernel"] == kernel and r.get("expected") == "guard"]
    if mine and all("restored_sites" in r for r in mine):
        tokens = _site_tokens(mine)
        return {"guard_pairs": len(tokens), "guard_distinct_sites": len(set(tokens)), "sites_source": "column"}
    return {"guard_pairs": None, "guard_distinct_sites": None, "sites_source": None}


def build(config: Config, *, root: Path) -> dict[str, str]:
    """Every table and figure data file, keyed by file name."""
    out: dict[str, str] = {}
    sources: list[dict[str, object]] = []
    for experiment, entries in sorted(config.directories.items()):
        for entry in entries:
            manifest = (root / entry.path / "manifest.json").read_bytes()
            sources.append(
                {
                    "experiment": experiment,
                    "path": entry.path,
                    "stratum": entry.stratum,
                    "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                }
            )
    if config.oracle:
        manifest = (root / config.oracle / "manifest.json").read_bytes()
        sources.append(
            {
                "experiment": "oracle",
                "path": config.oracle,
                "stratum": "development",
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            }
        )
    out["SOURCES.json"] = json.dumps({"seed": config.seed, "evidence": sources}, indent=2, sort_keys=True) + "\n"
    strata = _kernel_stratum(config, root)
    rows = {name: load_rows(root, config.directories.get(name, ())) for name in config.directories}

    # Table I: corpus summary from the sweep (obligations) and the differential campaign (cases).
    sweep = rows.get("sweep", [])
    differential = rows.get("differential", [])
    corpus: list[dict[str, object]] = []
    kernels = sorted({str(r["kernel"]) for r in sweep if r["tool"] == "pfc"} | {str(r["kernel"]) for r in differential})
    max_budget = max((int(str(r["budget_s"])) for r in sweep), default=0)
    for kernel in kernels:
        obligations = [
            r for r in sweep if r["tool"] == "pfc" and r["kernel"] == kernel and int(str(r["budget_s"])) == max_budget
        ]
        cases = [r for r in differential if r["kernel"] == kernel]
        corpus.append(
            {
                "kernel": kernel,
                "stratum": strata.get(kernel, "development"),
                "required_obligations": sum(r["category"] == "required" for r in obligations),
                "reported_obligations": sum(r["category"] == "reported" for r in obligations),
                "differential_cases": sum(bool(r["accepted"]) for r in cases),
                "rejected_cases": sum(not r["accepted"] for r in cases),
            }
        )
    out["table-I-corpus.md"] = (
        to_markdown(corpus, title="Table I — corpus summary")
        + "\n"
        + to_markdown(corpus_by_stratum(corpus), title="Table I (b) — corpus by stratum")
    )
    out["table-I-corpus.csv"] = to_csv(corpus)
    out["table-I-corpus-by-stratum.csv"] = to_csv(corpus_by_stratum(corpus))

    # Table II: soundness per kernel.
    conjuncts = rows.get("conjuncts", [])
    conjunct_sites = rows.get("conjunct_sites", [])
    inject = rows.get("inject", [])
    robustness = rows.get("robustness", [])
    soundness: list[dict[str, object]] = []
    for kernel in sorted(
        {str(r["kernel"]) for r in differential}
        | {str(r["kernel"]) for r in conjuncts}
        | {str(r["kernel"]) for r in inject}
    ):
        cases = [r for r in differential if r["kernel"] == kernel and r["accepted"]]
        failures = sum(not r["agreement"] for r in cases)
        mine = [r for r in conjuncts if r["kernel"] == kernel]
        guard = [r for r in mine if r["expected"] == "guard"]
        none = [r for r in mine if r["expected"] == "none"]
        injected = [r for r in inject if r["kernel"] == kernel]
        false = [r for r in injected if r["candidate_class"] == "false"]
        entry_models = [r for r in false if r["model_class"] == "entry"]
        variants = [r for r in robustness if r["kernel"] == kernel]
        soundness.append(
            {
                "kernel": kernel,
                "stratum": strata.get(kernel, "development"),
                "N": len(cases),
                "rejected_precondition": sum(
                    r["rejected_reason"] == "PreconditionViolated" for r in differential if r["kernel"] == kernel
                ),
                "rejected_overflow": sum(
                    r["rejected_reason"] == "Overflow" for r in differential if r["kernel"] == kernel
                ),
                "failures": failures,
                "failure_bound_95": stats.binomial_upper_bound(failures, len(cases)) if cases else None,
                "breakit_conjuncts": len(mine),
                "guard_conjuncts": len(guard),
                "guards_restored": sum(bool(r["guard_restored"]) for r in guard),
                **{k: v for k, v in guard_sites(mine, conjunct_sites, kernel).items() if k != "sites_source"},
                "none_conjuncts": len(none),
                "none_identical": sum(bool(r["verdicts_identical"]) and bool(r["c_identical"]) for r in none),
                "sensitivity": _ratio(sum(bool(r["guard_restored"]) for r in guard), len(guard)),
                "specificity": _ratio(
                    sum(bool(r["verdicts_identical"]) and bool(r["c_identical"]) for r in none), len(none)
                ),
                "restrict_withdrawn": sum(bool(r["restrict_withdrawn"]) for r in mine),
                "injected": len(injected),
                "false_injected": len(false),
                "false_rejected": sum(not r["admitted"] for r in false),
                "entry_models": len(entry_models),
                "reproduced": sum(r["reproduction"] == "reproduced" for r in entry_models),
                "unreconstructible": sum(r["reproduction"] == "unreconstructible" for r in entry_models),
                "variants": len(variants),
                "brittle": sum(bool(r["brittle"]) for r in variants),
                "divergent": sum(bool(r["divergent"]) for r in variants),
            }
        )
    soundness_note = (
        "failure_bound_95: exact one-sided binomial upper bound on the failure probability; specificity over "
        "conjuncts expected to change nothing (`expected = none`); sensitivity over conjuncts a guard depends on; "
        "guard_pairs / guard_distinct_sites: conjunct–site pairs and distinct guard sites restored by those conjuncts "
        "(from the sealed reconciliation when present), so conjunct counts are never read as site counts."
    )
    out["table-II-soundness.md"] = (
        to_markdown(soundness, title="Table II — soundness per kernel", note=soundness_note)
        + "\n"
        + to_markdown(
            soundness_by_stratum(soundness),
            title="Table II (b) — soundness pooled by stratum",
            note="The bound is recomputed on the pooled N; ratios pool numerators and denominators.",
        )
    )
    out["table-II-soundness.csv"] = to_csv(soundness)
    out["table-II-soundness-by-stratum.csv"] = to_csv(soundness_by_stratum(soundness))

    # Table III: verifier results by stratum and kind at the reference budget, plus the oracle 2 × 2.
    verifier: list[dict[str, object]] = []
    reference = [r for r in sweep if int(str(r["budget_s"])) == REFERENCE_BUDGET_S]
    for tool in ("pfc", "wp"):
        mine = [r for r in reference if r["tool"] == tool and not r.get("smoke")]
        for stratum in STRATA:
            for kind in sorted({str(r["kind"]) for r in mine}):
                selected = [
                    r for r in mine if r["kind"] == kind and strata.get(str(r["kernel"]), "development") == stratum
                ]
                if not selected:
                    continue
                statuses = Counter(str(r["status"]) for r in selected)
                verifier.append(
                    {
                        "tool": tool,
                        "stratum": stratum,
                        "kind": kind,
                        "total": len(selected),
                        "proved": statuses.get("proved", 0) + statuses.get("valid", 0),
                        "failed": statuses.get("failed", 0) + statuses.get("invalid", 0),
                        "timeout": statuses.get("timeout", 0),
                        "unknown": statuses.get("unknown", 0),
                        "other": len(selected)
                        - sum(
                            statuses.get(s, 0) for s in ("proved", "valid", "failed", "invalid", "timeout", "unknown")
                        ),
                    }
                )
    text = to_markdown(verifier, title=f"Table III — verifier results by stratum and kind at {REFERENCE_BUDGET_S} s")
    if config.oracle:
        summary = json.loads((root / config.oracle / "summary.json").read_text(encoding="utf-8"))
        cells = oracle_cells(summary if isinstance(summary, list) else [])
        text += "\n" + to_markdown(
            cells,
            title="Table III (b) — pfc/WP agreement cells per function and kind (baseline oracle)",
            note="Pairing is by kind within a function; `pfc_only` counts pfc proofs without a valid WP goal "
            "(adjudicated in docs/ORACLE-FRAMA-C.md: matvec nonlinear bounds, WP timeout).",
        )
        out["table-III-oracle.csv"] = to_csv(cells)
        wp_z3 = rows.get("wp_z3", [])
        if wp_z3:
            adjudicated = oracle_adjudication(cells, wp_z3)
            text += "\n" + to_markdown(
                adjudicated,
                title="Table III (b′) — the same cells adjudicated with a second WP prover run",
                note="Functions covered by the adjudication run take its `property` goals at its largest budget; a "
                "`pfc_only` cell moves to `both` only when the matching goal is valid. The baseline oracle is "
                "unchanged.",
            )
            out["table-III-oracle-adjudicated.csv"] = to_csv(adjudicated)
    crosscheck_rows = rows.get("crosscheck", [])
    if crosscheck_rows:
        agreement = crosscheck_table(crosscheck_rows)
        text += "\n" + to_markdown(
            agreement,
            title="Table III (c) — second-solver (cvc5) agreement per stratum and category",
            note="Advisory: cvc5 grants no removal authority; agreement is a second witness of the same exported "
            "formula, a disagreement is a red flag listed in the campaign summary.",
        )
        out["table-III-crosscheck.csv"] = to_csv(agreement)
    out["table-III-verifier.md"] = text
    out["table-III-verifier.csv"] = to_csv(verifier)

    # Figure 2: proved fraction against budget, per tool and stratum.
    out["figure-2-sweep.csv"] = to_csv(budget_curve(sweep))

    # Table VII: expected-trap campaign (emitter v5 entry checks and overflow traps; ADR-0058).
    negative_rows = rows.get("negative", [])
    if negative_rows:
        expected = negative_table(negative_rows)
        out["table-VII-negative.md"] = to_markdown(
            expected,
            title="Table VII — expected traps: interpreter-rejected inputs against the instrumented builds",
            note="Overflow cases must trap in both builds; a precondition violation traps in verified iff a rendered "
            "entry-check conjunct is false (skipped-conjunct cases are the disclosed gap); against checked the "
            "precondition class is context only. Compile, timeout and other-signal outcomes are never agreement.",
        )
        out["table-VII-negative.csv"] = to_csv(expected)

    # Proposer: Table IV, Table VIII, Figures 3, 4, 7.
    proposer = rows.get("proposer", [])
    check_protocols(proposer)
    houdini = rows.get("houdini", [])
    runs = _runs(proposer)
    per_task = success_at_k(proposer, runs=runs)
    b1 = baseline_by_stratum(houdini, "b1")
    b2 = baseline_by_stratum(houdini, "b2")
    table_iv = proposer_table(per_task, proposer, b1, runs=runs, seed=config.seed, b2=b2)
    planned = contrasts(per_task, b1, seed=config.seed, b2=b2)
    out["table-IV-proposer.md"] = (
        to_markdown(
            table_iv,
            title=f"Table IV — success@{runs} per condition and stratum",
            note="ceiling: the cluster bootstrap over tasks is a point because every task sits at 0 or 1; the raw "
            "proportion's Wilson and Clopper-Pearson intervals over tasks (successes/trials; for L1/L2 a task "
            "counts only when every run succeeded) are reported beside it.",
        )
        + "\n"
        + to_markdown(
            planned,
            title="Table IV (b) — contrasts (paired by task; cluster bootstrap, sign test, Holm by family)",
            note="family: preregistered = a planned contrast on the held-out stratum (Holm over that family, size "
            "holm_m); exploratory = every other (contrast, stratum) pair, Holm over the exploratory family; "
            "p_holm_all corrects over every pair (sensitivity). ceiling: degenerate bootstrap interval.",
        )
    )
    out["table-IV-proposer.csv"] = to_csv(table_iv)
    out["table-IV-contrasts.csv"] = to_csv(planned)
    classes: list[dict[str, object]] = []
    for (condition, stratum), _ in sorted(per_task.items()):
        mine = [r for r in proposer if r["condition"] == condition and r["stratum"] == stratum and not r["success"]]
        for name, count in sorted(Counter(str(r["rejection_class"]) for r in mine).items()):
            classes.append({"condition": condition, "stratum": stratum, "rejection_class": name, "count": count})
    out["table-VIII-rejections.md"] = to_markdown(
        classes, title="Table VIII — rejection classes per condition and stratum"
    )
    out["table-VIII-rejections.csv"] = to_csv(classes)
    figure3: list[dict[str, object]] = []
    for (condition, stratum), curve_values in sorted(success_within_k(proposer, runs=runs).items()):
        for k, fraction in curve_values.items():
            figure3.append({"condition": condition, "stratum": stratum, "k": k, "fraction": fraction})
    out["figure-3-success-at-k.csv"] = to_csv(figure3)
    figure4: list[dict[str, object]] = []
    for (condition, stratum), values in sorted(per_task.items()):
        if condition != "L1":
            continue
        for label, by_stratum in (("B1", b1), ("B2", b2)):
            baseline = by_stratum.get(stratum, {})
            for task, value in sorted(values.items()):
                if task in baseline:
                    figure4.append(
                        {
                            "stratum": stratum,
                            "task": task,
                            "contrast": f"L1 - {label}",
                            "difference": value - baseline[task],
                        }
                    )
        for task, value in sorted(values.items()):
            other = per_task.get(("L2", stratum), {})
            if task in other:
                figure4.append(
                    {"stratum": stratum, "task": task, "contrast": "L1 - L2", "difference": value - other[task]}
                )
    out["figure-4-paired-differences.csv"] = to_csv(figure4)
    figure7 = [
        {
            "condition": row["condition"],
            "stratum": row["stratum"],
            "mean_success": row["mean_success"],
            "low": row["low"],
            "high": row["high"],
        }
        for row in table_iv
    ]
    out["figure-7-strata.csv"] = to_csv(figure7)

    # Table VI: guards endpoint of the sealed stratum (only when held-out campaigns with guard columns exist).
    guard_kernels, guard_contrasts = guards_tables(rows, seed=config.seed)
    if guard_kernels:
        out["table-VI-guards.md"] = (
            to_markdown(
                guard_kernels,
                title="Table VI — fraction of executable guard sites removed per kernel and condition (held-out)",
                note="denominator: guard sites of the checked emission; numerator: sites removed in the best emitted "
                "build of the condition (mean over runs for L1/L2; manual = hand invariant).",
            )
            + "\n"
            + to_markdown(
                guard_contrasts,
                title="Table VI (b) — pre-registered paired contrasts on the guards endpoint (Holm over the family)",
                note="primary: L2 - B2 (AMENDMENTS item 31; minimum meaningful gain 0.20 with the interval "
                "excluding 0).",
            )
        )
        out["table-VI-guards.csv"] = to_csv(guard_kernels)
        out["table-VI-guards-contrasts.csv"] = to_csv(guard_contrasts)

    # Table V and Figure 5: runtime.
    runtime_rows = rows.get("runtime", [])
    if runtime_rows:
        governor = "unavailable"
        cpu: int | None = None
        for entry in config.directories.get("runtime", ()):
            settings = evidence.load(root / entry.path).configuration["settings"]
            if isinstance(settings, Mapping):
                governor = str(settings.get("governor", governor))
                pinned = settings.get("cpu")
                cpu = int(str(pinned)) if pinned is not None else None
        summary = runtime.summarize(runtime_rows, seed=config.seed, governor=governor, cpu=cpu)
        table_v: list[dict[str, object]] = []
        figure5: list[dict[str, object]] = []
        kernels_summary: Mapping[str, Mapping[str, object]] = summary["kernels"]  # type: ignore[assignment]
        for kernel, data in kernels_summary.items():
            medians: Mapping[str, float] = data["medians_ms"]  # type: ignore[assignment]
            iqr: Mapping[str, float] = data["iqr_ms"]  # type: ignore[assignment]
            vectorized: Mapping[str, bool] = data["vectorized"]  # type: ignore[assignment]
            for variant, value in medians.items():
                table_v.append(
                    {
                        "kernel": kernel,
                        "variant": variant,
                        "median_ms": value,
                        "iqr_ms": iqr[variant],
                        "vectorized": vectorized.get(variant),
                    }
                )
            ratios: Mapping[str, Mapping[str, object]] = data["ratios"]  # type: ignore[assignment]
            for contrast, value_ in ratios.items():
                figure5.append(
                    {
                        "kernel": kernel,
                        "contrast": contrast,
                        "estimate": value_["estimate"],
                        "low": value_["low"],
                        "high": value_["high"],
                    }
                )
        note = (
            f"Run label: {summary['label']} (governor {summary['governor']}); counter tool: "
            f"{summary['counter_tool']}. Geometric ratios paired by (block, run)."
        )
        out["table-V-runtime.md"] = (
            to_markdown(table_v, title="Table V — runtime per kernel and variant (absolute medians)", note=note)
            + "\n"
            + to_markdown(figure5, title="Table V (b) — ratios with 95% bootstrap intervals")
        )
        out["table-V-runtime.csv"] = to_csv(table_v)
        out["figure-5-runtime-ratios.csv"] = to_csv(figure5)

    # Figure 6: kill matrix.
    mutate_rows = rows.get("mutate", [])
    if mutate_rows:
        equivalents = load_equivalents(root)
        scores = mutation_scores(mutate_rows, equivalents, root=root)
        classified = {key for key in equivalents}
        matrix = [
            {
                "campaign": Path(str(r["source_dir"])).name,
                "group": r["group"],
                "mutant": r["mutant_id"],
                "equivalent": f"{Path(str(r['source_dir'])).name}/{r['mutant_id']}" in classified and not r["killed"],
                "target": r["target"],
                "operator": r["operator"],
                "layer": r["layer"],
                "status": r["status"],
                "killed": r["killed"],
                "golden_only": r["golden_only"],
            }
            for r in sorted(
                mutate_rows,
                key=lambda r: (str(r["source_dir"]), str(r["group"]), str(r["mutant_id"]), str(r["layer"])),
            )
        ]
        out["figure-6-kill-matrix.csv"] = to_csv(matrix)
        out["figure-6-kill-matrix.md"] = to_markdown(
            scores,
            title="Figure 6 — mutation score per campaign and group (Wilson 95%)",
            note="equivalent: survivors hand-classified in pfc_eval/equivalents.json (digest-pinned to the sealed "
            "diff); score uses equivalent = 0, score_excluding_equivalent removes them from the denominator.",
        )
    return out


def write(outputs: Mapping[str, str], directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in sorted(outputs.items()):
        path = directory / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    for stale in directory.iterdir():
        if stale.name not in outputs and stale.is_file():
            stale.unlink()
    return written


def check(outputs: Mapping[str, str], directory: Path) -> list[str]:
    """Names whose committed text differs from a fresh build (or are missing/extra)."""
    problems = []
    for name, text in sorted(outputs.items()):
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"{name}: differs")
    if directory.is_dir():
        problems.extend(
            f"{p.name}: extra" for p in sorted(directory.iterdir()) if p.is_file() and p.name not in outputs
        )
    return problems


def command(arguments: object) -> int:
    """`python -m pfc_eval tables DIRS [--output DIR]`: build from explicitly listed sealed directories."""
    namespace = vars(arguments)
    root = Path.cwd()
    directories: dict[str, list[Entry]] = {}
    for item in namespace["directories"]:
        path = Path(str(item))
        try:
            loaded = evidence.load(path)
        except (OSError, ValueError) as error:
            print(f"pfc_eval: {path}: {error}")
            return 1
        directories.setdefault(str(loaded.configuration["experiment"]), []).append(Entry(str(path), "development"))
    config = Config(0, {k: tuple(v) for k, v in directories.items()}, None)
    outputs = build(config, root=root)
    output = namespace.get("output")
    if output:
        for path in write(outputs, Path(str(output))):
            print(path)
        return 0
    for name, text in sorted(outputs.items()):
        if name.endswith(".md"):
            print(text)
    return 0
