"""
神子元 ハンマー予測 重み学習器

過去実測データ (dive_logs_structured.json) と Open-Meteo環境変数を突き合わせ、
「目撃有無 + 規模」を予測する線形回帰で 6因子の重みを最尤推定する。

因子:
  f_sst_anomaly    : 平年偏差 (sigmoid変換済み)
  f_current        : 流速 (ピーク付き)
  f_weather        : 波風
  f_visibility     : 実測透明度
  f_tropical       : 南方系魚類の出現数 (0-5 種類)
  f_recent_actual  : 直近3日の目撃連続数

出力: learned_weights.json
"""
import json
import math
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from domain import size_target, now_jst

ROOT = Path(__file__).parent


def sigmoid(x):
    if x > 40: return 1.0
    if x < -40: return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def load_data():
    obs = json.load(open(ROOT / "dive_logs_structured.json"))
    clim = json.load(open(ROOT / "climatology_mikomoto.json"))
    return obs["daily_summary"], clim


def score_visibility(vis_hi_m):
    if vis_hi_m is None:
        return 0.5
    if vis_hi_m < 5: return 0.1
    if vis_hi_m < 10: return 0.4
    if vis_hi_m < 15: return 0.7
    if vis_hi_m < 20: return 0.9
    return 1.0


def build_training_data(daily_summary, clim):
    """各日を1レコードに: [f_sst_anomaly, f_visibility, f_tropical, f_recent] → target"""
    daily_by_date = {s["date"]: s for s in daily_summary}
    records = []

    dates_sorted = sorted(daily_by_date.keys())
    for i, d in enumerate(dates_sorted):
        s = daily_by_date[d]
        if size_target(s) is None:
            continue
        if s.get("water_temp_lo") is None:
            continue

        # 実測水温 (中央値)
        wt = (s["water_temp_lo"] + s["water_temp_hi"]) / 2
        # 平年
        mmdd = d[5:]
        clim_val = clim["daily_climatology"].get(mmdd, {}).get("mean")
        if clim_val is None:
            continue
        anom = wt - clim_val
        f_sst = sigmoid(anom / 1.5)

        # 透明度
        f_vis = score_visibility(s.get("visibility_hi"))

        # 南方系
        f_trop = min(1.0, s.get("tropical_count", 0) / 3.0)

        # 直近3日の目撃勢い
        recent_seen = 0
        for j in range(max(0, i-3), i):
            prev = daily_by_date.get(dates_sorted[j])
            if prev and prev.get("hammer_seen"):
                recent_seen += 1
        f_recent = recent_seen / 3.0

        # ターゲット: 目撃 + 規模スコア (0=無, 1=単体, 2=小群, 3=中群, 4=大群)
        seen = 1 if s.get("hammer_seen") else 0
        size_map = {"large": 4, "medium": 3, "small": 2, "single": 1, None: (1 if seen else 0)}
        y = size_map.get(s.get("hammer_size"), 0)
        # 0-1 に正規化
        y_norm = y / 4.0

        records.append({
            "date": d,
            "f_sst": f_sst,
            "f_vis": f_vis,
            "f_trop": f_trop,
            "f_recent": f_recent,
            "y_norm": y_norm,
            "seen": seen,
            "size": s.get("hammer_size"),
        })
    return records


def linreg_lsq(X, y):
    """単純最小二乗回帰 (正規方程式), numpy 未使用"""
    n = len(X)
    if n == 0:
        return None
    m = len(X[0])
    # 定数項を追加 (bias)
    Xa = [[1.0] + row for row in X]
    ma = m + 1

    # X^T X
    XtX = [[0.0]*ma for _ in range(ma)]
    for row in Xa:
        for i in range(ma):
            for j in range(ma):
                XtX[i][j] += row[i]*row[j]
    # X^T y
    Xty = [0.0]*ma
    for row, yi in zip(Xa, y):
        for i in range(ma):
            Xty[i] += row[i]*yi

    # ridge (正則化 λ=0.5) 追加
    lam = 0.5
    for i in range(ma):
        XtX[i][i] += lam

    # ガウス消去
    A = [XtX[i] + [Xty[i]] for i in range(ma)]
    for i in range(ma):
        # pivot
        maxv = abs(A[i][i]); mri = i
        for k in range(i+1, ma):
            if abs(A[k][i]) > maxv:
                maxv = abs(A[k][i]); mri = k
        A[i], A[mri] = A[mri], A[i]
        if abs(A[i][i]) < 1e-12:
            return None
        # normalize
        piv = A[i][i]
        for j in range(i, ma+1):
            A[i][j] /= piv
        # eliminate
        for k in range(ma):
            if k != i and abs(A[k][i]) > 1e-12:
                fk = A[k][i]
                for j in range(i, ma+1):
                    A[k][j] -= fk * A[i][j]
    return [A[i][ma] for i in range(ma)]


