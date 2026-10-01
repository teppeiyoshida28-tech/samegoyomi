"""
神子元 平年SST 算出スクリプト
Open-Meteo Historical Marine API から過去5年分の SST を取得し、日別平年値を作る。
"""
import json
import time
from datetime import date, timedelta
import requests
import statistics

LAT, LON = 34.5717, 138.9433  # 神子元島
YEARS = [2021, 2022, 2023, 2024, 2025]
from pathlib import Path
OUT_JSON = str(Path(__file__).resolve().parent / "climatology_mikomoto.json")

def fetch_year(year):
    """ある年の全日SSTを取得"""
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "start_date": start,
        "end_date": end,
        "daily": "sea_surface_temperature_mean,sea_surface_temperature_max,sea_surface_temperature_min",
        "timezone": "Asia/Tokyo",
        "cell_selection": "sea",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    # 各年のSST時系列を取得
    yearly_series = {}
    for y in YEARS:
        print(f"Fetching {y}...")
        try:
            js = fetch_year(y)
            yearly_series[y] = js
            time.sleep(1.5)  # rate limit
        except Exception as e:
            print(f"  failed: {e}")

    # (month, day) -> [SST values across years]
    daily_bucket = {}  # {(mm, dd): [vals...]}
    for y, js in yearly_series.items():
        times = js.get("daily", {}).get("time", [])
        ssts = js.get("daily", {}).get("sea_surface_temperature_mean", [])
        for t, s in zip(times, ssts):
            if s is None:
                continue
            _, mm, dd = t.split("-")
            key = (int(mm), int(dd))
            daily_bucket.setdefault(key, []).append(float(s))

    # 平年値算出
    climatology = {}
    for (mm, dd), vals in sorted(daily_bucket.items()):
        if not vals:
            continue
        climatology[f"{mm:02d}-{dd:02d}"] = {
            "mean": round(statistics.mean(vals), 2),
            "std": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0,
            "n": len(vals),
        }

    # 月別サマリ
    monthly = {}
    for key, v in climatology.items():
        mm = int(key.split("-")[0])
        monthly.setdefault(mm, []).append(v["mean"])
    monthly_mean = {mm: round(statistics.mean(vs), 2) for mm, vs in monthly.items()}

    result = {
        "location": {"lat": LAT, "lon": LON, "name": "Mikomoto Island"},
        "source": "Open-Meteo Historical Marine (MeteoFrance SST)",
        "years_used": list(yearly_series.keys()),
        "monthly_mean_sst": monthly_mean,
        "daily_climatology": climatology,
    }

    with open(OUT_JSON, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {OUT_JSON}")
    print(f"Monthly means: {monthly_mean}")


if __name__ == "__main__":
    main()
