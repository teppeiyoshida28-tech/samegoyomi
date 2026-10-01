"""
神子元ハンマー遭遇予測エンジン

Open-Meteo Marine + Weather API から今日〜14日先のデータを取得、
平年偏差ベースの水温スコア + 潮汐流 + 波・風 + シーズン係数で
日別スコアと時間別ダッシュボードを生成する。

出力: engine/forecast_data.json
"""
import json
import math
import os
import statistics
import time
from datetime import date, datetime, timedelta

import requests

LAT, LON = 34.5717, 138.9433  # 神子元島
TZ = "Asia/Tokyo"
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
CLIM_PATH = str(_ROOT / "climatology_mikomoto.json")
OUT_JSON = str(_ROOT / "forecast_data.json")
BLOG_PATH = str(_ROOT / "dive_logs_structured.json")
WEIGHTS_PATH = str(_ROOT / "learned_weights.json")
POINT_STATS_PATH = str(_ROOT.parent / "analysis" / "point_stats.json")
MOON_TIDE_PATH = str(_ROOT.parent / "data" / "moon_tide_calendar.json")


# ---------- utils ----------
def sigmoid(x):
    if x > 40: return 1.0
    if x < -40: return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def deg_to_vec(deg_toward):
    """海流の"向かう先"の角度(°, 0=北, 時計回り) → (u=east, v=north)成分"""
    rad = math.radians(deg_toward)
    return math.sin(rad), math.cos(rad)


def dot_norm(u1, v1, u2, v2):
    """正規化された内積 (-1..1)"""
    n1 = math.hypot(u1, v1)
    n2 = math.hypot(u2, v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 0.0
    return (u1 * u2 + v1 * v2) / (n1 * n2)


# ---------- data fetch ----------
def fetch_marine():
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": ",".join([
            "sea_surface_temperature",
            "ocean_current_velocity",
            "ocean_current_direction",
            "wave_height",
            "wave_period",
            "sea_level_height_msl",
        ]),
        "forecast_days": 14,
        "timezone": TZ,
        "cell_selection": "sea",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "wind_speed_10m,wind_direction_10m,visibility,precipitation",
        "forecast_days": 14,
        "timezone": TZ,
        "wind_speed_unit": "ms",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def load_climatology():
    with open(CLIM_PATH) as f:
        return json.load(f)


def load_blog_observations():
    """現地ショップの直近実測データをロード"""
    if not os.path.exists(BLOG_PATH):
        return {}
    with open(BLOG_PATH) as f:
        data = json.load(f)
    return {s["date"]: s for s in data.get("daily_summary", [])}


# エンジンPOINTSキー → 実績統計(point_stats.json)キー の対応
# カメ根エリアの分解根 (北の根/東の根/絶壁の根/南北の根/ツインピークス/
# ビューポイント/カベ根/最南端の根) はログ上「カメ根」と記録されるため kame_ne に帰属
STATS_KEY_FOR_POINT = {
    "A_point": "A_point", "karito": "karito", "eno_kuchi": "eno_kuchi",
    "andoro": "andoro",
    "kita_ne": "kame_ne", "higashi_ne": "kame_ne", "cliff_rock": "kame_ne",
    "namb_ne": "kame_ne", "most_south": "kame_ne", "twin_peaks": "kame_ne",
    "view_point": "kame_ne", "kabe_ne": "kame_ne",
    "jab_ne": "jab_ne", "zabu_ne": "zabu_ne", "kado_ne": "kado_ne",
    "mitsu_ne": "mitsu_ne", "plate": "plate", "hammers_rock": "hammers_rock",
    "third_takane": "third_takane",
    # 外洋南の小根群・トビエイロックはログに独立記録なし → 統計なし(0.5)
}


def load_point_stats():
    """analysis/point_stats.json (ポイント別実績出現率) をロード"""
    if not os.path.exists(POINT_STATS_PATH):
        return None
    with open(POINT_STATS_PATH) as f:
        return json.load(f)


def load_moon_tide_calendar():
    """date → {tide_name, moon_age} (2015-2027)"""
    if not os.path.exists(MOON_TIDE_PATH):
        return {}
    with open(MOON_TIDE_PATH) as f:
        return json.load(f).get("calendar", {})


def empirical_prior_for(stats, point_key, tide_name=None, month=None, flow=None):
    """現在条件 (潮名・月・潮汐フェーズ) に対応する実績出現率 → 事前確率 (0-1)

    優先順位:
      1. 条件セル (n>=10 のもののみ) の平均
      2. 全期間出現率 (n>=10)
      3. 0.5 (中立)
    """
    if not stats:
        return 0.5, None
    skey = STATS_KEY_FOR_POINT.get(point_key)
    entry = stats["points"].get(skey) if skey else None
    if not entry:
        return 0.5, None
    cells = []
    detail = {"stats_key": skey}
    if tide_name:
        c = entry.get("by_tide_name", {}).get(tide_name)
        if c and c.get("reliable"):
            cells.append(c["rate"])
            detail["tide_name"] = {"name": tide_name, "rate": c["rate"], "n": c["n"]}
    if month:
        c = entry.get("by_month", {}).get(str(month))
        if c and c.get("reliable"):
            cells.append(c["rate"])
            detail["month"] = {"month": month, "rate": c["rate"], "n": c["n"]}
    if flow in ("up", "down"):
        c = entry.get("by_flow", {}).get(flow)
        if c and c.get("reliable"):
            cells.append(c["rate"])
            detail["flow"] = {"flow": flow, "rate": c["rate"], "n": c["n"]}
    if cells:
        return sum(cells) / len(cells), detail
    o = entry.get("overall", {})
    if o.get("reliable"):
        detail["overall_fallback"] = {"rate": o["rate"], "n": o["n"]}
        return o["rate"], detail
    return 0.5, None


