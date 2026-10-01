"""
スクレイプしたブログ本文を LLM (gsk summarize) で構造化
ダイブごとに: 時間帯・ポイント・ハンマー目撃・規模・場所

出力: dive_logs_structured.json
"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent


def normalize_hammers_table(t):
    """神子元ハンマーズのテーブルキーを正規化"""
    n = {}
    for k, v in t.items():
        k_l = k.lower()
        if "気温" in k or "temperature" in k_l and "water" not in k_l:
            n["air"] = v
        elif "風向" in k or "wind" in k_l:
            n["wind"] = v
        elif "水温" in k or "water" in k_l:
            m = re.search(r"([\d.]+)\s*[~〜～\-−]\s*([\d.]+)", v)
            if m:
                n["water_temp_lo"] = float(m.group(1))
                n["water_temp_hi"] = float(m.group(2))
            else:
                m = re.search(r"(\d+\.?\d*)", v)
                if m:
                    n["water_temp_lo"] = n["water_temp_hi"] = float(m.group(1))
        elif "波" in k or "wave" in k_l:
            m = re.search(r"([\d.]+)", v)
            if m:
                # "0.5 mm" のような表記ミスを補正
                val = float(m.group(1))
                if "mm" in v.lower() and val < 5:
                    n["wave_m"] = val
                else:
                    n["wave_m"] = val
        elif "透明度" in k or ("visibility" in k_l and "top" in k_l):
            m = re.search(r"([\d.]+)\s*[~〜～\-−]\s*([\d.]+)", v)
            if m:
                n["vis_lo"] = float(m.group(1))
                n["vis_hi"] = float(m.group(2))
            else:
                m = re.search(r"([\d.]+)", v)
                if m:
                    n["vis_lo"] = n["vis_hi"] = float(m.group(1))
        elif "ダイビング" in k and "point" in k_l.lower() or "diving point" in k_l:
            n["points_raw"] = v
    return n


HAMMER_TRUE_KWS = ["ハンマー", "群れ", "ハンマーゲット", "ハンマー登場", "ハンマー炸裂",
                    "hammer", "hammerhead", "リバー"]
HAMMER_FALSE_KWS = ["ハンマーには出会えません", "ノーハンマー", "見れず", "会えず",
                    "ハンマー不在", "no hammer", "見られませんでした", "残念ながら"]

# 時間帯検出パターン (優先: 明確な"N本目" > "午前/朝" > "午後")
TIME_SEGMENTS = [
    # (regex, segment_key, hour_range)
    (r"(1本目|一本目|朝イチ|朝一|first dive)", "morning", (8, 10)),
    (r"(2本目|二本目|second dive)", "midday", (10, 12)),
    (r"(3本目|三本目|4本目|四本目|third dive|fourth dive|ラスト)", "afternoon", (12, 14)),
    (r"(午前|morning)", "morning", (8, 11)),
    (r"(昼過ぎ|昼|midday)", "midday", (10, 12)),
    (r"(午後|afternoon|夕方)", "afternoon", (12, 14)),
]

# 南方系(黒潮)魚類
TROPICAL_SPECIES = [
    ("シイラ", "dorado"), ("ドラード", "dorado"),
    ("ツムブリ", "tsumburi"),
    ("カマスサワラ", "wahoo"), ("ワフー", "wahoo"),
    ("ムロアジ", "muroaji"), ("クサヤモロ", "kusaya"),
    ("ハガツオ", "hagatsuo"), ("カツオ", "katsuo"),
    ("マンボウ", "mola"),  ("マンタ", "manta"),
    ("トビエイ", "tobiei"),
    ("イスズミ", "isuzumi"), ("キハダ", "kihada"),
    ("メジロザメ", "mejiro"), ("カマストガリザメ", "kamasu"),
    ("ニタリ", "nitari"),
]

# 潮の状態を時間帯別に分解
TIDE_CHANGE_PATTERNS = [
    (r"(上げ|上潮|上り|入り|入り潮).{0,20}(始|入る|入りかけ|入り出|入り始め)", "up_start"),
    (r"(下げ|下潮|下り).{0,20}(始|入る|入りかけ|入り出|入り始め)", "down_start"),
    (r"(切り替わ|変わ|逆流|反転)", "reversal"),
    (r"(緩め|緩やか|穏やか|弱い|止まり|slack)", "weak"),
    (r"(激流|強い|爆流|びゅんびゅん|強め)", "strong"),
]

SCHOOL_SIZE_PATTERNS = [
    (r"(大群|超大群|ハンマーリバー|炸裂|群れ群れ)", "large"),
    (r"(小群れ|小さな群れ|数匹|チラホラ|少しずつ|ちらほら)", "small"),
    (r"(群れ)", "medium"),
    (r"(単体|1匹|1個体|solitary)", "single"),
]

# 現地ポイント名 → 標準キー
POINT_MAP = {
    "カメ根": "kame_ne", "Aポイント": "A_point", "A ポイント": "A_point", "A": "A_point",
    "青根": "jab_ne", "ジャブ根": "jab_ne",
    "三ツ根": "mitsu_ne", "三つ根": "mitsu_ne", "ミツネ": "mitsu_ne",
    "カド根": "kado_ne", "江の口": "eno_kuchi", "江ノ口": "eno_kuchi",
    "ザブ根": "zabu_ne", "アンドロ": "andoro",
    "ハンマーズロック": "hammers_rock",
    "プレート": "plate",
    "三つ目の高根": "third_takane", "3rdの高根": "third_takane",
    "トビエイロック": "eagleray_rock",
    "西の高根": "west_takane", "西の根": "west_rock",
    "ツインピークス": "twin_peaks", "ビューポイント": "view_point", "カベ根": "kabe_ne",
    "北の根": "kita_ne", "東の根": "higashi_ne", "カリトの鼻": "karito",
}


def extract_hammer_info(body):
    """本文から目撃情報を抽出 (時間帯・ポイント別セグメント含む)"""
    if not body:
        return {"seen": None, "size": None, "notes": [], "segments": [], "tropical": []}
    seen = None
    for kw in HAMMER_FALSE_KWS:
        if kw in body:
            seen = False
            break
    if seen is None:
        for kw in HAMMER_TRUE_KWS:
            if kw in body:
                seen = True
                break

    size = None
    for pat, s in SCHOOL_SIZE_PATTERNS:
        if re.search(pat, body):
            size = s
            break

    # ポイント名検出
    points = []
    for jp, key in POINT_MAP.items():
        if jp in body:
            points.append(key)

    # 南方系(黒潮)魚類の検出
    tropical_hits = []
    for jp, key in TROPICAL_SPECIES:
        if jp in body:
            tropical_hits.append(key)

    # 目立った特徴
    notes = []
    if "冷たい" in body or "冷水" in body or "cold" in body.lower():
        notes.append("cold_water_present")
    if "濁" in body or "白っぽ" in body or "murky" in body.lower():
        notes.append("turbid_layer")
    if "青" in body or "ブルー" in body or "clear blue" in body.lower():
        notes.append("blue_water")
    if "激流" in body or "爆流" in body or "強い流れ" in body:
        notes.append("strong_current")
    if "緩" in body or "穏やか" in body:
        notes.append("weak_current")
    if tropical_hits:
        notes.append("tropical_species")

    # 時間帯セグメント抽出: 本文を大まかに前半/中/後半に分割し
    # 各セグメントで「ハンマー言及」「潮の状態」を検出
    segments = extract_time_segments(body)

    return {
        "seen": seen,
        "size": size,
        "points_mentioned": list(set(points)),
        "tropical": tropical_hits,
        "notes": notes,
        "segments": segments,
    }


def extract_time_segments(body):
    """本文を時間帯マーカーで分割し、各セグメントで詳細情報を抽出"""
    # 時間帯マーカー位置を検出
    markers = []
    for pat, seg, hr_range in TIME_SEGMENTS:
        for m in re.finditer(pat, body):
            markers.append((m.start(), seg, hr_range))
    if not markers:
        return []
    markers.sort()

    # 重複除去 (連続する同じセグメント)
    dedup = []
    seen_segs = set()
    for pos, seg, hr in markers:
        if seg in seen_segs:
            continue
        dedup.append((pos, seg, hr))
        seen_segs.add(seg)

    # 各セグメントのテキスト範囲を確定 (次のマーカーまで、最大500文字)
    results = []
    for i, (pos, seg, hr) in enumerate(dedup):
        end = dedup[i+1][0] if i+1 < len(dedup) else min(pos + 500, len(body))
        chunk = body[pos:end]
        # ハンマー言及
        hammer_here = None
        for kw in HAMMER_FALSE_KWS:
            if kw in chunk:
                hammer_here = False
                break
        if hammer_here is None:
            for kw in HAMMER_TRUE_KWS:
                if kw in chunk:
                    hammer_here = True
                    break
        # 潮の状態
        tide_state = None
        for pat, ts in TIDE_CHANGE_PATTERNS:
            if re.search(pat, chunk):
                tide_state = ts
                break
        # ポイント
        pts = []
        for jp, key in POINT_MAP.items():
            if jp in chunk:
                pts.append(key)
        results.append({
            "segment": seg,
            "hour_range": hr,
            "hammer_here": hammer_here,
            "tide_state": tide_state,
            "points": list(set(pts)),
            "chunk_preview": chunk[:120],
        })
    return results


def structure_articles():
    data = json.load(open(ROOT / "dive_logs_raw.json"))
    logs = []
    for a in data["articles"]:
        entry = {
            "date": a.get("date"),
            "shop": a.get("shop"),
            "url": a.get("url"),
        }
        # 数値データを統合
        if "raw_table" in a:
            entry.update(normalize_hammers_table(a["raw_table"]))
        if "raw" in a:
            for k, v in a["raw"].items():
                if k not in entry:
                    entry[k] = v
        # 目撃情報
        entry["hammer"] = extract_hammer_info(a.get("body", ""))
        logs.append(entry)

    # 日付でソート
    logs = sorted(logs, key=lambda x: (x.get("date") or "", x.get("shop") or ""))
    # 日別集約
    daily = {}
    for e in logs:
        d = e.get("date")
        if not d: continue
        daily.setdefault(d, []).append(e)

    # 集約サマリ生成
    daily_summary = []
    for d, reports in sorted(daily.items()):
        # 水温レンジ
        wts_lo = [r.get("water_temp_lo") for r in reports if r.get("water_temp_lo") is not None]
        wts_hi = [r.get("water_temp_hi") for r in reports if r.get("water_temp_hi") is not None]
        vis_lo = [r.get("vis_lo") or r.get("visibility_lo") for r in reports]
        vis_lo = [v for v in vis_lo if v is not None]
        vis_hi = [r.get("vis_hi") or r.get("visibility_hi") for r in reports]
        vis_hi = [v for v in vis_hi if v is not None]

        # 目撃投票
        seen_votes = [r["hammer"].get("seen") for r in reports if r["hammer"].get("seen") is not None]
        seen_true = sum(1 for s in seen_votes if s)
        seen_any = seen_true > 0

        sizes = [r["hammer"].get("size") for r in reports if r["hammer"].get("size")]
        # 最大規模
        size_rank = {"large": 3, "medium": 2, "small": 1, "single": 0}
        best_size = max(sizes, key=lambda s: size_rank.get(s, -1)) if sizes else None

        # ポイントヒット
        pts = []
        for r in reports:
            pts.extend(r["hammer"].get("points_mentioned", []))
        from collections import Counter
        pts_counter = Counter(pts)

        # 総合ノート
        notes = set()
        tropicals = set()
        for r in reports:
            for n in r["hammer"].get("notes", []):
                notes.add(n)
            for t in r["hammer"].get("tropical", []):
                tropicals.add(t)

        # 全セグメントを統合 (同一時間帯は複数ショップの結果を集約)
        segment_agg = {}  # {"morning": {"hammer_votes":[True,True],"tide":[],"points":[]}}
        for r in reports:
            for seg in r["hammer"].get("segments", []):
                s = seg["segment"]
                if s not in segment_agg:
                    segment_agg[s] = {"hammer_votes": [], "tide_states": [], "points": [],
                                       "hour_range": seg["hour_range"]}
                if seg.get("hammer_here") is not None:
                    segment_agg[s]["hammer_votes"].append(seg["hammer_here"])
                if seg.get("tide_state"):
                    segment_agg[s]["tide_states"].append(seg["tide_state"])
                segment_agg[s]["points"].extend(seg.get("points", []))
        # サマリ化
        segments_summary = []
        for s, agg in sorted(segment_agg.items(), key=lambda kv: kv[1]["hour_range"][0]):
            hammer_true = sum(1 for v in agg["hammer_votes"] if v)
            hammer_total = len(agg["hammer_votes"])
            from collections import Counter as Cnt
            top_pt = Cnt(agg["points"]).most_common(2)
            top_tide = Cnt(agg["tide_states"]).most_common(1)
            segments_summary.append({
                "segment": s,
                "hour_range": agg["hour_range"],
                "hammer_ratio": hammer_true / hammer_total if hammer_total else None,
                "hammer_reports": f"{hammer_true}/{hammer_total}",
                "dominant_tide": top_tide[0][0] if top_tide else None,
                "top_points": [p[0] for p in top_pt],
            })

        daily_summary.append({
            "date": d,
            "shops_reporting": len(reports),
            "water_temp_lo": min(wts_lo) if wts_lo else None,
            "water_temp_hi": max(wts_hi) if wts_hi else None,
            "visibility_lo": min(vis_lo) if vis_lo else None,
            "visibility_hi": max(vis_hi) if vis_hi else None,
            "hammer_seen": seen_any,
            "hammer_size": best_size,
            "hammer_points": pts_counter.most_common(5),
            "tropical_species": sorted(tropicals),
            "tropical_count": len(tropicals),
            "notes": sorted(notes),
            "segments": segments_summary,
        })

    out = {
        "generated_at": datetime.now().isoformat(),
        "n_articles": len(logs),
        "n_days": len(daily),
        "logs": logs,
        "daily_summary": daily_summary,
    }
    with open(ROOT / "dive_logs_structured.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Structured {len(logs)} logs across {len(daily)} days")
    # プリント
    print(f"\n=== 日別サマリ (最新10日) ===")
    print(f"{'Date':<12} {'Shops':>5} {'水温':>10} {'透明度':>10} {'目撃':>4} {'規模':>7} {'Notes'}")
    for s in daily_summary[-10:]:
        wt = f"{s['water_temp_lo']}-{s['water_temp_hi']}" if s['water_temp_lo'] else "-"
        vs = f"{s['visibility_lo']}-{s['visibility_hi']}" if s['visibility_lo'] else "-"
        seen = "○" if s["hammer_seen"] else "✕"
        sz = s["hammer_size"] or "-"
        print(f"{s['date']:<12} {s['shops_reporting']:>5} {wt:>10} {vs:>10} {seen:>4} {sz:>7} {','.join(s['notes'][:3])}")
    return out


if __name__ == "__main__":
    structure_articles()
