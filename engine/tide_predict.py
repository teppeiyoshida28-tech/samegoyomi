"""潮汐調和分解 + 予測 (神子元近傍格子)

hindcast_marine.json の sea_level_height_msl (毎時, 2015〜) から
主要8分潮 (M2,S2,N2,K2,K1,O1,P1,Q1) + 平均を最小二乗フィットし、
任意日の毎時潮位を予測する。

nodal補正は省略 (直近2年でフィットして吸収)。ダイビング計画用途には十分。
"""
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
PH1 = ROOT.parent / "data"

# 分潮角速度 (deg/hour)
CONSTITUENTS = {
    "M2": 28.9841042, "S2": 30.0, "N2": 28.4397295, "K2": 30.0821373,
    "K1": 15.0410686, "O1": 13.9430356, "P1": 14.9589314, "Q1": 13.3986609,
}
EPOCH = datetime(2015, 1, 1, 0, 0)  # JST naive


def _hours_since_epoch(dt):
    return (dt - EPOCH).total_seconds() / 3600.0


def fit(years=2.0, verbose=False):
    """直近 years 年の hindcast 潮位でフィット。係数 dict を返す"""
    m = json.load(open(PH1 / "hindcast_marine.json"))["hourly"]
    times = m["time"]
    lvls = m["sea_level_height_msl"]
    # 末尾から years 年分
    n_keep = int(years * 365.25 * 24)
    times = times[-n_keep:]
    lvls = lvls[-n_keep:]
    ts, ys = [], []
    for t, y in zip(times, lvls):
        if y is None:
            continue
        ts.append(_hours_since_epoch(datetime.fromisoformat(t)))
        ys.append(y)

    omegas = [math.radians(w) for w in CONSTITUENTS.values()]
    P = 1 + 2 * len(omegas)

    def basis(t):
        row = [1.0]
        for w in omegas:
            row.append(math.cos(w * t))
            row.append(math.sin(w * t))
        return row

    # 正規方程式
    XtX = [[0.0] * P for _ in range(P)]
    Xty = [0.0] * P
    for t, y in zip(ts, ys):
        row = basis(t)
        for i in range(P):
            ri = row[i]
            Xty[i] += ri * y
            for j in range(i, P):
                XtX[i][j] += ri * row[j]
    for i in range(P):
        for j in range(i):
            XtX[i][j] = XtX[j][i]
    # ガウス消去
    A = [XtX[i][:] + [Xty[i]] for i in range(P)]
    for i in range(P):
        piv = max(range(i, P), key=lambda r: abs(A[r][i]))
        A[i], A[piv] = A[piv], A[i]
        d = A[i][i]
        for j in range(i, P + 1):
            A[i][j] /= d
        for r in range(P):
            if r != i and abs(A[r][i]) > 1e-14:
                f = A[r][i]
                for j in range(i, P + 1):
                    A[r][j] -= f * A[i][j]
    coefs = [A[i][P] for i in range(P)]

    if verbose:
        # in-sample RMSE
        sse = 0.0
        for t, y in zip(ts, ys):
            row = basis(t)
            pred = sum(c * r for c, r in zip(coefs, row))
            sse += (pred - y) ** 2
        rmse = math.sqrt(sse / len(ts))
        amp = {}
        for k, name in enumerate(CONSTITUENTS):
            a, b = coefs[1 + 2 * k], coefs[2 + 2 * k]
            amp[name] = math.hypot(a, b)
        print(f"fit n={len(ts)} rmse={rmse:.3f}m amplitudes=" +
              ", ".join(f"{n}:{v:.3f}" for n, v in amp.items()))
    return {"coefs": coefs, "omegas_deg": list(CONSTITUENTS.values())}


def predict_hour(model, dt):
    t = _hours_since_epoch(dt)
    coefs = model["coefs"]
    v = coefs[0]
    for k, wdeg in enumerate(model["omegas_deg"]):
        w = math.radians(wdeg)
        v += coefs[1 + 2 * k] * math.cos(w * t) + coefs[2 + 2 * k] * math.sin(w * t)
    return v


def day_profile(model, d, h0=6, h1=16):
    """日 d の h0〜h1 時の予測潮位リスト [(hour, level), ...]"""
    base = datetime(d.year, d.month, d.day)
    return [(h, predict_hour(model, base + timedelta(hours=h))) for h in range(h0, h1 + 1)]


if __name__ == "__main__":
    model = fit(verbose=True)
    # 検証: hindcast 末尾30日 vs 予測
    m = json.load(open(PH1 / "hindcast_marine.json"))["hourly"]
    times = m["time"][-720:]
    lvls = m["sea_level_height_msl"][-720:]
    errs = []
    for t, y in zip(times, lvls):
        if y is None:
            continue
        p = predict_hour(model, datetime.fromisoformat(t))
        errs.append(abs(p - y))
    import statistics
    print(f"holdout-ish last30d MAE={statistics.mean(errs):.3f}m max={max(errs):.3f}m")
    json.dump(model, open(ROOT / "tide_model.json", "w"))
    print("saved tide_model.json")
