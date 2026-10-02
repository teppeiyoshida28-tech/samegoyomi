"""Shared, explicit conventions for forecasts, labels and validation.

Blog observations measure detection under guide selection and visibility bias;
unknown observations must never become confirmed absences.
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9), name="JST")
MODEL_VERSION = "2026-10-integrity-v1"
BIAS_NOTE = ("ショップによる行先・時間の選択、透明度による発見率、記事の記載頻度に偏りがあります。"
             "目撃率は自然界の出現確率ではありません。無言及・規模不明・地点不明は未知として扱います。")
SIZE_NUM = {"large": 4, "medium": 3, "small": 2, "single": 1}


def now_jst():
    return datetime.now(JST)


def today_jst():
    return now_jst().date()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value, indent=1):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=indent, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def size_target(obs):
    if obs.get("hammer_seen") is False:
        return 0
    if obs.get("hammer_seen") is True:
        return SIZE_NUM.get(obs.get("hammer_size"))
    return None


def point_names(values):
    return sorted({("jab_ne" if p == "ao_ne" else p)
                   for value in (values or [])
                   for p in [value[0] if isinstance(value, (tuple, list)) else value]})


def observed_points(obs):
    # Old hammer_points were article mentions, NOT confirmed sighting locations.
    return point_names(obs.get("sighting_points")) if obs.get("hammer_seen") is True else []


def sea_safety(wind, wave):
    if (wind is not None and wind > 12) or (wave is not None and wave > 2):
        return False, "海況基準超過・出航判断は現地確認"
    if wind is None or wave is None:
        return None, "海況データ不足・判定不可"
    if wind > 9 or wave > 1.5:
        return True, "海況注意・出航判断は現地確認"
    return True, "海況基準内・出航判断は現地確認"


def current_vector(speed, direction, dh=0.0, mode="smoc"):
    """SMOC already includes tides. The legacy term is diagnostic only.

    Source: https://open-meteo.com/en/docs/marine-weather-api
    Island-scale cape effects require separate observations before calibration.
    """
    if speed is None or direction is None:
        return None, None, None
    if mode not in ("smoc", "legacy"):
        raise ValueError("Unvalidated current mode: " + mode)
    rad = math.radians(direction)
    tidal = -20.0 * dh if mode == "legacy" else 0.0
    u, v = speed * math.sin(rad) + tidal, speed * math.cos(rad)
    return math.hypot(u, v), math.degrees(math.atan2(u, v)) % 360, tidal


def rank_from_count(vis, count):
    if count is None or vis is None:
        return None, "観測不足"
    if vis >= 20 and count >= 100: return "S", "◆◆◆ 青潮×大群"
    if count >= 100: return "A", "◆◆ 大群"
    if vis >= 20 and count >= 50: return "B", "◆◆ 青い海×中群"
    if vis >= 15 and count >= 20: return "C", "◆ 中規模群"
    if vis >= 10 and count >= 1: return "D", "△ 単体〜小群"
    return "E", "✕ 濁り/目撃なし"
