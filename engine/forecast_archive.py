"""Append immutable issuance records and evaluate each lead time against later logs."""
import argparse
import hashlib
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

from domain import read_json, write_json, now_jst, today_jst, BIAS_NOTE, JST
from evaluation import actual_record, baselines, summarize

ROOT = Path(__file__).parent
ARCHIVE = ROOT / "forecast_archive"


def save_snapshot(data, obs, directory=ARCHIVE, *, published_at):
    issued = datetime.fromisoformat(published_at)
    generated = datetime.fromisoformat(data["issued_at"])
    if issued.utcoffset() is None or generated.utcoffset() is None:
        raise ValueError("Publication and generation times require timezones")
    issued, generated = issued.astimezone(JST), generated.astimezone(JST)
    if issued < generated:
        raise ValueError("Publication cannot precede forecast generation")
    days = []
    for d in data["daily"]:
        # Never count a day's observed/retrospectively revised result as a prediction.
        if d["date"] < issued.date().isoformat() or (d["date"] == issued.date().isoformat() and issued.hour >= 8):
            continue
        if d.get("actual_hammer_seen") is not None or d.get("actual_visibility"):
            continue
        if d.get("forecast_kind") != "full" or "ml" not in d:
            continue
        p = dict(d["ml"])
        p["score"], p["tier"] = d["score"], d["tier"]
        p["points"] = [v["point"] for v in d["top3_points"]]
        days.append({"date": d["date"], "lead_days": (date.fromisoformat(d["date"]) - issued.date()).days,
                     "predicted": p, "recommendation": {k: d.get(k) for k in ("best_point", "best_point_score", "best_point_evidence", "top3_points")},
                     "baselines": baselines(obs, d["date"], generated.date().isoformat())})
    source_hash = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    value = {"issued_at": issued.isoformat(), "generated_at": generated.isoformat(),
             "model_version": data["model_version"],
             "git_commit": commit, "forecast_sha256": source_hash,
             "current_method": data.get("current_method"), "days": days}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (issued.strftime("%Y%m%dT%H%M%S%f%z") + ".json")
    if path.exists():
        if read_json(path) != value:
            raise ValueError("既存の発表予報は上書きできません")
        return path
    # Exclusive creation prevents accidental replacement, including concurrent runs.
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1, allow_nan=False)
    return path


def build_issued_history(obs, directory=ARCHIVE):
    selected = {}
    # Multiple runs on one issuance date: evaluate the earliest forecast per target/lead.
    for path in sorted(Path(directory).glob("*.json")):
        snap = read_json(path)
        for d in snap["days"]:
            if d["date"] >= today_jst().isoformat():
                continue
            key = (d["date"], d["lead_days"])
            if key not in selected:
                selected[key] = dict(d, issued_at=snap["issued_at"], model_version=snap["model_version"],
                                     actual=actual_record(obs.get(d["date"])))
    rows = sorted(selected.values(), key=lambda r: (r["date"], r["lead_days"]))
    leads = sorted({r["lead_days"] for r in rows})
    return {"generated_at": now_jst().isoformat(), "evaluation_type": "issued",
            "observation_bias_note": BIAS_NOTE,
            "method": "保存した発表時点の予報を評価。同じ対象日・予測日数では最初の発表を採用。未観測は採点しません。",
            "by_lead": {str(lead): summarize([r for r in rows if r["lead_days"] == lead]) for lead in leads},
            "days": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--published-at", help="Successful deployment time (ISO with timezone, or 'now')")
    args = ap.parse_args()
    obs = {s["date"]: s for s in read_json(ROOT / "dive_logs_structured.json")["daily_summary"]}
    if args.save:
        if not args.published_at:
            ap.error("--save requires --published-at after successful deployment")
        published = now_jst().isoformat() if args.published_at == "now" else args.published_at
        print("Saved:", save_snapshot(read_json(ROOT / "forecast_data.json"), obs, published_at=published))
    write_json(ROOT / "issued_history.json", build_issued_history(obs))


if __name__ == "__main__":
    main()
