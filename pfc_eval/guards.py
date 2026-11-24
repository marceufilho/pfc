"""Guards endpoint of the held-out study: executable guard sites removed, per kernel and condition (plan phase 7).

The public strata measure admission on loops without arrays; the development kernels measure check elimination but
are solved almost equally by every condition. The sealed study is where the two meet, so its secondary endpoint is
the fraction of a kernel's executable guard sites (bounds and division, counted in the checked emission) that the
best emitted build of each condition removes. The denominator is frozen per kernel; the numerator is the number of
removed sites of the admission's verified emission (0 when a condition admitted nothing). Conditions: L1, L2
(proposer rows, best build per run, mean over runs), B1, B2 (Houdini rows) and `manual` (the hand invariant, from
the differential rows). The pre-registered contrasts are L2 − B2 (primary: LLM without feedback against the symbolic
baseline that leaves the template family), L1 − L2 and L1 − B1, paired by kernel, cluster bootstrap over kernels,
exact sign test, Holm over the three (AMENDMENTS item 31). Nothing here confers proof authority; the rows come from
emissions decided by the frozen compiler.
"""

from collections.abc import Mapping, Sequence

from pfc_eval import stats

Row = Mapping[str, object]
PRIMARY = "L2 - B2"
CONTRASTS = ("L2 - B2", "L1 - L2", "L1 - B1")
MINIMUM_MEANINGFUL_GAIN = 0.20
FAMILY = "preregistered-guards"


def _int(value: object) -> int:
    return int(str(value)) if value is not None else 0


def _denominators(rows: Sequence[Row]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        if row.get("guards_total") is None:
            continue
        kernel, total = str(row["kernel"]), _int(row["guards_total"])
        if kernel in totals and totals[kernel] != total:
            raise ValueError(f"inconsistent guard denominator for {kernel}: {totals[kernel]} and {total}")
        totals[kernel] = total
    return totals


def _proposer_fractions(rows: Sequence[Row], condition: str, totals: Mapping[str, int]) -> dict[str, float]:
    best: dict[tuple[str, int], int] = {}
    for row in rows:
        if row.get("condition") != condition or row.get("guards_total") is None:
            continue
        key = (str(row["kernel"]), _int(row["run"]))
        best[key] = max(best.get(key, 0), _int(row.get("guards_removed")))
    fractions: dict[str, float] = {}
    for kernel in sorted({k for k, _ in best}):
        runs = [removed for (k, _), removed in best.items() if k == kernel]
        total = totals[kernel]
        fractions[kernel] = sum(r / total for r in runs) / len(runs) if total else 0.0
    return fractions


def _houdini_fractions(rows: Sequence[Row], family: str, totals: Mapping[str, int]) -> dict[str, float]:
    removed: dict[str, int] = {}
    for row in rows:
        if str(row.get("family", "b1")) != family or row.get("guards_total") is None:
            continue
        kernel = str(row["kernel"])
        removed[kernel] = max(removed.get(kernel, 0), _int(row.get("guards_removed")))
    return {k: (v / totals[k] if totals[k] else 0.0) for k, v in sorted(removed.items())}


def _differential_fractions(rows: Sequence[Row], totals: Mapping[str, int]) -> dict[str, float]:
    removed: dict[str, int] = {}
    for row in rows:
        if row.get("guards_total") is None:
            continue
        removed[str(row["kernel"])] = _int(row.get("guards_removed"))
    return {k: (v / totals[k] if totals[k] else 0.0) for k, v in sorted(removed.items())}


def endpoint(
    proposer: Sequence[Row], houdini: Sequence[Row], differential: Sequence[Row], *, seed: int
) -> dict[str, object]:
    """Per-kernel fractions per condition and the three pre-registered paired contrasts."""
    totals = _denominators([*proposer, *houdini, *differential])
    conditions: dict[str, dict[str, float]] = {}
    for condition in ("L1", "L2"):
        fractions = _proposer_fractions(proposer, condition, totals)
        if fractions:
            conditions[condition] = fractions
    for condition, family in (("B1", "b1"), ("B2", "b2")):
        fractions = _houdini_fractions(houdini, family, totals)
        if fractions:
            conditions[condition] = fractions
    manual = _differential_fractions(differential, totals)
    if manual:
        conditions["manual"] = manual
    kernels = [
        {"kernel": kernel, "condition": condition, "guards_total": totals[kernel], "fraction": fraction}
        for condition, fractions in conditions.items()
        for kernel, fraction in fractions.items()
    ]
    contrasts: list[dict[str, object]] = []
    for name in CONTRASTS:
        left, right = name.split(" - ")
        a, b = conditions.get(left, {}), conditions.get(right, {})
        common = sorted(set(a) & set(b))
        if not common:
            continue
        differences = {k: a[k] - b[k] for k in common}
        bootstrap = stats.cluster_bootstrap({k: [d] for k, d in differences.items()}, stats.mean, seed=seed)
        sign = stats.sign_test(list(differences.values()))
        contrasts.append(
            {
                "contrast": name,
                "tasks": len(common),
                "estimate": bootstrap.estimate,
                "low": bootstrap.interval.low,
                "high": bootstrap.interval.high,
                "positive": sign.positive,
                "negative": sign.negative,
                "ties": sign.ties,
                "p_sign": sign.p_value,
                "family": FAMILY,
                "primary": name == PRIMARY,
            }
        )
    corrected = stats.holm([float(str(c["p_sign"])) for c in contrasts]) if contrasts else ()
    for contrast, p in zip(contrasts, corrected, strict=True):
        contrast["holm_m"] = len(contrasts)
        contrast["p_holm"] = p
    return {
        "kernels": kernels,
        "contrasts": contrasts,
        "primary": PRIMARY,
        "minimum_meaningful_gain": MINIMUM_MEANINGFUL_GAIN,
    }
