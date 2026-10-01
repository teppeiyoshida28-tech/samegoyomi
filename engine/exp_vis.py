"""透明度モデルの改善実験: 線形 vs 対数空間 vs 変化量予測"""
import math
import statistics
from datetime import date, timedelta

from models import EnvModels, fit_ridge

em = EnvModels(future_days=1, quiet=True)
cutoff = (date.today() - timedelta(days=365)).isoformat()

# 学習データ収集
X, y = [], []
for d in em.obs_dates:
    if d >= cutoff or d < "2015-01-10":
        continue
    agg = em.day_agg(d)
    if agg is None:
        continue
    s = em.obs[d]
    if s.get("visibility_hi") is None:
        continue
    hf = em.hist_features(d)
    X.append((em.x_vis(d, agg, hf), hf))
    y.append(min(30.0, float(s["visibility_hi"])))

# テストデータ
Xt, yt = [], []
d_cur = date.fromisoformat(cutoff)
end = date.today() - timedelta(days=1)
while d_cur <= end:
    ds = d_cur.isoformat()
    agg = em.day_agg(ds)
    s = em.obs.get(ds)
    if agg is not None and s and s.get("visibility_hi") is not None:
        hf = em.hist_features(ds)
        Xt.append((em.x_vis(ds, agg, hf), hf))
        yt.append(min(30.0, float(s["visibility_hi"])))
    d_cur += timedelta(days=1)

print(f"train={len(X)}, test={len(Xt)}")

# A: 線形 (現行)
fa = fit_ridge([x for x, _ in X], y)
mae_a = statistics.mean(abs(max(1, min(30, fa(x))) - t) for (x, _), t in zip(Xt, yt))

# B: 対数空間
fb = fit_ridge([x for x, _ in X], [math.log(v) for v in y])
mae_b = statistics.mean(abs(max(1, min(30, math.exp(fb(x)))) - t) for (x, _), t in zip(Xt, yt))

# C: 変化量 (y - last_vis) を予測
fc = fit_ridge([x for x, _ in X], [t - hf["last_vis"] for (x, hf), t in zip(X, y)])
mae_c = statistics.mean(abs(max(1, min(30, hf["last_vis"] + fc(x))) - t) for (x, hf), t in zip(Xt, yt))

# D: 対数変化量 log(y/last_vis)
fd = fit_ridge([x for x, _ in X], [math.log(t / max(1, hf["last_vis"])) for (x, hf), t in zip(X, y)])
mae_d = statistics.mean(abs(max(1, min(30, hf["last_vis"] * math.exp(fd(x)))) - t) for (x, hf), t in zip(Xt, yt))

# E: ベースライン (last_vis そのまま / vis3)
mae_p = statistics.mean(abs(hf["last_vis"] - t) for (_, hf), t in zip(Xt, yt))
mae_p3 = statistics.mean(abs(hf["vis3"] - t) for (_, hf), t in zip(Xt, yt))

print(f"A 線形(現行):      MAE {mae_a:.3f}")
print(f"B 対数空間:        MAE {mae_b:.3f}")
print(f"C 変化量:          MAE {mae_c:.3f}")
print(f"D 対数変化量:      MAE {mae_d:.3f}")
print(f"E last_vis持続:    MAE {mae_p:.3f}")
print(f"E vis3持続:        MAE {mae_p3:.3f}")
