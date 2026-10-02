"""Evaluation shared by retrospective experiments and immutable issued forecasts.

All metrics state their denominator; unknown blog labels are excluded, not negatives.
Guide selection, visibility and reporting bias remain unresolved and are disclosed.
"""
import math
import statistics
from datetime import date, timedelta

from domain import size_target, observed_points, rank_from_count, point_names


def actual_record(obs):
    if obs is None:
        return None
    size = size_target(obs)
    count = {0: 0, 1: 3, 2: 25, 3: 60, 4: 150}.get(size)
    tier, label = rank_from_count(obs.get("visibility_hi"), count)
    lo, hi = obs.get("water_temp_lo"), obs.get("water_temp_hi")
    vlo, vhi = obs.get("visibility_lo"), obs.get("visibility_hi")
    return {
        "hammer_seen": obs.get("hammer_seen"), "hammer_size": obs.get("hammer_size"),
        "size_num": size, "hammer_points": observed_points(obs),
        "visibility_hi": vhi, "visibility": f"{vlo:g}-{vhi:g}m" if vlo is not None and vhi is not None else None,
        "water_temp_mean": (lo + hi) / 2 if lo is not None and hi is not None else None,
        "water_temp": f"{lo:g}-{hi:g}℃" if lo is not None and hi is not None else None,
        "tier": tier, "tier_label": label, "shops_reporting": obs.get("shops_reporting", []),
    }


def probability(pred):
    # Legacy records had a size threshold, not a calibrated probability.
    return pred.get("seen_probability", float(pred["size_num"] >= 0.5))


def binary_metrics(pairs):
    pairs = [(float(p), bool(y)) for p, y in pairs if p is not None and y is not None]
    n = len(pairs)
    if not n:
        return {k: None for k in ("accuracy", "balanced_accuracy", "brier", "auc", "recall", "specificity")} | {"n": 0, "positive": 0, "negative": 0, "calibration": []}
    tp = sum(p >= .5 and y for p, y in pairs)
    tn = sum(p < .5 and not y for p, y in pairs)
    pos = sum(y for _, y in pairs)
    neg = n - pos
    recall = tp / pos if pos else None
    spec = tn / neg if neg else None
    # Rank-based AUC with ties, independent of probability calibration.
    ordered = sorted(pairs)
    rank_sum = 0.0
    i = 0
    while i < n:
        j = i + 1
        while j < n and ordered[j][0] == ordered[i][0]: j += 1
        rank_sum += (i + 1 + j) / 2 * sum(y for _, y in ordered[i:j])
        i = j
    auc = (rank_sum - pos * (pos + 1) / 2) / (pos * neg) if pos and neg else None
    bins = []
    for b in range(5):
        cell = [(p, y) for p, y in pairs if min(4, int(p * 5)) == b]
        if cell:
            bins.append({"n": len(cell), "predicted": round(statistics.mean(p for p, _ in cell), 3),
                         "observed": round(statistics.mean(y for _, y in cell), 3)})
    return {"n": n, "positive": pos, "negative": neg,
            "accuracy": round((tp + tn) / n, 4),
            "balanced_accuracy": round((recall + spec) / 2, 4) if recall is not None and spec is not None else None,
            "brier": round(statistics.mean((p - y) ** 2 for p, y in pairs), 4),
            "auc": round(auc, 4) if auc is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "specificity": round(spec, 4) if spec is not None else None, "calibration": bins}


def baselines(obs, target, as_of):
    """Use only observations before issuance, never target-day/future observations."""
    prior = [(d, s) for d, s in sorted(obs.items()) if d < as_of and s.get("hammer_seen") is not None]
    month = [(d, s) for d, s in prior if d[5:7] == target[5:7]]
    sample = month or prior
    season = (sum(s["hammer_seen"] for _, s in sample) + 1) / (len(sample) + 2)
    previous = (date.fromisoformat(as_of) - timedelta(days=1)).isoformat()
    last = obs.get(previous, {}).get("hammer_seen")
    known_sizes = [size_target(s) for _, s in sample if size_target(s) is not None]
    large = (sum(v == 4 for v in known_sizes) + 1) / (len(known_sizes) + 2)
    return {"always_seen": 1.0, "seasonal": season,
            "persistence": float(last) if last is not None else None,
            "seasonal_large": large}


