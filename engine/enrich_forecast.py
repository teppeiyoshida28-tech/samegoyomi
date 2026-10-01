"""forecast_data.json に新モデル (models.py) の予測を追記する。
forecast_engine.py 実行後に走らせる。

各 daily レコードに "ml" キーを追加:
  size_num/size_label/count_est, visibility_m, water_temp,
  cancel_prob/cancel_flag, points(Top3), tier/tier_label

実測日 (actual_hammer_seen が非null) はスキップせず参考として付与。
tier は実測がない日のみ ml 側で上書き。score も規模ベースにブレンド。
"""
import json
from datetime import date

from models import EnvModels, ROOT, blended_score

FD = ROOT / "forecast_data.json"


def main():
    data = json.load(open(FD))
    em = EnvModels(future_days=16)
    em.train(cutoff=date.today().isoformat())  # 予報は全実測で学習してOK (未来にリークは無い)

    n_ml = 0
    for d in data["daily"]:
        p = em.predict_day(d["date"])
        if p is None:
            continue
        p.pop("hf", None)
        d["ml"] = p
        has_actual = d.get("actual_hammer_seen") is not None or d.get("actual_visibility")
        if not has_actual:
            d["tier"] = p["tier"]
            d["tier_label"] = p["tier_label"]
            d["score"] = blended_score(p["size_num"], p["visibility_m"], p["cancel_prob"], tier=p["tier"])
            d["diveable"] = p["diveable"]
            d["best_point"] = p["points"][0] if p["points"] else d.get("best_point")
        n_ml += 1
    # 直近7日の答え合わせ (backtest_data.json から)
    bt_path = ROOT / "backtest_data.json"
    if bt_path.exists():
        bt = json.load(open(bt_path))
        recent = [r for r in bt["days"] if r.get("actual")][-7:]
        track = []
        for r in recent:
            p_, a_ = r["predicted"], r["actual"]
            hit_seen = (p_["size_num"] >= 0.5) == bool(a_["hammer_seen"])
            tier_diff = abs("SABCDE".index(p_["tier"]) - "SABCDE".index(a_["tier"]))
            track.append({
                "date": r["date"],
                "pred_tier": p_["tier"],
                "act_tier": a_["tier"],
                "hit_seen": hit_seen,
                "tier_within1": tier_diff <= 1,
                "act_seen": a_["hammer_seen"],
                "act_size": a_.get("hammer_size"),
            })
        data["recent_track"] = track

    data["ml_meta"] = {
        "n_train": em.n_train,
        "cancel_threshold": round(em.cancel_threshold, 2),
        "note": "規模/透明度/欠航: リッジ回帰 (全実測で学習)。検証成績は /history/ 参照。",
    }
    json.dump(data, open(FD, "w"), ensure_ascii=False, indent=2)
    print(f"Enriched {n_ml}/{len(data['daily'])} days with ML predictions")


if __name__ == "__main__":
    main()
