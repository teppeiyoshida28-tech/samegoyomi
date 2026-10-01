"""
Phase1-3: 月齢・潮名カレンダー 2015-01-01〜2027-12-31
- 月齢: ephem で直前の朔からの経過日数 (JST正午基準)
- 旧暦日: floor(月齢)+1
- 潮名: 旧暦日ベースの標準割当 (Wikipedia 潮汐 / 一般的な釣り潮見表)
    1-3:大潮 4-6:中潮 7-9:小潮 10:長潮 11:若潮 12-13:中潮
    14-17:大潮 18-20:中潮 21-23:小潮 24:長潮 25:若潮 26-28:中潮 29-30:大潮
出力: moon_tide_calendar.json {date: {moon_age, lunar_day, tide_name}}
"""
import json
import math
from datetime import date, datetime, timedelta, timezone

import ephem

JST = timezone(timedelta(hours=9))

TIDE_TABLE = {}
for d_ in (1, 2, 3, 14, 15, 16, 17, 29, 30):
    TIDE_TABLE[d_] = "大潮"
for d_ in (4, 5, 6, 12, 13, 18, 19, 20, 26, 27, 28):
    TIDE_TABLE[d_] = "中潮"
for d_ in (7, 8, 9, 21, 22, 23):
    TIDE_TABLE[d_] = "小潮"
TIDE_TABLE[10] = "長潮"
TIDE_TABLE[24] = "長潮"
TIDE_TABLE[11] = "若潮"
TIDE_TABLE[25] = "若潮"

start = date(2015, 1, 1)
end = date(2027, 12, 31)

cal = {}
d = start
while d <= end:
    # JST正午 -> UTC
    ref = datetime(d.year, d.month, d.day, 12, 0, tzinfo=JST).astimezone(timezone.utc)
    e = ephem.Date(ref.strftime("%Y/%m/%d %H:%M:%S"))
    prev_new = ephem.previous_new_moon(e)
    moon_age = float(e) - float(prev_new)  # days since new moon
    lunar_day = int(math.floor(moon_age)) + 1
    if lunar_day > 30:
        lunar_day = 30
    tide = TIDE_TABLE.get(lunar_day, "中潮")
    cal[d.isoformat()] = {
        "moon_age": round(moon_age, 2),
        "lunar_day": lunar_day,
        "tide_name": tide,
    }
    d += timedelta(days=1)

out = {
    "generated_at": datetime.now().isoformat(),
    "method": "ephem previous_new_moon, JST noon reference; tide name from lunar-day standard table",
    "range": [start.isoformat(), end.isoformat()],
    "n_days": len(cal),
    "calendar": cal,
}
with open("moon_tide_calendar.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("days:", len(cal))
# sanity checks: known new moons  2023-11-13 (JST 18:27新月) -> 11-13 age ~0.?; 大潮 around 11-13..15
for k in ["2023-11-05", "2023-11-13", "2026-08-24"]:
    print(k, cal[k])
