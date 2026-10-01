"""
Phase1-1b: dive_logs_raw_full.json -> dive_logs_structured_full.json
repo/blog_structure.py のロジックを改良:
 - 否定文脈の検出を強化 (不発/現れず/出ず/会えず/姿はなく 等 + 「ハンマー」近傍の否定)
 - 日別サマリ (hammer_seen, hammer_size, hammer_points, water_temp, visibility,
   tropical_species, 潮回りメモ)
"""
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

# ---------------- ハンマー検出 ----------------
HAMMER_KW = re.compile(r"(ハンマー|hammerhead|hammer|シュモクザメ|撞木)", re.I)

# 文単位の否定パターン (ハンマー言及と同一文にあれば否定とみなす)
NEG_PATTERNS = [
    r"不発", r"現れ(ず|ません|なかった)", r"出(ず|ませんでした|なかった|てくれず)",
    r"会え(ず|ません|なかった)", r"出会え(ず|ません|なかった)",
    r"姿(は|が)?(なく|見えず|見られず|ありません)", r"見(られ|れ)(ず|ません|なかった)",
    r"見つけられ(ず|ません|なかった)", r"逢え(ず|ません|なかった)",
    r"遭遇でき(ず|ません|なかった)", r"ノーハンマー", r"no\s*hammer",
    r"空振り", r"振られ", r"すかされ", r"externals?み(られ)?ず",
    r"駄目|ダメでした", r"残念ながら", r"叶(わず|いません)", r"お預け",
    r"ハンマー不在", r"はずれ", r"ハズレ", r"惨敗", r"撃沈",
    r"狙(い|う)ましたが", r"探し(ました|た)が",
]
NEG_RE = re.compile("|".join(NEG_PATTERNS))

# 強い肯定 (数量・群れ表現があれば肯定確定)
POS_STRONG = re.compile(
    r"(ハンマー(リバー|の群れ|の?大群|川|シャワー|玉|渦)|"
    r"\d{2,}\s*(匹|本|個体)|数十|百|群れ群れ|炸裂|乱舞|"
    r"ハンマー(を|も)?\s*(ゲット|ヒット|激写|キャッチ)|"
    r"ハンマー(登場|出現|遭遇|出ました|見れました|見られました|に会えました|に出会えました|づくし|三昧))")

SIZE_PATTERNS = [
    (r"(3[0-9]{2,}|[3-9][0-9]{2}匹|\d{3,}\s*(匹|本)|大群|超大群|ハンマーリバー|ハンマー川|炸裂|乱舞|群れ群れ|数百)", "large"),
    (r"(数十|[2-9]0\s*(匹|本)|1[0-9]\s*(匹|本)|群れ)", "medium"),
    (r"(数匹|[2-9]\s*(匹|本)|チラホラ|ちらほら|小さな群れ|小群れ)", "small"),
    (r"(単体|1\s*(匹|本|個体)|一匹|ソロ)", "single"),
]

TROPICAL_SPECIES = [
    ("シイラ", "dorado"), ("ドラード", "dorado"), ("ツムブリ", "tsumburi"),
    ("カマスサワラ", "wahoo"), ("ワフー", "wahoo"), ("ムロアジ", "muroaji"),
    ("クサヤモロ", "kusaya"), ("ハガツオ", "hagatsuo"), ("カツオ", "katsuo"),
    ("マンボウ", "mola"), ("マンタ", "manta"), ("トビエイ", "tobiei"),
    ("イスズミ", "isuzumi"), ("キハダ", "kihada"), ("メジロザメ", "mejiro"),
    ("カマストガリザメ", "kamasu"), ("ニタリ", "nitari"), ("ジンベエ", "whale_shark"),
    ("バショウカジキ", "sailfish"), ("カジキ", "marlin"),
]

POINT_MAP = {
    "カメ根": "kame_ne", "亀根": "kame_ne", "KAMENE": "kame_ne",
    "Aポイント": "A_point", "A ポイント": "A_point",
    "青根": "ao_ne", "ジャブ根": "jab_ne", "ザブ根": "zabu_ne", "ZABUNE": "zabu_ne",
    "三ツ根": "mitsu_ne", "三つ根": "mitsu_ne", "ミツネ": "mitsu_ne",
    "カド根": "kado_ne", "江の口": "eno_kuchi", "江ノ口": "eno_kuchi",
    "アンドロ": "andoro", "ハンマーズロック": "hammers_rock", "プレート": "plate",
    "三つ目の高根": "third_takane", "トビエイロック": "eagleray_rock",
    "西の高根": "west_takane", "ツインピークス": "twin_peaks",
    "ビューポイント": "view_point", "カベ根": "kabe_ne", "北の根": "kita_ne",
    "東の根": "higashi_ne", "カリトの鼻": "karito", "白根": "shirane",
    "はしご段": "hashigodan", "ハシゴ段": "hashigodan", "梯子段": "hashigodan",
    "オオタ根": "oota_ne",
}