def load_learned_weights():
    """学習済み重み. なければデフォルト"""
    default = {
        "raw_weights": {"f_sst_anomaly": 0.35, "f_visibility": 0.25, "f_tropical": 0.20, "f_recent_actual": 0.20},
        "bias": 0.0, "n_records": 0,
    }
    if not os.path.exists(WEIGHTS_PATH):
        return default
    with open(WEIGHTS_PATH) as f:
        return json.load(f)


def classify_rank(visibility_hi, hammer_seen, hammer_size, adjusted_score, has_actual):
    """透明度×群れ規模のマトリクスからS-Eランク判定
    実測データがある日は直接判定、未来日はスコアから確率的マッピング
    """
    # 群れ規模を数値化
    size_map = {"large": 150, "medium": 60, "small": 25, "single": 3, None: 0}

    if has_actual:
        # 実測データがある日: 直接マトリクスで判定
        vis = visibility_hi or 0
        # hammer_size=large は現地表現で概ね「100匹以上」
        # hammer_size=medium は「50前後」、small は「20前後」
        estimated_size = size_map.get(hammer_size, 0)
        if not hammer_seen:
            estimated_size = 0

        if vis >= 20 and estimated_size >= 100:
            return "S", "◆◆◆ 透明度20m以上×大群 究極"
        if estimated_size >= 100:
            return "A", "◆◆ 大群遭遇 (透明度<20m)"
        if vis >= 20 and estimated_size >= 50:
            return "B", "◆◆ 青い海×中規模群"
        if vis >= 15 and estimated_size >= 20:
            return "C", "◆ 中規模群"
        if vis >= 10 and estimated_size >= 1:
            return "D", "△ 単体〜小群 (幸運)"
        return "E", "✕ 濁り/不在"
    else:
        # 未来日: スコア+透明度prior でランク推定
        vis = visibility_hi or 10
        # 透明度20m以上 想定は S/B (青潮日)
        # 透明度15-20m は A/B (中間)
        # 透明度<15m は C 以下
        if adjusted_score >= 75 and vis >= 18:
            return "S", "◆◆◆ 大群+青潮期待"
        if adjusted_score >= 72:
            return "A", "◆◆ 大群期待"
        if adjusted_score >= 62:
            return ("B", "◆◆ 中規模期待") if vis >= 15 else ("C", "◆ 小〜中群")
        if adjusted_score >= 52:
            return "C", "◆ 小〜中群"
        if adjusted_score >= 42:
            return "D", "△ 見れたら幸運"
        return "E", "✕ 厳しい"


def score_visibility(vis_hi_m):
    """透明度スコア: 5m未満=悪, 15m以上=◎
    ハンマーの視認性 & 暖水フラグ
    """
    if vis_hi_m is None:
        return 0.5  # 不明時は中立
    if vis_hi_m < 5:
        return 0.1
    if vis_hi_m < 10:
        return 0.4
    if vis_hi_m < 15:
        return 0.7
    if vis_hi_m < 20:
        return 0.9
    return 1.0


# ---------- scoring ----------
def score_sst(sst, month, day, clim):
    """平年偏差 sigmoid スコア"""
    key = f"{month:02d}-{day:02d}"
    entry = clim["daily_climatology"].get(key)
    if entry is None:
        # 前後7日で緩衝
        near = []
        for delta in range(-7, 8):
            d = date(2024, month, day) + timedelta(days=delta)
            k = f"{d.month:02d}-{d.day:02d}"
            if k in clim["daily_climatology"]:
                near.append(clim["daily_climatology"][k]["mean"])
        clim_val = statistics.mean(near) if near else 22.0
    else:
        clim_val = entry["mean"]
    anomaly = sst - clim_val
    return {
        "sst": round(sst, 2),
        "climatology": round(clim_val, 2),
        "anomaly": round(anomaly, 2),
        "score": round(sigmoid(anomaly / 1.5), 3),
    }


def score_current(velocity_kmh):
    """流速スコア (現地観測校正版)

    1kt ≈ 1.852 km/h
    現地観察の要点:
      - <0.5kt (≈0.9km/h)   : 流れなさすぎ、ハンマー散る
      - 1.5-3kt (2.8-5.6km/h): 大群条件のスイートスポット
      - 3-5kt (5.6-9.3km/h)  : 強い流れ、島陰に大集合 (現地"下げ潮爆流"時にAポイントで大群)
      - >5kt (>9.3km/h)       : 激流だがハンマー自体は集まる (但しダイビング難易度が跳ね上がる)
    したがってスコア関数はハンマー観察確率としては広い平坦ピーク
    """
    if velocity_kmh <= 0:
        return 0.2
    if velocity_kmh < 0.9:  # <0.5kt
        return 0.2 + 0.4 * (velocity_kmh / 0.9)  # 0.2 → 0.6
    if velocity_kmh < 2.8:  # 0.5-1.5kt
        return 0.6 + 0.4 * ((velocity_kmh - 0.9) / 1.9)  # 0.6 → 1.0
    if velocity_kmh <= 9.3:  # 1.5-5kt: プラトー
        return 1.0
    # >5kt: 徐々に減衰(観察確率としてではなくダイビング成立性)
    excess = velocity_kmh - 9.3
    return max(0.4, 1.0 - excess * 0.05)


def score_weather(wind_ms, wave_h, precip):
    """出航可否 + ダイビング可能性"""
    # 神子元の欠航基準 (現地慣行より): 風速 12m/s or 波高 2m 超で欠航
    if wind_ms > 12 or wave_h > 2.0:
        return 0.0, "欠航濃厚"
    if wind_ms > 9 or wave_h > 1.5:
        return 0.5, "海況注意"
    if wind_ms > 6 or wave_h > 1.0:
        return 0.8, "やや不安定"
    return 1.0, "海況良好"


def season_bonus(month):
    """月別シーズン係数（現地言と統計に基づく）"""
    # 6-10月がベストシーズン
    table = {1:0.4, 2:0.35, 3:0.35, 4:0.5, 5:0.7, 6:0.9, 7:1.0, 8:1.0, 9:1.0, 10:0.9, 11:0.7, 12:0.5}
    return table.get(month, 0.5)


