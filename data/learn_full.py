"""Phase1: 全量データ (dive_logs_structured_full.json, 1000日超) で重み再学習。
repo/weight_learner.py のロジックを流用し、入力だけ差し替える。
出力: phase1/learned_weights_full.json (+ repo/learned_weights.json を更新)
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
REPO = ROOT.parent / "engine"
sys.path.insert(0, str(REPO))

import weight_learner as wl  # noqa: E402


def load_data_full():
    obs = json.load(open(ROOT / "dive_logs_structured_full.json"))
    clim = json.load(open(REPO / "climatology_mikomoto.json"))
    daily = obs["daily_summary"]
    # 旧スキーマ互換: tropical_count を補完
    for s in daily:
        if "tropical_count" not in s:
            s["tropical_count"] = len(s.get("tropical_species") or [])
    return daily, clim


def main():
    daily, clim = load_data_full()
    records = wl.build_training_data(daily, clim)
    print(f"Training records: {len(records)}")

    X = [[r["f_sst"], r["f_vis"], r["f_trop"], r["f_recent"]] for r in records]
    y = [r["y_norm"] for r in records]

    # 非負制約付き最小二乗 (座標降下法)。bias は自由。
    # 共線性 (SST偏差↔透明度) で負に落ちるのを防ぎ、相関因子間で重みを配分する。
    n, m = len(X), len(X[0])
    lam = 0.5  # ridge
    w = [0.0] * m
    bias = sum(y) / n
    for _ in range(500):
        # bias 更新
        bias = sum(yi - sum(wj * xj for wj, xj in zip(w, row))
                   for row, yi in zip(X, y)) / n
        for j in range(m):
            num = 0.0
            den = lam
            for row, yi in zip(X, y):
                r = yi - bias - sum(w[k] * row[k] for k in range(m) if k != j)
                num += row[j] * r
                den += row[j] * row[j]
            w[j] = max(0.0, num / den)
    raw = w
    total = sum(raw)
    if total < 1e-6:
        print("all weights zero"); return 1
    norm = [x / total for x in raw]

    # in-sample MAE
    mae = sum(abs(bias + sum(w * x for w, x in zip(raw, row)) - yi)
              for row, yi in zip(X, y)) / len(X)

    names = ["f_sst_anomaly", "f_visibility", "f_tropical", "f_recent_actual"]
    result = {
        "trained_at": datetime.now().isoformat(),
        "n_records": len(records),
        "source": "phase1 full logs (hammers+ms+290 partial)",
        "bias": bias,
        "raw_weights": dict(zip(names, raw)),
        "normalized_weights": dict(zip(names, norm)),
        "mae": mae,
    }
    json.dump(result, open(ROOT / "learned_weights_full.json", "w"), ensure_ascii=False, indent=2)
    json.dump(result, open(REPO / "learned_weights.json", "w"), ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