def learn_weights():
    daily, clim = load_data()
    records = build_training_data(daily, clim)
    print(f"Training records: {len(records)}")
    if len(records) < 5:
        print("Not enough data — using default weights.")
        return default_weights()

    X = [[r["f_sst"], r["f_vis"], r["f_trop"], r["f_recent"]] for r in records]
    y = [r["y_norm"] for r in records]

    coefs = linreg_lsq(X, y)
    if coefs is None:
        return default_weights()

    bias = coefs[0]
    raw_weights = coefs[1:]

    # 負の重みは 0 に (ridge が効かせない場合)
    # → 各因子は"良いほど+"なので単調性を強制
    clipped = [max(0.0, w) for w in raw_weights]
    total = sum(clipped)
    if total < 1e-6:
        return default_weights()
    normalized = [w / total for w in clipped]

    result = {
        "trained_at": now_jst().isoformat(),
        "n_records": len(records),
        "bias": bias,
        "raw_weights": {
            "f_sst_anomaly": raw_weights[0],
            "f_visibility": raw_weights[1],
            "f_tropical": raw_weights[2],
            "f_recent_actual": raw_weights[3],
        },
        "normalized_weights": {
            "f_sst_anomaly": normalized[0],
            "f_visibility": normalized[1],
            "f_tropical": normalized[2],
            "f_recent_actual": normalized[3],
        },
        # 予測ロジック用のシンプルなpredict関数を書ける情報
    }

    # in-sample fit
    preds = []
    for r in records:
        p = bias + raw_weights[0]*r["f_sst"] + raw_weights[1]*r["f_vis"] + \
            raw_weights[2]*r["f_trop"] + raw_weights[3]*r["f_recent"]
        preds.append(p)
    mae = statistics.mean(abs(p-r["y_norm"]) for p, r in zip(preds, records))
    result["mae"] = mae

    with open(ROOT / "learned_weights.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n=== 学習結果 (n={len(records)}) ===")
    print(f"MAE: {mae:.3f} (0-1スケール)")
    print(f"Bias: {bias:+.3f}")
    print(f"Raw weights:")
    for k, v in result["raw_weights"].items():
        print(f"  {k:<20} {v:+.3f}")
    print(f"Normalized weights:")
    for k, v in result["normalized_weights"].items():
        print(f"  {k:<20} {v:.1%}")

    print(f"\n=== In-sample predictions ===")
    print(f"{'Date':<12} {'Actual':>8} {'Pred':>6} {'f_sst':>6} {'f_vis':>6} {'f_trop':>7} {'f_recent':>8}")
    for r, p in zip(records, preds):
        size_lbl = r["size"] or ("見た" if r["seen"] else "なし")
        print(f"{r['date']:<12} {size_lbl:>8} {p:>6.2f} {r['f_sst']:>6.2f} {r['f_vis']:>6.2f} {r['f_trop']:>7.2f} {r['f_recent']:>8.2f}")

    return result


def default_weights():
    return {
        "trained_at": None,
        "n_records": 0,
        "bias": 0.0,
        "raw_weights": {
            "f_sst_anomaly": 0.35,
            "f_visibility": 0.25,
            "f_tropical": 0.20,
            "f_recent_actual": 0.20,
        },
        "normalized_weights": {
            "f_sst_anomaly": 0.35,
            "f_visibility": 0.25,
            "f_tropical": 0.20,
            "f_recent_actual": 0.20,
        },
    }


if __name__ == "__main__":
    learn_weights()