def summarize(rows):
    evaluated = [r for r in rows if r.get("actual")]
    seen = [r for r in evaluated if r["actual"]["hammer_seen"] is not None]
    known_sizes = [r for r in evaluated if r["actual"]["size_num"] is not None]
    known_tiers = [r for r in evaluated if r["actual"]["tier"] is not None and r["actual"]["tier"] in "SABCDE"]
    # The explicit guard precedes membership so unknown tiers remain unevaluated.
    tiers = {t: i for i, t in enumerate("SABCDE")}
    def mean(values):
        return round(statistics.mean(values), 4) if values else None
    model = binary_metrics([(probability(r["predicted"]), r["actual"]["hammer_seen"]) for r in seen])
    comparisons = {name: binary_metrics([(r.get("baselines", {}).get(name), r["actual"]["hammer_seen"]) for r in seen])
                   for name in ("always_seen", "seasonal", "persistence")}
    # Also report the model on the exact persistence-available subset.
    comparisons["model_on_persistence_days"] = binary_metrics([
        (probability(r["predicted"]), r["actual"]["hammer_seen"]) for r in seen
        if r.get("baselines", {}).get("persistence") is not None])
    pt_rows = [r for r in evaluated if r["actual"]["hammer_points"]]
    def pt_key(p):
        # Map the engine's subdivided Kame-ne rocks to the recorded parent area.
        import forecast_engine as fe
        return fe.STATS_KEY_FOR_POINT.get(p, p)
    size_pairs = [(r["predicted"]["size_num"], r["actual"]["size_num"]) for r in known_sizes]
    corr = None
    if len(size_pairs) > 2:
        x, y = zip(*size_pairs)
        if statistics.pstdev(x) and statistics.pstdev(y): corr = round(statistics.correlation(x, y), 4)
    return {
        "n_days": len(rows), "n_evaluated": len(evaluated), "n_no_report": len(rows) - len(evaluated),
        "n_seen_days": len(seen), "n_unknown_seen": len(evaluated) - len(seen),
        "n_size_days": len(known_sizes), "n_tier_days": len(known_tiers),
        "seen_hit_rate": model["accuracy"], "seen_metrics": model, "baselines": comparisons,
        "large_metrics": binary_metrics([(r["predicted"].get("large_probability"), r["actual"]["size_num"] == 4) for r in known_sizes]),
        "large_baseline": binary_metrics([(r.get("baselines", {}).get("seasonal_large"), r["actual"]["size_num"] == 4) for r in known_sizes]),
        "tier_exact_rate": mean([r["predicted"]["tier"] == r["actual"]["tier"] for r in known_tiers]),
        "tier_within1_rate": mean([abs(tiers[r["predicted"]["tier"]] - tiers[r["actual"]["tier"]]) <= 1 for r in known_tiers]),
        "size_exact_rate": mean([round(p) == a for p, a in size_pairs]),
        "size_within1_rate": mean([abs(round(p) - a) <= 1 for p, a in size_pairs]),
        "size_correlation": corr,
        "visibility_mae_m": mean([abs(r["predicted"]["visibility_m"] - r["actual"]["visibility_hi"]) for r in evaluated if r["actual"]["visibility_hi"] is not None]),
        "water_temp_mae_c": mean([abs(r["predicted"]["water_temp"] - r["actual"]["water_temp_mean"]) for r in evaluated if r["actual"]["water_temp_mean"] is not None]),
        "n_point_days": len(pt_rows),
        "point_top3_hit_rate": mean([bool({pt_key(p) for p in r["predicted"]["points"]} & {pt_key(p) for p in r["actual"]["hammer_points"]}) for r in pt_rows]),
    }
