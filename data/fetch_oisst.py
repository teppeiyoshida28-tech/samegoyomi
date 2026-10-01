"""NOAA OISST v2.1 daily SST 2015-2022 @ (34.625N,138.875E) via PSL THREDDS.
月チャンク(大レンジは502になるため)。レジューム対応: oisst_state.json
出力: oisst_daily_2015_2022.json
"""
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
import requests

LAT_IDX = 498
LON_IDX = 555
STATE = Path("oisst_state.json")

out = {}
if STATE.exists():
    out = json.load(open(STATE))

for year in range(2015, 2023):
    ndays = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    d0 = date(year, 1, 1)
    for start in range(0, ndays, 61):
        end = min(start + 60, ndays - 1)
        key_probe = (d0 + timedelta(days=end)).isoformat()
        if key_probe in out and (d0 + timedelta(days=start)).isoformat() in out:
            continue
        url = (f"https://psl.noaa.gov/thredds/dodsC/Datasets/noaa.oisst.v2.highres/"
               f"sst.day.mean.{year}.nc.ascii?sst%5B{start}:{end}%5D%5B{LAT_IDX}%5D%5B{LON_IDX}%5D")
        ok = False
        for attempt in range(6):
            try:
                r = requests.get(url, timeout=90)
                r.raise_for_status()
                ok = True
                break
            except Exception as e:
                print(f"  retry {year}[{start}:{end}] a{attempt}: {e}", flush=True)
                time.sleep(20 + 20 * attempt)
        if not ok:
            print(f"  GIVEUP {year}[{start}:{end}]", flush=True)
            continue
        vals = re.findall(r"^\[(\d+)\]\[0\],\s*([\-\d.]+)", r.text, re.M)
        for i, v in vals:
            d = d0 + timedelta(days=start + int(i))
            out[d.isoformat()] = round(float(v), 2)
        json.dump(out, open(STATE, "w"))
        print(f"{year}[{start}:{end}]: +{len(vals)} (total {len(out)})", flush=True)
        time.sleep(4)

json.dump({"source": "NOAA OISST v2.1 daily 0.25deg via PSL THREDDS",
           "grid_point": [34.625, 138.875], "daily_sst": out},
          open("oisst_daily_2015_2022.json", "w"))
print("total days:", len(out))