def infer_tide_direction(sea_levels, i):
    """海面高度の前後差分で上げ/下げ判定"""
    def s(k):
        if 0 <= k < len(sea_levels) and sea_levels[k] is not None:
            return sea_levels[k]
        return None
    a = s(i-1)
    c = s(i+1)
    b = s(i)
    if c is not None and a is not None:
        d = c - a
    elif c is not None and b is not None:
        d = c - b
    elif b is not None and a is not None:
        d = b - a
    else:
        return "unknown"
    if abs(d) < 0.02:
        return "slack"
    return "up" if d > 0 else "down"


"""ダイビングポイントの実マップ配置 (神子元ハンマーズ 2003-2023 公式詳細マップより)

  座標系: 島中心を原点(0,0), 東=+x, 南=+y, 単位=メートル (実距離)
  神子元島本体: 長軸 350-400m, 短軸 100-150m, NE-SW方向に長軸
  潜水可能エリア: 島の南側 (南岸から沖合最大 ~800m のドリフトエリア)
  北側の白根/ブダイ根/エビ根等は潜水不可のため除外

  ハンマーの習性:
    - 潮目 (潮の境界線) にいることが多い
    - 温かい海流(黒潮)の縁ギリギリを回遊
    - 潮陰に集まるが、暖水側のエッジを好む
"""

# 南側ダイビングポイント (island_center=(0,0), +x=east, +y=south, meters)
# カメ根エリアは 7つの独立した根に分解 (実マップ画像認識より)
POINTS = {
    # 沿岸ポイント群
    "A_point":       {"pos": (145, 125),  "label": "Aポイント",     "depth": "13-26m",  "note": "湾状 着底ハンマー"},
    "karito":        {"pos": (110, 165),  "label": "カリトの鼻",   "depth": "10m",     "note": "Aから南下ルート"},
    "eno_kuchi":     {"pos": (-100, 100), "label": "江の口",        "depth": "10-15m",  "note": "穏やかな湾"},
    "andoro":        {"pos": (-180, 20),  "label": "アンドロ",     "depth": "16-23m",  "note": "西側浅根群"},
    # カメ根エリア (南北スリット状の集合体)
    "kita_ne":       {"pos": (235, 175),  "label": "北の根",        "depth": "-18m",    "note": "カメ根北端"},
    "higashi_ne":    {"pos": (345, 185),  "label": "東の根",        "depth": "-22m",    "note": "外海側 深場"},
    "cliff_rock":    {"pos": (122, 275),  "label": "絶壁の根",     "depth": "-10m",    "note": "エリア西の壁"},
    "namb_ne":       {"pos": (190, 270),  "label": "南北の根",     "depth": "-18m",    "note": "細長スリット"},
    "most_south":    {"pos": (190, 315),  "label": "最南端の根",   "depth": "-",       "note": ""},
    "twin_peaks":    {"pos": (235, 245),  "label": "ツインピークス","depth": "-18/19m","note": "2つの峰"},
    "view_point":    {"pos": (235, 290),  "label": "ビューポイント","depth": "-10/19m","note": "パノラマ大根"},
    "kabe_ne":       {"pos": (280, 280),  "label": "カベ根",        "depth": "-17m",    "note": "東寄り垂直壁"},
    "jab_ne":        {"pos": (-80, 240),  "label": "青根/ジャブ根", "depth": "20m",     "note": "下げ潮に強い"},
    "zabu_ne":       {"pos": (-140, 220), "label": "ザブ根",        "depth": "18-22m",  "note": "南西"},
    "kado_ne":       {"pos": (-320, 30),  "label": "カド根",        "depth": "20-25m",  "note": "西端 -50m落込み"},
    "mitsu_ne":      {"pos": (-220, -20), "label": "三ツ根",        "depth": "25m",     "note": "北西ドロップオフ"},
    # 南側の外洋ポイント群 (ハンマー爆流ゾーン)
    "seven_rock":    {"pos": (110, 470),  "label": "セブンロック", "depth": "-",      "note": "小岩礁群"},
    "west_west":     {"pos": (150, 450),  "label": "西の西の根",   "depth": "10-17m",  "note": ""},
    "west_rock":     {"pos": (170, 470),  "label": "西の根",        "depth": "10-21m",  "note": ""},
    "tall_rock":     {"pos": (200, 470),  "label": "高根",          "depth": "-",      "note": ""},
    "west_takane":   {"pos": (0, 520),    "label": "西の高根",     "depth": "10m",     "note": "真南"},
    "west_west_tk":  {"pos": (-50, 460),  "label": "西の西の高根", "depth": "12m",     "note": ""},
    "plate":         {"pos": (80, 570),   "label": "プレート",     "depth": "21m",     "note": "平坦大根"},
    "hammers_rock":  {"pos": (180, 620),  "label": "ハンマーズロック","depth": "23m","note": "★ハンマー最有力"},
    "eagleray_rock": {"pos": (310, 520),  "label": "トビエイロック","depth": "27m",    "note": "エイの群れ"},
    "third_takane":  {"pos": (240, 770),  "label": "三つ目の高根", "depth": "15-25m",  "note": "★最南端の巨根"},
}

# 島の楕円モデル (SW-NE 長軸, real meters)
ISLAND_MODEL = {
    "cx_m": 0, "cy_m": 0,
    "a_m": 190,   # 長軸半径 380m
    "b_m": 65,    # 短軸半径 130m
    "tilt_deg": 40,  # 長軸が北東方向 (0=北, +東)
}


