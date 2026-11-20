"""Enrich complete forecasts without changing their recommendation or sea-state evidence."""
from datetime import datetime, timedelta
from models import EnvModels, ROOT, blended_score
from domain import read_json, write_json, today_jst, now_jst, sea_safety, BIAS_NOTE
from forecast_archive import build_issued_history
from environment_inputs import INPUT_VERSION, API_MODEL


def require_fresh_forecast(data, now=None):
    now = now or now_jst()
    issued = datetime.fromisoformat(data.get("issued_at", ""))
    if (issued.utcoffset() != timedelta(hours=9) or issued.date() != now.date()
            or issued > now + timedelta(minutes=5)):
        raise RuntimeError("本日生成の予報ではありません。古い予報の再装飾を停止します。")
    if not data.get("daily") or data["daily"][0]["date"] != now.date().isoformat():
        raise RuntimeError("本日のフル予報がありません。")
    source = data.get("input_sources", {}).get("marine", {}).get("ocean", {})
    if (data.get("input_pipeline_version") != INPUT_VERSION or source.get("api_model") != API_MODEL):
        raise RuntimeError("検証済みSMOC入力から生成された予報ではありません。")


def main():
    data = read_json(ROOT / "forecast_data.json")
    require_fresh_forecast(data)
    em = EnvModels(future_days=16, persist_env=True, require_fresh=True)
    em.train(cutoff=today_jst().isoformat())
    for d in data["daily"]:
        p = em.predict_day(d["date"])
        if p is None:
            raise RuntimeError(f"ML環境データ不足: {d['date']}。混在予報の公開を停止")
        p.pop("hf", None)
        # One authoritative recommendation, with its evidence and score kept together.
        p["points"] = [v["point"] for v in d["top3_points"]]
        d["diveable"], d["sea_status"] = sea_safety(d["max_wind"], d["max_wave"])
        p["diveable"], p["sea_status"] = d["diveable"], d["sea_status"]
        p["weather_risk"] = 1.0 if d["diveable"] is False else (0.5 if d["max_wind"] > 9 or d["max_wave"] > 1.5 else 0.0)
        d["ml"] = p
        if d.get("actual_hammer_seen") is None and not d.get("actual_visibility"):
            d["tier"], d["tier_label"] = p["tier"], p["tier_label"]
            d["score"] = blended_score(p["size_num"], p["visibility_m"], p["weather_risk"], tier=p["tier"])
        d.setdefault("environment_note", d.get("note", ""))
        d["note"] = d["sea_status"] + " / " + d["environment_note"]
    issued = build_issued_history(em.obs)
    recent = [r for r in issued["days"] if r["lead_days"] == 1 and r.get("actual") and r["actual"]["tier"]][-7:]
    data["recent_track"] = [{"date": r["date"], "pred_tier": r["predicted"]["tier"],
        "act_tier": r["actual"]["tier"], "tier_within1": abs("SABCDE".index(r["predicted"]["tier"]) - "SABCDE".index(r["actual"]["tier"])) <= 1} for r in recent]
    data["ml_meta"] = {"n_train": em.n_train, "generated_at": now_jst().isoformat(),
                       "input_sources": em.live_sources,
                       "latest_observation_date": max(em.obs, default=None),
                       "note": "規模・透明度は回帰、目撃・大群はロジスティック回帰。海況判定は波・風の基準。" + BIAS_NOTE}
    write_json(ROOT / "forecast_data.json", data)
    print(f"Enriched {len(data['daily'])} complete forecast days")


if __name__ == "__main__":
    main()
