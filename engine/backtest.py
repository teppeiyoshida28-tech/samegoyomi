"""Retrospective evaluation using historical environment values, not issued forecasts."""
import argparse
from datetime import timedelta
from models import EnvModels, ROOT, blended_score
from domain import today_jst, now_jst, write_json, BIAS_NOTE, MODEL_VERSION
from evaluation import actual_record, baselines, summarize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--current-mode", choices=["smoc", "legacy"], default="smoc")
    ap.add_argument("--output", default=str(ROOT / "backtest_data.json"))
    args = ap.parse_args()
    end = today_jst() - timedelta(days=1)
    start = today_jst() - timedelta(days=365)
    em = EnvModels(future_days=1, fetch_live=not args.offline, current_mode=args.current_mode)
    em.train(cutoff=start.isoformat())
    rows, skipped = [], []
    d = start
    while d <= end:
        ds = d.isoformat()
        p = em.predict_day(ds)
        if p is None:
            skipped.append(ds)
        else:
            p.pop("hf", None)
            p["score"] = blended_score(p["size_num"], p["visibility_m"], p["weather_risk"], tier=p["tier"])
            rows.append({"date": ds, "weekday": "月火水木金土日"[d.weekday()], "predicted": p,
                         "actual": actual_record(em.obs.get(ds)), "baselines": baselines(em.obs, ds, ds)})
        d += timedelta(days=1)
    if not rows:
        raise RuntimeError("No complete environmental data in evaluation period")
    summary = summarize(rows)
    summary["n_train_days"] = em.n_train
    write_json(args.output, {
        "generated_at": now_jst().isoformat(), "model_version": MODEL_VERSION,
        "evaluation_type": "retrospective", "current_mode": args.current_mode,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "method": "過去環境値による再計算。観測は対象日前のみ、学習は検証期間前のみ。当時発表した予報の成績とは異なります。",
        "observation_bias_note": BIAS_NOTE, "skipped_incomplete_dates": skipped,
        "summary": summary, "days": rows})
    print(f"Retrospective: {len(rows)} days, missing environment: {len(skipped)}; {summary['seen_metrics']}")


if __name__ == "__main__":
    main()