TIDE_MEMO_RE = re.compile(r"(大潮|中潮|小潮|長潮|若潮)")
FLOW_RE = re.compile(r"(上げ潮|下げ潮|上り潮|下り潮|激流|爆流|緩(い|め|やか)|流れ(なし|無し|ゆるめ|強め))")


def split_sentences(text):
    return re.split(r"[。！!？?\n]+", text)


# 本文末尾の関連記事リスト/フッターを除去 (ハンマーズの RELATED DIVE LOG など)
FOOTER_MARKERS = [
    "Share this dive log", "RELATED DIVE LOG", "関連するダイブログ",
    "関連記事", "DIVE LOG TOP", "前の記事", "次の記事", "カテゴリー一覧",
    "最近の投稿", "アーカイブ",
]


def clean_body(body):
    if not body:
        return ""
    cut = len(body)
    for mk in FOOTER_MARKERS:
        i = body.find(mk)
        if 0 <= i < cut:
            cut = i
    return body[:cut]


SHOPNAME_RE = re.compile(
    r"(神子元ハンマーズ|ハンマーズ[-−ー]?神子元|HAMMERS?[’']?S?|ハンマーズロック)", re.I)


def strip_shop_names(text):
    """ショップ名/固有名詞に含まれる『ハンマー』を除去して誤検出を防ぐ"""
    return SHOPNAME_RE.sub("", text)


def detect_hammer(body, title=""):
    """否定文脈を考慮したハンマー目撃判定
    返り値: (seen: bool|None, evidence: str)"""
    text = strip_shop_names((title or "") + "。" + (body or ""))
    if not HAMMER_KW.search(text):
        return None, None  # 言及なし
    if POS_STRONG.search(text):
        m = POS_STRONG.search(text)
        return True, m.group(0)
    pos_votes, neg_votes = 0, 0
    pos_ev, neg_ev = None, None
    for sent in split_sentences(text):
        if not HAMMER_KW.search(sent):
            continue
        if NEG_RE.search(sent):
            neg_votes += 1
            if not neg_ev:
                neg_ev = sent.strip()[:80]
        else:
            # 肯定的動詞
            if re.search(r"(見れ|見られ|会え|出会え|遭遇|登場|出現|現れ|ゲット|いました|出ました|見えました|ヒット|観察|確認)", sent):
                pos_votes += 1
                if not pos_ev:
                    pos_ev = sent.strip()[:80]
    if pos_votes > 0 and pos_votes >= neg_votes:
        return True, pos_ev
    if neg_votes > 0:
        return False, neg_ev
    # 言及はあるが判定不能 -> None (曖昧)
    return None, None


def detect_size(body, title=""):
    text = strip_shop_names((title or "") + " " + (body or ""))
    for pat, size in SIZE_PATTERNS:
        if re.search(pat, text):
            return size
    return None


