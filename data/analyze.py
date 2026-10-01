"""
Phase1 予備分析:
構造化ログ (daily_summary) × SST(OISST/marine) × 月齢/潮名 × 黒潮流路タイプ を日付で結合し、
ハンマー目撃率 / 大群率 を単純集計する。
出力: analysis_summary.json + stdout (Markdown表)
"""
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent


def load():
    st = json.load(open(ROOT / "dive_logs_structured_full.json"))
    daily = st["daily_summary"]

    moon = json.load(open(ROOT / "moon_tide_calendar.json"))["calendar"]

    ku = json.load(open(ROOT / "kuroshio_path_history.json"))
    monthly = ku["monthly_type_2015_2026"]

    # 日次SST: OISST(2015-2022) 優先、それ以降は marine hourly の日平均
    oisst = json.load(open(ROOT / "oisst_daily_2015_2022.json"))["daily_sst"]
    marine = json.load(open(ROOT / "hindcast_marine.json"))["hourly"]
    sst_daily = dict(oisst)
    acc = defaultdict(list)
    for t, v in zip(marine["time"], marine["sea_surface_temperature"]):
        if v is None:
            continue
        acc[t[:10]].append(v)
    for d, vals in acc.items():
        if d not in sst_daily:  # OISST優先
            sst_daily[d] = round(sum(vals) / len(vals), 2)
    return daily, moon, monthly, sst_daily, oisst, acc


def doy(dstr):
    dt = date.fromisoformat(dstr)
    return dt.timetuple().tm_yday


def build_climatology(sst_daily):
    """day-of-year 平年値 (±7日窓平均, 全年利用)"""
    bydoy = defaultdict(list)
    for dstr, v in sst_daily.items():
        bydoy[min(doy(dstr), 365)].append(v)
    clim = {}
    for d in range(1, 366):
        vals = []
        for off in range(-7, 8):
            dd = (d + off - 1) % 365 + 1
            vals.extend(bydoy.get(dd, []))
        clim[d] = sum(vals) / len(vals) if vals else None
    return clim


def kuroshio_type(dstr, monthly):
    y, m = dstr[:4], dstr[5:7]
    ym = monthly.get(y)
    if not isinstance(ym, dict):
        return None
    t = ym.get(m)
    if not t:
        return None
    t = t.replace("(推定)", "")
    return t


def main():
    daily, moon, monthly, sst_daily, oisst, marine_daily = load()
    clim = build_climatology(sst_daily)

    rows = []
    for e in daily:
        d = e["date"]
        if e["hammer_seen"] is None:
            continue
        m = moon.get(d, {})
        sst = sst_daily.get(d)
        anom = None
        if sst is not None and clim.get(min(doy(d), 365)):
            anom = sst - clim[min(doy(d), 365)]
        rows.append({
            "date": d,
            "seen": e["hammer_seen"],
            "large": e.get("hammer_size") == "large",
            "tide_name": m.get("tide_name"),
            "moon_age": m.get("moon_age"),
            "kuroshio": kuroshio_type(d, monthly),
            "sst": sst,
            "sst_anom": anom,
            "n_reports": e["n_reports"],
        })

    def agg(keyfn, order=None):
        g = defaultdict(lambda: [0, 0, 0])  # n, seen, large
        for r in rows:
            k = keyfn(r)
            if k is None:
                continue
            g[k][0] += 1
            g[k][1] += r["seen"]
            g[k][2] += r["large"]
        out = []
        keys = order if order else sorted(g.keys())
        for k in keys:
            if k not in g:
                continue
            n, s, L = g[k]
            out.append({"key": k, "n_days": n, "seen": s,
                        "seen_rate": round(s / n, 3),
                        "large": L, "large_rate": round(L / n, 3)})
        return out

    res = {}
    res["by_kuroshio"] = agg(lambda r: r["kuroshio"], ["nNLM", "oNLM", "LM", "meander_non_LM"])
    res["by_tide"] = agg(lambda r: r["tide_name"], ["大潮", "中潮", "小潮", "長潮", "若潮"])

    def moon_bin(r):
        a = r["moon_age"]
        if a is None:
            return None
        if a < 3.7 or a >= 26.3:
            return "新月期(±3日)"
        if 11.1 <= a < 18.5:
            return "満月期(±3日)"
        if 3.7 <= a < 11.1:
            return "上弦側"
        return "下弦側"
    res["by_moon_phase"] = agg(moon_bin, ["新月期(±3日)", "上弦側", "満月期(±3日)", "下弦側"])

    def anom_bin(r):
        a = r["sst_anom"]
        if a is None:
            return None
        if a < -2: return "<-2"
        if a < -1: return "-2〜-1"
        if a < 0: return "-1〜0"
        if a < 1: return "0〜+1"
        if a < 2: return "+1〜+2"
        return ">=+2"
    res["by_sst_anom"] = agg(anom_bin, ["<-2", "-2〜-1", "-1〜0", "0〜+1", "+1〜+2", ">=+2"])

    def sst_bin(r):
        s = r["sst"]
        if s is None: return None
        lo = int(s // 2) * 2
        return f"{lo}-{lo+2}"
    res["by_sst_abs"] = agg(sst_bin)

    # 年別
    res["by_year"] = agg(lambda r: r["date"][:4])
    # 月別
    res["by_month"] = agg(lambda r: r["date"][5:7])

    res["n_rows"] = len(rows)
    res["generated_at"] = datetime.now().isoformat()
    json.dump(res, open(ROOT / "analysis_summary.json", "w"), ensure_ascii=False, indent=1)

    def md_table(name, data):
        print(f"\n### {name}")
        print("| 区分 | 日数 | 目撃日 | 目撃率 | 大群日 | 大群率 |")
        print("|---|---|---|---|---|---|")
        for r in data:
            print(f"| {r['key']} | {r['n_days']} | {r['seen']} | {r['seen_rate']:.1%} | {r['large']} | {r['large_rate']:.1%} |")

    print(f"n_days_with_judgement={len(rows)}")
    md_table("黒潮流路タイプ別", res["by_kuroshio"])
    md_table("潮名別", res["by_tide"])
    md_table("月相別", res["by_moon_phase"])
    md_table("SST平年偏差ビン別", res["by_sst_anom"])
    md_table("SST絶対値ビン別", res["by_sst_abs"])
    md_table("年別", res["by_year"])
    md_table("月別", res["by_month"])


if __name__ == "__main__":
    main()
