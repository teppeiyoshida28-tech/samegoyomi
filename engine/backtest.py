"""1年バックテスト (leak-free) v3 — models.py 共有モデル使用

予測対象:
  - ハンマー目撃/ランク (S-E)
  - 群れ規模クラス (0-4)      … リッジ回帰 (期間前データのみで学習)
  - 透明度 (m)                … 改善版: 直近実測+経過日数, 72h降水, 48h波, 流速
  - 水温 (℃)                 … SST + 直近実測オフセット
  - 欠航確率                  … 学習ベース (波・風・降水・季節 → 報告なし率)
  - 出現ポイント Top3

出力: backtest_data.json
"""
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta

import forecast_engine as fe
from models import EnvModels, SIZE_NUM, ROOT, blended_score

OUT = ROOT / "backtest_data.json"
BACKTEST_DAYS = 365


def main():
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=BACKTEST_DAYS)
    start_s = start.isoformat()

    em = EnvModels(future_days=1)
    em.train(cutoff=start_s)

    print("Running backtest...")
    results = []
    d_cur = start
    while d_cur <= end:
        d_str = d_cur.isoformat()
        p = em.predict_day(d_str)
        if p is None:
            d_cur += timedelta(days=1)
            continue
        hf = p.pop("hf")

        s = em.obs.get(d_str)
        actual = None
        if s:
            act_tier, act_label = fe.classify_rank(
                visibility_hi=s.get("visibility_hi"), hammer_seen=s.get("hammer_seen"),
                hammer_size=s.get("hammer_size"), adjusted_score=0, has_actual=True)
            actual = {
                "hammer_seen": s.get("hammer_seen"),
                "hammer_size": s.get("hammer_size"),
                "size_num": SIZE_NUM.get(s.get("hammer_size"), 1 if s.get("hammer_seen") else 0)
                            if s.get("hammer_seen") else 0,
                "hammer_points": [x[0] if isinstance(x, (list, tuple)) else x
                                  for x in (s.get("hammer_points") or [])],
                "visibility_hi": s.get("visibility_hi"),
                "visibility": (f"{s['visibility_lo']:g}-{s['visibility_hi']:g}m"
                               if s.get("visibility_lo") is not None else None),
                "water_temp_mean": (round((s["water_temp_lo"] + s["water_temp_hi"]) / 2, 1)
                                    if s.get("water_temp_lo") is not None else None),
                "water_temp": (f"{s['water_temp_lo']:g}-{s['water_temp_hi']:g}℃"
                               if s.get("water_temp_lo") is not None else None),
                "tier": act_tier, "tier_label": act_label,
                "shops_reporting": s.get("shops_reporting", []),
            }

        p["score"] = blended_score(p["size_num"], p["visibility_m"], p["cancel_prob"], tier=p["tier"])
        results.append({
            "date": d_str,
            "weekday": "月火水木金土日"[d_cur.weekday()],
            "predicted": p,
            "actual": actual,
        })
        d_cur += timedelta(days=1)

    # ---------- サマリ ----------
    TIER_NUM = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1, "E": 0}
    evald = [r for r in results if r["actual"] is not None]
    seen_days = [r for r in evald if r["actual"]["hammer_seen"] is not None]
    # 目撃予測は規模モデルベース (濁りでも出るため透明度と分離)
    hit_seen = sum(1 for r in seen_days
                   if (r["predicted"]["size_num"] >= 0.5) == bool(r["actual"]["hammer_seen"]))
    tier_exact = sum(1 for r in evald if r["predicted"]["tier"] == r["actual"]["tier"])
    tier_w1 = sum(1 for r in evald
                  if abs(TIER_NUM[r["predicted"]["tier"]] - TIER_NUM[r["actual"]["tier"]]) <= 1)
    size_pairs = [(int(round(r["predicted"]["size_num"])), r["actual"]["size_num"]) for r in seen_days]
    size_exact = sum(1 for p_, a in size_pairs if p_ == a)
    size_w1 = sum(1 for p_, a in size_pairs if abs(p_ - a) <= 1)
    vis_pairs = [(r["predicted"]["visibility_m"], r["actual"]["visibility_hi"])
                 for r in evald if r["actual"]["visibility_hi"] is not None]
    vis_mae = statistics.mean(abs(p_ - a) for p_, a in vis_pairs) if vis_pairs else None
    temp_pairs = [(r["predicted"]["water_temp"], r["actual"]["water_temp_mean"])
                  for r in evald if r["actual"]["water_temp_mean"] is not None]
    temp_mae = statistics.mean(abs(p_ - a) for p_, a in temp_pairs) if temp_pairs else None
    pt_days = [r for r in evald if r["actual"]["hammer_points"]]
    pt_hits = sum(1 for r in pt_days
                  if set(r["predicted"]["points"]) & set(r["actual"]["hammer_points"]))

    # 欠航予測の検証: 学習済みしきい値で「欠航予想」とし、報告なしと比較
    cancel_tp = sum(1 for r in results if r["predicted"]["cancel_flag"] and r["actual"] is None)
    cancel_fp = sum(1 for r in results if r["predicted"]["cancel_flag"] and r["actual"] is not None)
    cancel_fn = sum(1 for r in results if not r["predicted"]["cancel_flag"] and r["actual"] is None)
    cancel_tn = sum(1 for r in results if not r["predicted"]["cancel_flag"] and r["actual"] is not None)
    cancel_acc = (cancel_tp + cancel_tn) / len(results) if results else None
    cancel_recall = cancel_tp / (cancel_tp + cancel_fn) if (cancel_tp + cancel_fn) else None
    cancel_precision = cancel_tp / (cancel_tp + cancel_fp) if (cancel_tp + cancel_fp) else None

    # 規模相関
    corr = None
    if len(size_pairs) >= 5:
        xs = [r["predicted"]["size_num"] for r in seen_days]
        ys = [a for _, a in size_pairs]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        if sx > 0 and sy > 0:
            corr = round(cov / (sx * sy), 3)

    summary = {
        "n_days": len(results),
        "n_evaluated": len(evald),
        "n_no_report": len(results) - len(evald),
        "seen_hit_rate": round(hit_seen / len(seen_days), 3) if seen_days else None,
        "tier_exact_rate": round(tier_exact / len(evald), 3) if evald else None,
        "tier_within1_rate": round(tier_w1 / len(evald), 3) if evald else None,
        "size_exact_rate": round(size_exact / len(size_pairs), 3) if size_pairs else None,
        "size_within1_rate": round(size_w1 / len(size_pairs), 3) if size_pairs else None,
        "size_correlation": corr,
        "visibility_mae_m": round(vis_mae, 2) if vis_mae is not None else None,
        "water_temp_mae_c": round(temp_mae, 2) if temp_mae is not None else None,
        "point_top3_hit_rate": round(pt_hits / len(pt_days), 3) if pt_days else None,
        "n_point_days": len(pt_days),
        "cancel_accuracy": round(cancel_acc, 3) if cancel_acc is not None else None,
        "cancel_recall": round(cancel_recall, 3) if cancel_recall is not None else None,
        "cancel_precision": round(cancel_precision, 3) if cancel_precision is not None else None,
        "n_train_days": em.n_train,
    }

    out = {
        "generated_at": datetime.now().isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "method": ("各日の予測は前日までのショップ観測のみ使用 (leak-free)。"
                   "規模・透明度・欠航モデルはバックテスト期間より前の実測 (2015〜) だけで学習。"
                   "環境データはOpen-Meteo再解析。"),
        "summary": summary,
        "days": results,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"Wrote {OUT}")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