def structure():
    # 生記事 (dive_logs_raw_full.json) は著作権配慮のためリポジトリに含まない。
    # Actions 実行時は「新着分のみ」の生記事が一時生成されるので、
    # 構造化済みの既存 logs と URL マージして全量の daily_summary を再構築する。
    raw_path = ROOT / "dive_logs_raw_full.json"
    if raw_path.exists():
        data = json.load(open(raw_path))
    else:
        print("[structure] no raw articles file — rebuilding summary from existing structured logs only")
        data = {"articles": []}
    logs = []
    for a in data["articles"]:
        body = clean_body(a.get("body", "") or "")
        title = a.get("title", "") or ""
        entry = {
            "date": a.get("date"),
            "shop": a.get("shop"),
            "url": a.get("url"),
            "title": title[:120],
        }
        # 数値: raw_table (hammers) / raw (290, ms)
        rt = a.get("raw_table") or {}
        for k, v in rt.items():
            if "水温" in k:
                m = re.search(r"([\d.]+)\s*[~〜～\-−]\s*([\d.]+)", v)
                if m:
                    entry["water_temp_lo"] = float(m.group(1))
                    entry["water_temp_hi"] = float(m.group(2))
                else:
                    m = re.search(r"([\d.]+)", v)
                    if m:
                        entry["water_temp_lo"] = entry["water_temp_hi"] = float(m.group(1))
            elif "透明度" in k:
                m = re.search(r"([\d.]+)\s*[~〜～\-−]\s*([\d.]+)", v)
                if m:
                    entry["visibility_lo"] = float(m.group(1))
                    entry["visibility_hi"] = float(m.group(2))
                else:
                    m = re.search(r"([\d.]+)", v)
                    if m:
                        entry["visibility_lo"] = entry["visibility_hi"] = float(m.group(1))
            elif "波" in k:
                m = re.search(r"([\d.]+)", v)
                if m:
                    entry["wave_m"] = float(m.group(1))
            elif "ポイント" in k or "point" in k.lower():
                entry["points_raw"] = v
        raw = a.get("raw") or {}
        for src, dst in [("water_temp_lo", "water_temp_lo"), ("water_temp_hi", "water_temp_hi"),
                         ("visibility_lo", "visibility_lo"), ("visibility_hi", "visibility_hi"),
                         ("points", "points_raw"), ("tide_flow", "tide_flow")]:
            if src in raw and dst not in entry:
                entry[dst] = raw[src]
        # 日本語キー (ms)
        for k, v in raw.items():
            if isinstance(v, str):
                if "水温" in k and "water_temp_lo" not in entry:
                    m = re.search(r"([\d.]+)", v)
                    if m:
                        entry["water_temp_lo"] = entry["water_temp_hi"] = float(m.group(1))
                elif "透明度" in k and "visibility_lo" not in entry:
                    m = re.search(r"([\d.]+)", v)
                    if m:
                        entry["visibility_lo"] = entry["visibility_hi"] = float(m.group(1))
                elif "ポイント" in k and "points_raw" not in entry:
                    entry["points_raw"] = v

        seen, ev = detect_hammer(body, title)
        entry["hammer_seen"] = seen
        entry["hammer_evidence"] = ev
        entry["hammer_size"] = detect_size(body, title) if seen else None
        pts = set()
        search_text = body + " " + str(entry.get("points_raw", ""))
        for jp, key in POINT_MAP.items():
            if jp in search_text:
                pts.add(key)
        entry["points"] = sorted(pts)
        trop = set()
        for jp, key in TROPICAL_SPECIES:
            if jp in body:
                trop.add(key)
        entry["tropical_species"] = sorted(trop)
        m = TIDE_MEMO_RE.search(body)
        entry["tide_memo"] = m.group(0) if m else None
        m = FLOW_RE.search(body)
        entry["flow_memo"] = m.group(0) if m else None
        logs.append(entry)

    logs = [l for l in logs if l.get("date")]

    # 既存の構造化済み logs とマージ (新着が同一URLなら上書き)
    out_path = ROOT / "dive_logs_structured_full.json"
    if out_path.exists():
        try:
            prev = json.load(open(out_path))
            merged = {l["url"]: l for l in prev.get("logs", []) if l.get("url")}
            n_prev = len(merged)
            for l in logs:
                merged[l["url"]] = l
            logs = list(merged.values())
            print(f"[structure] merged: {n_prev} existing + new -> {len(logs)} total")
        except Exception as e:
            print(f"[structure] merge skipped ({e}) — using fresh logs only")
    logs.sort(key=lambda x: (x["date"], x["shop"]))

    daily = {}
    for e in logs:
        daily.setdefault(e["date"], []).append(e)

    size_rank = {"large": 3, "medium": 2, "small": 1, "single": 0}
    daily_summary = []
    for d, reports in sorted(daily.items()):
        wts_lo = [r["water_temp_lo"] for r in reports if r.get("water_temp_lo") is not None]
        wts_hi = [r["water_temp_hi"] for r in reports if r.get("water_temp_hi") is not None]
        vis_lo = [r["visibility_lo"] for r in reports if r.get("visibility_lo") is not None]
        vis_hi = [r["visibility_hi"] for r in reports if r.get("visibility_hi") is not None]
        votes = [r["hammer_seen"] for r in reports if r["hammer_seen"] is not None]
        seen_any = any(votes) if votes else None
        sizes = [r["hammer_size"] for r in reports if r.get("hammer_size")]
        best_size = max(sizes, key=lambda s: size_rank.get(s, -1)) if sizes else None
        pts = Counter()
        for r in reports:
            for p in r["points"]:
                pts[p] += 1
        trop = set()
        for r in reports:
            trop.update(r["tropical_species"])
        tide_memos = [r["tide_memo"] for r in reports if r.get("tide_memo")]
        flow_memos = [r["flow_memo"] for r in reports if r.get("flow_memo")]
        daily_summary.append({
            "date": d,
            "shops_reporting": sorted({r["shop"] for r in reports}),
            "n_reports": len(reports),
            "hammer_seen": seen_any,
            "hammer_votes": f"{sum(1 for v in votes if v)}/{len(votes)}",
            "hammer_size": best_size,
            "hammer_points": pts.most_common(5),
            "water_temp_lo": min(wts_lo) if wts_lo else None,
            "water_temp_hi": max(wts_hi) if wts_hi else None,
            "visibility_lo": min(vis_lo) if vis_lo else None,
            "visibility_hi": max(vis_hi) if vis_hi else None,
            "tropical_species": sorted(trop),
            "tide_memo": Counter(tide_memos).most_common(1)[0][0] if tide_memos else None,
            "flow_memo": Counter(flow_memos).most_common(1)[0][0] if flow_memos else None,
        })

    out = {
        "generated_at": datetime.now().isoformat(),
        "n_articles": len(logs),
        "n_days": len(daily),
        "logs": logs,
        "daily_summary": daily_summary,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    n_seen = sum(1 for s in daily_summary if s["hammer_seen"] is True)
    n_no = sum(1 for s in daily_summary if s["hammer_seen"] is False)
    n_amb = sum(1 for s in daily_summary if s["hammer_seen"] is None)
    print(f"articles={len(logs)} days={len(daily)} seen={n_seen} not_seen={n_no} ambiguous={n_amb}")
    print("date range:", daily_summary[0]["date"], "..", daily_summary[-1]["date"])


if __name__ == "__main__":
    structure()