# === 海底地形 (GMRT実測 2026-08) ===
# 島中心からの「浅棚半径」(m): 流れの実効障害物は海面上の島でなく浅い台地全体。
# 30m等深線までの実測距離ベース (北〜北西は1km超 → 描画/計算上500mにキャップ)。
# 方位は北=0°時計回り。地図座標は px=東+, py=南+。
# 40m等深線ベース (GMRT実測 2026-08 再解析): 海底40m付近から潮が曲がり始める
# 北〜北西は2km級の浅い台地 → 計算上900mにキャップ
SHELF_RADII_M = {
    0: 900, 30: 900, 60: 475, 90: 300, 120: 250, 150: 225,
    180: 250, 210: 350, 240: 725, 270: 900, 300: 900, 330: 900,
}


def shelf_radius(bearing_deg):
    """方位別の浅棚半径 (m)。30°刻みテーブルを線形補間"""
    b = bearing_deg % 360
    lo = int(b // 30) * 30
    hi = (lo + 30) % 360
    t = (b - lo) / 30.0
    return SHELF_RADII_M[lo] * (1 - t) + SHELF_RADII_M[hi % 360] * t


def wake_geometry(current_dir_deg, current_speed_kmh):
    """浅棚を考慮したwake(潮陰・潮目)幾何を返す。
    - 実効障害物半幅 W_half: 流れに直交する方向の浅棚半径の平均
    - 潮目距離 D_front: 下流側棚端 + 台地幅に比例したwake長 (流速で伸びる)
      → 下げ潮(南向き成分)時は남側の潮目がハンマーズロック沖(~600m)に出る実測と整合
    """
    kt = current_speed_kmh / 1.852
    # 流下方向の方位 (px=sin, py=-cos → bearing = atan2(fx, -fy))
    rad_flow = math.radians(current_dir_deg)
    fx = math.sin(rad_flow); fy = -math.cos(rad_flow)
    bearing_down = math.degrees(math.atan2(fx, -fy)) % 360
    r_down = shelf_radius(bearing_down)
    r_up = shelf_radius((bearing_down + 180) % 360)
    w_half = 0.5 * (shelf_radius((bearing_down + 90) % 360)
                    + shelf_radius((bearing_down - 90) % 360))
    speed_stretch = 0.8 + 0.4 * min(1.0, kt / 3.0)
    # 係数0.55: 40m等深線ベースの棚幅に対応 (下げ潮2ktで潮目≈ハンマーズロック沖650m)
    d_front = (r_down + 0.55 * w_half) * speed_stretch
    return {"fx": fx, "fy": fy, "kt": kt, "r_down": r_down, "r_up": r_up,
            "w_half": w_half, "d_front": d_front}


def compute_point_scores(current_dir_deg, current_speed_kmh, warm_water_dir_deg=None,
                           muroaji_prior=0.5, empirical_priors=None):
    """各ダイビングポイントに 潮陰×潮目×温水×スイートスポット距離×ムロアジプロキシ
    × 実績事前確率(empirical_prior) を計算

    empirical_priors: {point_key: prior(0-1)} — analysis/point_stats.json 由来の
    「現在の条件(潮名・月・潮汐フェーズ)での過去出現率」。全体の25%の重みで寄与し、
    従来のジオメトリ要素は75%に按分縮小。固定ボーナス(hammers_rock等+0.15)は撤廃。

    v2 (海底地形考慮):
      1. 実効障害物 = 海面上の島でなく浅棚 (GMRT実測: 北〜北西に1km級の20m台地,
         南〜南東は急深) → 流れは島の手前から変形し、潮目は台地幅に応じて沖に出る
      2. 潮目位置 D_front = 下流棚端 + 1.8×実効半幅 (流速で伸縮)。
         下げ潮の南側潮目 ≈ ハンマーズロック沖 (現地観察と整合)
      3. 流速依存の行動切替: ~3kt までは潮目滞在、3〜4kt超の激流では
         潮目維持が困難になり潮陰側へ移動 (現地観察)
      4. ハンマーは島直近(<80m)の乱流を嫌う / ムロアジ食物連鎖プロキシ
    """
    wk = wake_geometry(current_dir_deg, current_speed_kmh)
    fx, fy, kt = wk["fx"], wk["fy"], wk["kt"]
    perp_x, perp_y = -fy, fx

    # 流速による 潮目⇄潮陰 の重みシフト (3〜4.5ktで遷移)
    t_strong = max(0.0, min(1.0, (kt - 3.0) / 1.5))
    # ジオメトリ要素は全体の75%に按分縮小 (残り25%は実績事前確率)
    GEO = 0.75
    w_front = GEO * 0.45 * (1.0 - 0.55 * t_strong)
    w_lee = GEO * 0.20 * (1.0 + 1.4 * t_strong)
    w_dist = GEO * 0.20
    w_warm = GEO * 0.10
    w_muro = GEO * 0.05
    w_emp = 0.25

    scores = {}
    for name, p in POINTS.items():
        px, py = p["pos"]
        dist_from_island = math.hypot(px, py)

        # === (0) スイートスポット距離 ===
        if dist_from_island < 80:
            distance_score = max(0.05, dist_from_island / 80 * 0.3)
        elif dist_from_island < 500:
            offset = abs(dist_from_island - 250) / 250
            distance_score = 1.0 - offset * 0.35
        else:
            distance_score = max(0.4, 1.0 - (dist_from_island - 500) / 500)

        # === wake座標系: s=流下方向距離, c=横方向距離 ===
        s = px * fx + py * fy
        c = abs(px * perp_x + py * perp_y)

        # === (1) 潮目スコア: D_front 中心のガウス帯 ===
        # スコア用実効半幅: 40m棚幅そのままでは帯が広がりすぎるため50%圧縮
        w_eff = wk["w_half"] * 0.5
        sigma_s = 140.0 + 60.0 * t_strong
        sigma_c = w_eff + 150.0
        front_score = math.exp(-0.5 * ((s - wk["d_front"]) / sigma_s) ** 2) \
            * math.exp(-0.5 * (c / sigma_c) ** 2)
        # 潮目はエッジ(横ずれ)も良い: 中心軸ドンピシャより僅かに横を好む
        edge_bonus = math.exp(-0.5 * ((c - sigma_c * 0.6) / (sigma_c * 0.5)) ** 2)
        front_score = 0.75 * front_score + 0.25 * front_score * edge_bonus * 2.0
        front_score = min(1.0, front_score)

        # === (2) 潮陰スコア: 棚端〜潮目の間の遮蔽域 ===
        if s > 0:
            lee_center = wk["r_down"] * 0.5 + wk["d_front"] * 0.45
            lee_score = math.exp(-0.5 * ((s - lee_center) / (wk["d_front"] * 0.4)) ** 2) \
                * math.exp(-0.5 * (c / (w_eff * 0.8 + 60.0)) ** 2)
        else:
            lee_score = 0.0

        # === (3) 温水エッジ ===
        if warm_water_dir_deg is not None:
            rad_w = math.radians(warm_water_dir_deg)
            wx = math.sin(rad_w); wy = -math.cos(rad_w)
            if dist_from_island > 0.1:
                pos_ux = px / dist_from_island
                pos_uy = py / dist_from_island
                warm_dot = pos_ux * wx + pos_uy * wy
                warm_score = max(0, warm_dot) * 0.5
            else:
                warm_score = 0
        else:
            warm_score = 0.3

        # === (4) ムロアジプロキシ ===
        muroaji_score = muroaji_prior
        if py > 400:
            muroaji_score = muroaji_score * 1.15

        # === (5) 実績事前確率 (analysis/point_stats.json 由来) ===
        emp = 0.5
        if empirical_priors:
            emp = empirical_priors.get(name, 0.5)

        # === 統合スコア (ジオメトリ75% + 実績25%) ===
        raw = (
            w_lee * lee_score
          + w_front * front_score
          + w_dist * distance_score
          + w_warm * warm_score
          + w_muro * muroaji_score
          + w_emp * emp
        )

        final = min(1.0, raw)
        scores[name] = round(final, 3)
    return scores


# ---------- main pipeline ----------
def run():
    print("Fetching marine data...")
    marine = fetch_marine()
    print("Fetching weather data...")
    weather = fetch_weather()
    clim = load_climatology()
    observations = load_blog_observations()
    weights = load_learned_weights()
    point_stats = load_point_stats()
    moon_tide = load_moon_tide_calendar()
    print(f"Loaded {len(observations)} days of blog observations for calibration.")
    print(f"Loaded learned weights (n={weights.get('n_records', 0)})")
    if point_stats:
        print(f"Loaded point stats ({point_stats.get('n_articles_used', 0)} articles, "
              f"baseline={point_stats.get('baseline_rate')})")

    # 実績事前確率のキャッシュ: (tide_name, month, flow) → {point: prior}, {point: detail}
    _prior_cache = {}

    def priors_for(tide_name, month, flow):
        key = (tide_name, month, flow)
        if key in _prior_cache:
            return _prior_cache[key]
        pri, det = {}, {}
        for pname in POINTS:
            p, d = empirical_prior_for(point_stats, pname, tide_name, month, flow)
            pri[pname] = round(p, 3)
            if d:
                det[pname] = d
        _prior_cache[key] = (pri, det)
        return pri, det

    m_h = marine["hourly"]
    w_h = weather["hourly"]

    times = m_h["time"]
    ssts = m_h["sea_surface_temperature"]
    cur_v = m_h["ocean_current_velocity"]  # km/h
    cur_d = m_h["ocean_current_direction"]  # °
    waves = m_h["wave_height"]
    sea_lvl = m_h["sea_level_height_msl"]
    winds = w_h["wind_speed_10m"]
    wind_dir = w_h["wind_direction_10m"]
    precips = w_h["precipitation"]

    # SST欠損値の補完 + 現地ブログ実測での補正
    #  - 実測がある日: 実測の水温平均を優先
    #  - Open-Meteoは沖合8km格子で神子元近辺の冷水塊を捉えられないため、
    #    実測は補正の精度を大きく上げる
    #  - 未来日: Open-Meteoを使用しつつ、直近の実測との乖離ぶんをオフセット補正
    last_valid = None
    ssts_filled = []
    # 直近実測水温 (最新5日平均)
    recent_obs_temps = []
    for d, s in sorted(observations.items())[-5:]:
        if s.get("water_temp_lo") is not None and s.get("water_temp_hi") is not None:
            recent_obs_temps.append((s["water_temp_lo"] + s["water_temp_hi"]) / 2)
    # 現地実測 vs Open-Meteo予測 の差(オフセット)
    obs_om_offset = 0.0
    if recent_obs_temps:
        # 過去5日のOpen-Meteoの平均を計算 (ssts の最初の日々)
        recent_om_temps = []
        obs_dates = sorted(observations.keys())[-5:]
        for i_t, t in enumerate(times):
            date_only = t[:10]
            if date_only in obs_dates:
                if ssts[i_t] is not None:
                    recent_om_temps.append(ssts[i_t])
        if recent_om_temps:
            om_avg = statistics.mean(recent_om_temps)
            obs_avg = statistics.mean(recent_obs_temps)
            obs_om_offset = obs_avg - om_avg
            print(f"  観測平均SST={obs_avg:.2f}℃, Open-Meteo平均={om_avg:.2f}℃, offset={obs_om_offset:+.2f}℃")

    for i, s in enumerate(ssts):
        date_only = times[i][:10]
        # 実測日はSSTを実測平均で置換
        if date_only in observations:
            obs = observations[date_only]
            if obs.get("water_temp_lo") is not None and obs.get("water_temp_hi") is not None:
                ssts_filled.append((obs["water_temp_lo"] + obs["water_temp_hi"]) / 2)
                continue
        # Open-Meteo値を使用、ただし未来日は最近offsetで補正
        if s is not None:
            corrected = s + obs_om_offset  # 実測との乖離を全体に適用
            ssts_filled.append(corrected)
            last_valid = corrected
        elif last_valid is not None:
            ssts_filled.append(last_valid)
        else:
            dt = datetime.fromisoformat(times[i])
            key = f"{dt.month:02d}-{dt.day:02d}"
            fallback = clim["daily_climatology"].get(key, {}).get("mean", 22.0)
            ssts_filled.append(fallback)

    # 潮汐流 u成分の推定: 神子元では潮汐流は東西方向支配
    # 下げ潮 (sea_lvl 下降): E→W 流 (向かう先=W=270°), 潮汐 u < 0
    # 上げ潮 (sea_lvl 上昇): W→E 流 (向かう先=E=90°), 潮汐 u > 0
    # dh/dt (m/h) をスケール変換して kt に (経験式: 神子元大潮で最大1.5kt≈2.8km/h)
    def sea_level_derivative(i):
        def s(k):
            if 0 <= k < len(sea_lvl) and sea_lvl[k] is not None:
                return sea_lvl[k]
            return None
        a, c = s(i-1), s(i+1)
        b = s(i)
        if c is not None and a is not None:
            return (c - a) / 2.0
        if c is not None and b is not None:
            return c - b
        if b is not None and a is not None:
            return b - a
        return 0.0

    # 時間別レコード
    hourly_records = []
    for i, t in enumerate(times):
        dt = datetime.fromisoformat(t)
        m, d, h = dt.month, dt.day, dt.hour
        sst_used = ssts_filled[i]
        sst_score = score_sst(sst_used, m, d, clim)
        sst_score["sst_is_extrapolated"] = ssts[i] is None

        # Open-Meteo の (velocity, direction) を u,v に分解
        base_v = cur_v[i] or 0
        base_d = cur_d[i] if cur_d[i] is not None else 0
        base_u_east, base_v_north = deg_to_vec(base_d)
        base_u_east *= base_v
        base_v_north *= base_v

        # 潮汐流を東西方向に加算 (神子元の潮汐流は東西支配的)
        # 現地観測との校正 (2026-08-23 ユーザー実測: 上げ潮ピークで東→西 1.5-8kt = 2.8-14.8 km/h)
        # ここで方向が「上げ潮で東→西」なのは、島南側では上げ潮の北からの流入が
        # 島の東を回り込んで南岸で反時計回りに合成される（岬効果）ため。
        # 実装: 上げ潮(dh>0)は「南岸で東→西」の流れを表すため tidal_u_east を負に
        #       下げ潮(dh<0)は「南岸で西→東」に対応するため tidal_u_east を正に
        dh = sea_level_derivative(i)  # m/hour, 正 = 上げ潮
        # スケール: 大潮ピーク dh ≈ 0.4 m/h → tidal_u ≈ 8 km/h ≈ 4kt
        # ユーザー実測 8kt はスプリングタイド極値なのでピーク時のみ到達
        tidal_u_east = -dh * 20.0  # km/h, 上げ潮で東→西 (負)

        combined_u = base_u_east + tidal_u_east
        combined_v = base_v_north
        combined_speed = math.hypot(combined_u, combined_v)
        combined_dir = math.degrees(math.atan2(combined_u, combined_v)) % 360
        cur_s = score_current(combined_speed)
        wea_s, wea_note = score_weather(winds[i] or 0, waves[i] or 0, precips[i] or 0)
        tide = infer_tide_direction(sea_lvl, i)
        # 温水方向 = base_current (Open-Meteo/黒潮寄与) の来る側
        warm_dir = (base_d + 180) % 360 if base_v > 0.3 else None
        # ムロアジプロキシ: 当日 or 直近3日のブログログに 'muroaji' 出現があれば+
        date_str = t[:10]
        muroaji_prior = 0.5
        if date_str in observations:
            trop = observations[date_str].get("tropical_species", [])
            if "muroaji" in trop:
                muroaji_prior = 0.9
            elif trop:
                muroaji_prior = 0.65
        else:
            # 未来日: 直近3日で muroaji が出た比率
            recent_muroaji = 0
            recent_days = 0
            for back in range(1, 4):
                d_back = (datetime.fromisoformat(date_str) - timedelta(days=back)).date().isoformat()
                if d_back in observations:
                    recent_days += 1
                    if "muroaji" in observations[d_back].get("tropical_species", []):
                        recent_muroaji += 1
            if recent_days > 0:
                muroaji_prior = 0.5 + 0.4 * (recent_muroaji / recent_days)

        # 実績事前確率: 潮名(月齢カレンダー) × 月 × 潮汐フェーズ(上げ/下げ)
        tide_name = moon_tide.get(date_str, {}).get("tide_name")
        flow = tide if tide in ("up", "down") else None
        emp_priors, emp_details = priors_for(tide_name, m, flow)

        point_scores = compute_point_scores(combined_dir, combined_speed, warm_dir,
                                            muroaji_prior, empirical_priors=emp_priors)
        # 統合スコア (0-100)
        base = (
            0.45 * sst_score["score"]
            + 0.25 * cur_s
            + 0.20 * wea_s
            + 0.10 * season_bonus(m)
        )
        raw_score = round(base * 100, 1)
        hourly_records.append({
            "time": t,
            "hour": h,
            "sst": sst_score["sst"],
            "sst_climatology": sst_score["climatology"],
            "sst_anomaly": sst_score["anomaly"],
            "f_sst": sst_score["score"],
            "base_current_velocity_kmh": round(base_v, 2),
            "base_current_direction": round(base_d, 0) if cur_d[i] is not None else None,
            "tidal_u_east_kmh": round(tidal_u_east, 2),
            "current_velocity_kmh": round(combined_speed, 2),
            "current_direction": round(combined_dir, 0),
            "f_current": round(cur_s, 3),
            "wave_height": round(waves[i] or 0, 2),
            "wind_ms": round(winds[i] or 0, 1),
            "wind_direction": round(wind_dir[i] or 0, 0) if wind_dir[i] is not None else None,
            "precipitation": round(precips[i] or 0, 2),
            "f_weather": round(wea_s, 2),
            "weather_note": wea_note,
            "sea_level": round(sea_lvl[i] or 0, 3),
            "tide": tide,
            "point_scores": point_scores,
            "empirical_priors": emp_priors,
            "tide_name": tide_name,
            "warm_water_dir": warm_dir,
            "score": raw_score,
        })

    # 日別サマリ (ダイビング時間 8-14時のみ)
    daily = {}
    for r in hourly_records:
        d = r["time"][:10]
        h = r["hour"]
        if 8 <= h <= 14:
            daily.setdefault(d, []).append(r)

    daily_records = []
    for d, rs in sorted(daily.items()):
        best = max(rs, key=lambda x: x["score"])
        avg_score = round(statistics.mean(x["score"] for x in rs), 1)
        avg_sst = round(statistics.mean(x["sst"] for x in rs), 2)
        avg_anom = round(statistics.mean(x["sst_anomaly"] for x in rs), 2)
        avg_cur = round(statistics.mean(x["current_velocity_kmh"] for x in rs), 2)
        max_wave = round(max(x["wave_height"] for x in rs), 2)
        max_wind = round(max(x["wind_ms"] for x in rs), 1)
        diveable = all(x["f_weather"] > 0 for x in rs)
        best_shadow = best["point_scores"]
        best_point = max(best_shadow.items(), key=lambda kv: kv[1])
        # 推奨根拠: best_point の実績事前確率の内訳
        best_tide_name = best.get("tide_name")
        best_flow = best.get("tide") if best.get("tide") in ("up", "down") else None
        _, det_map = priors_for(best_tide_name, int(d[5:7]), best_flow)
        best_point_evidence = det_map.get(best_point[0])
        # 上位3ポイント (UIの番号バッジ用)
        top3 = sorted(best_shadow.items(), key=lambda kv: -kv[1])[:3]

        # === 実測データからの追加情報 ===
        obs = observations.get(d)
        f_vis = 0.5
        f_tropical = 0.3  # 中立default
        actual_hammer = None
        actual_size = None
        actual_vis = None
        recent_actual_vis = None
        actual_notes = []
        actual_hammer_points = []
        actual_tropical = []
        actual_segments = []
        if obs:
            f_vis = score_visibility(obs.get("visibility_hi"))
            actual_hammer = obs.get("hammer_seen")
            actual_size = obs.get("hammer_size")
            if obs.get("visibility_lo") is not None:
                actual_vis = f"{obs['visibility_lo']}-{obs['visibility_hi']}m"
            actual_notes = obs.get("notes", [])
            actual_hammer_points = obs.get("hammer_points", [])
            actual_tropical = obs.get("tropical_species", [])
            actual_segments = obs.get("segments", [])
            f_tropical = min(1.0, obs.get("tropical_count", 0) / 3.0)
        else:
            # 未来日は直近3日の透明度・南方系プロキシを prior として使用
            recent_vis = []
            recent_trop = []
            for obs_d, s in sorted(observations.items())[-3:]:
                if s.get("visibility_hi") is not None:
                    recent_vis.append(s["visibility_hi"])
                recent_trop.append(min(1.0, s.get("tropical_count", 0) / 3.0))
            if recent_vis:
                recent_actual_vis = statistics.mean(recent_vis)
                f_vis = score_visibility(recent_actual_vis)
            if recent_trop:
                f_tropical = statistics.mean(recent_trop)

        # === 直近3日の目撃連続数 (f_recent_actual) ===
        d_dt = datetime.fromisoformat(d).date()
        recent_seen_count = 0
        for back in range(1, 4):
            prev_d = (d_dt - timedelta(days=back)).isoformat()
            if prev_d in observations and observations[prev_d].get("hammer_seen"):
                recent_seen_count += 1
        f_recent = recent_seen_count / 3.0

        # === 学習済みモデル予測 ===
        f_sst_sig = 1.0 / (1.0 + math.exp(-avg_anom / 1.5))  # 平年偏差のsigmoid
        rw = weights.get("raw_weights", {})
        model_y = weights.get("bias", 0) + \
                  rw.get("f_sst_anomaly", 0) * f_sst_sig + \
                  rw.get("f_visibility", 0) * f_vis + \
                  rw.get("f_tropical", 0) * f_tropical + \
                  rw.get("f_recent_actual", 0) * f_recent
        # 0-100 スケールに変換 (target は 0-1 の "規模スコア")
        learned_score = max(0, min(100, model_y * 100))

        # スコアに透明度と暖水偏差ボーナスを加味
        # 透明度: 15m以上なら +5点、5m以下なら -10点
        vis_bonus = 0
        if f_vis > 0.85:
            vis_bonus = 5
        elif f_vis > 0.6:
            vis_bonus = 2
        elif f_vis < 0.3:
            vis_bonus = -10
        elif f_vis < 0.5:
            vis_bonus = -5

        # 南方系魚類/暖水ボーナス, 冷水塊ペナルティ
        note_adj = 0
        if "tropical_species" in actual_notes:
            note_adj += 3
        if "cold_water_present" in actual_notes:
            note_adj -= 4
        if "turbid_layer" in actual_notes:
            note_adj -= 3
        if "blue_water" in actual_notes:
            note_adj += 3
        # 過去実績の直接反映: 実測でハンマー見えた日 → 明日のprior高い
        # (単純に "3日連続で見えている場所" は今日も出る)

        # 生スコア(海況+潮流+SST)と学習モデルスコア(実測ベース)のブレンド
        # 実測データがある日は学習モデル重視 (70:30)、無い日は生スコア重視 (30:70)
        blend = 0.7 if obs else 0.3
        adjusted_score = max(0, min(100,
            (1 - blend) * (avg_score + vis_bonus + note_adj) +
            blend * learned_score
        ))

        # === 新ランク定義 (S-E) ===
        # 透明度 × 群れ規模のマトリクス (ユーザー現地肌感)
        #   S: 透明度≥20m + 群れ≥100匹 (究極, 青い水+大群)
        #   A: 透明度<20m + 群れ≥100匹 (透明度普通+大群)
        #   B: 透明度≥20m + 群れ50-100匹 (青い水+中群)
        #   C: 透明度≥15m + 群れ20-50匹 (中規模)
        #   D: 透明度≥10m + 単体〜10匹 (見えれば幸運)
        #   E: 透明度<10m or 目撃なし (濁り/不在)
        tier, tier_label = classify_rank(
            visibility_hi=obs.get("visibility_hi") if obs else recent_actual_vis,
            hammer_seen=actual_hammer,
            hammer_size=actual_size,
            adjusted_score=adjusted_score,
            has_actual=obs is not None,
        )

        daily_records.append({
            "date": d,
            "score": round(adjusted_score, 1),
            "base_score": avg_score,
            "learned_score": round(learned_score, 1),
            "vis_bonus": vis_bonus,
            "note_adj": note_adj,
            "tier": tier,
            "tier_label": tier_label,
            "diveable": diveable,
            "avg_sst": avg_sst,
            "avg_anomaly": avg_anom,
            "avg_current_kmh": avg_cur,
            "max_wave": max_wave,
            "max_wind": max_wind,
            "best_hour": best["hour"],
            "best_point": best_point[0],
            "best_point_score": best_point[1],
            "best_point_evidence": best_point_evidence,
            "tide_name": best_tide_name,
            "tide_phase": best.get("tide"),
            "top3_points": [{"point": k, "score": v} for k, v in top3],
            # 学習モデル用の因子
            "f_sst_sig": round(f_sst_sig, 3),
            "f_visibility": round(f_vis, 3),
            "f_tropical": round(f_tropical, 3),
            "f_recent_actual": round(f_recent, 3),
            # 実測データ
            "actual_visibility": actual_vis,
            "actual_hammer_seen": actual_hammer,
            "actual_hammer_size": actual_size,
            "actual_notes": actual_notes,
            "actual_hammer_points": actual_hammer_points,
            "actual_tropical": actual_tropical,
            "actual_segments": actual_segments,
            "note": build_daily_note(avg_anom, avg_cur, max_wave, max_wind, best_point,
                                     actual_vis, actual_hammer, actual_notes),
        })

    # 過去実測ログ (新しい順に)
    recent_obs = []
    for d, s in sorted(observations.items(), reverse=True)[:14]:
        vis = f"{s['visibility_lo']}-{s['visibility_hi']}m" if s.get("visibility_lo") is not None else None
        wt = f"{s['water_temp_lo']}-{s['water_temp_hi']}℃" if s.get("water_temp_lo") is not None else None
        recent_obs.append({
            "date": d,
            "hammer_seen": s.get("hammer_seen"),
            "hammer_size": s.get("hammer_size"),
            "visibility": vis,
            "water_temp": wt,
            "tropical_count": s.get("tropical_count", 0),
            "notes": s.get("notes", []),
            "shops_reporting": s.get("shops_reporting", 0),
        })

    # 潮汐ヒートマップ
    tide_heat = {}
    tide_heatmap_path = os.path.join(os.path.dirname(BLOG_PATH), "tide_hammer_heatmap.json")
    if os.path.exists(tide_heatmap_path):
        try:
            with open(tide_heatmap_path) as f:
                tide_heat = json.load(f)
        except Exception:
            pass

    result = {
        "generated_at": datetime.now().isoformat(),
        "location": {"lat": LAT, "lon": LON, "name": "神子元島"},
        "climatology_source": clim["source"],
        "climatology_years": clim["years_used"],
        "learned_weights": weights,
        "point_stats_meta": {
            "baseline_rate": point_stats.get("baseline_rate") if point_stats else None,
            "n_articles": point_stats.get("n_articles_used") if point_stats else None,
        },
        "recent_observations": recent_obs,
        "tide_heatmap": tide_heat,
        "daily": daily_records,
        "hourly": hourly_records,
    }
    with open(OUT_JSON, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUT_JSON}")
    # print summary
    print("\n=== Daily forecast (8-14時ダイビング時間) ===")
    print(f"{'Date':<12} {'Tier':<4} {'Score':>6} {'SST':>6} {'Anom':>6} {'Cur':>6} {'Wave':>5} {'Wind':>5} Best Point")
    for r in daily_records:
        print(f"{r['date']:<12} {r['tier']:<4} {r['score']:>6.1f} "
              f"{r['avg_sst']:>6.2f} {r['avg_anomaly']:>+6.2f} "
              f"{r['avg_current_kmh']:>6.2f} {r['max_wave']:>5.2f} "
              f"{r['max_wind']:>5.1f} {r['best_point']}")


def build_daily_note(anom, cur, wave, wind, best, actual_vis=None, actual_hammer=None, actual_notes=None):
    parts = []
    # 実測ノート優先
    if actual_hammer is True:
        parts.append("◎現地目撃実績あり")
    elif actual_hammer is False:
        parts.append("△現地目撃なし")
    if actual_vis:
        parts.append(f"実測透明度{actual_vis}")
    else:
        if anom > 1.5:
            parts.append(f"平年+{anom:.1f}℃◎")
        elif anom > 0:
            parts.append(f"平年+{anom:.1f}℃")
        elif anom > -1.5:
            parts.append(f"平年{anom:.1f}℃")
        else:
            parts.append(f"平年{anom:.1f}℃ 冷水")
    if cur < 0.5:
        parts.append("流れ穏やか")
    elif cur < 1.5:
        parts.append("適度な流れ◎")
    elif cur < 3:
        parts.append("やや強い流れ")
    else:
        parts.append("激流注意")
    if wave > 2:
        parts.append("欠航濃厚")
    elif wave > 1.5:
        parts.append("海況悪化")
    if wind > 12:
        parts.append("強風欠航")
    if actual_notes:
        if "cold_water_present" in actual_notes:
            parts.append("冷水塊あり")
        if "tropical_species" in actual_notes:
            parts.append("南方系魚類◎")
    return " / ".join(parts)


if __name__ == "__main__":
    run()
