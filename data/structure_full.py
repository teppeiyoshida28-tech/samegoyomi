"""
Phase1-1b: dive_logs_raw_full.json -> dive_logs_structured_full.json
repo/blog_structure.py のロジックを改良:
 - 否定文脈の検出を強化 (不発/現れず/出ず/会えず/姿はなく 等 + 「ハンマー」近傍の否定)
 - 日別サマリ (hammer_seen, hammer_size, hammer_points, water_temp, visibility,
   tropical_species, 潮回りメモ)
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent / "engine"))
from domain import now_jst, write_json, read_json, point_names, BIAS_NOTE

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
    r"駄目|ダメでした", r"叶(わず|いません)", r"お預け",
    r"ハンマー不在", r"はずれ", r"ハズレ", r"惨敗", r"撃沈",
    r"狙(い|う)ましたが", r"探し(ました|た)が",
    r"(?<![0-9０-９])(?:0|０|ゼロ)\s*(?:匹|個体)", r"(?:遭遇|確認|目撃)[はが]ありません",
    r"見つけることができ(?:ず|ません)", r"お休みでした",
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
FUTURE_RE = re.compile(r"明日|明後日|あす|次回|次の出航|来週|来月|今後|(?:に|を)期待(?:し|する|！|!|$)|願[いっう]|ますように")
PAST_RE = re.compile(r"昨日|一昨日|先日|先週|先月|昨年|ここ最近|ここ数日")
OTHER_FISH_RE = re.compile(r"イサキ|タカベ|カンパチ|イナダ|ヒラマサ|タカサゴ|ムロアジ|メジナ|ニザダイ|シラコダイ|オオニベ")
OBSERVATION_RE = re.compile(
    r"見(?:れ|られ|え)(?:ました|た)|見(?:れて|られて|えて)(?:います|ます)|会え(?:ました|た)|出会え(?:ました|た)|"
    r"遭遇(?:しました|した|でき|[はもが]?続いて)|登場|出現|ゲット|ヒット|"
    r"いました|出ました|現れ(?:ました|た)|観察|確認|発見|健在")


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


def observation_clauses(body, title=""):
    """Discard plans/history before assigning an article's observation labels.

    Comma clauses keep 'saw sharks, but no photographs' from becoming a negative
    sighting. Unrelated fish or tomorrow's hopes must not supply a shark size.
    """
    text = strip_shop_names((title or "") + "。" + (body or ""))
    text = re.sub(r"#[^\s。！!？?\n]+", "", text)
    for sentence in split_sentences(text):
        if FUTURE_RE.search(sentence):
            continue
        if PAST_RE.search(sentence) and not re.search(r"今日|本日", sentence):
            continue
        for clause in re.split(r"、", sentence):
            if clause.strip():
                yield clause.strip()


def observed_hammer_clause(clause):
    if not HAMMER_KW.search(clause):
        return None
    if NEG_RE.search(clause):
        return False
    if POS_STRONG.search(clause) and not OTHER_FISH_RE.search(clause):
        return True
    if OBSERVATION_RE.search(clause):
        return True
    # Short reporting phrases can assert an encounter without a complete verb,
    # but searching for a group is not observing it. Bare counts of other fish
    # elsewhere in the article are never sufficient.
    if not re.search(r"狙|探し|捜索", clause) and re.search(
            r"ハンマー(?:リバー|の?大群|川|シャワー|玉|渦|づくし|三昧)|"
            r"ハンマー(?:[がはをと]*)(?:単体|数匹|数十匹|数百匹|\d+\s*匹)", clause):
        return True
    return None


def detect_hammer(body, title=""):
    """否定文脈を考慮したハンマー目撃判定
    返り値: (seen: bool|None, evidence: str)"""
    negative = None
    for clause in observation_clauses(body, title):
        result = observed_hammer_clause(clause)
        if result is True:
            return True, clause[:80]
        if result is False and negative is None:
            negative = clause[:80]
    if negative is not None:
        return False, negative
    # 言及はあるが判定不能 -> None (曖昧)
    return None, None


def detect_size(body, title=""):
    candidates, context_remaining = [], 0
    for clause in observation_clauses(body, title):
        mentions = HAMMER_KW.search(clause) is not None
        if OTHER_FISH_RE.search(clause) and not mentions:
            context_remaining = 0
            continue
        if mentions:
            context_remaining = 6
        else:
            context_remaining -= 1
        if not mentions and not (context_remaining >= 0 and OBSERVATION_RE.search(clause)):
            continue
        if NEG_RE.search(clause):
            continue
        if OTHER_FISH_RE.search(clause):
            # Ambiguous mixed-species clause: do not guess which group is sharks.
            continue
        for pattern, size in SIZE_PATTERNS:
            if re.search(pattern, clause):
                candidates.append(size)
                break
    rank = {"single": 0, "small": 1, "medium": 2, "large": 3}
    return max(candidates, key=rank.get) if candidates else None


def points_in(text):
    return point_names([key for name, key in POINT_MAP.items() if name in (text or "")])


def sighting_points(body):
    """保守的な帰属: 地点でのハンマー目撃を明記した一文だけ採用。"""
    result = set()
    for sent in split_sentences(body):
        pts = points_in(sent)
        if len(pts) != 1 or detect_hammer(sent)[0] is not True:
            continue
        if any(re.search(re.escape(name) + r"(?:で|にて|周辺で)", sent) for name in POINT_MAP):
            result.update(pts)
    return sorted(result)


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
        entry["mentioned_points"] = point_names(sorted(pts))
        entry["visited_points"] = points_in(str(entry.get("points_raw", "")))
        entry["sighting_points"] = sighting_points(body) if seen is True else []
        entry["label_version"] = 3
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
            prev = read_json(out_path)
            merged = {l["url"]: l for l in prev.get("logs", []) if l.get("url")}
            n_prev = len(merged)
            for l in logs:
                merged[l["url"]] = l
            logs = list(merged.values())
            print(f"[structure] merged: {n_prev} existing + new -> {len(logs)} total")
        except Exception as e:
            raise RuntimeError("既存ログのマージに失敗。履歴を保持して中止します") from e
    logs.sort(key=lambda x: (x["date"], x["shop"]))
    for entry in logs:
        entry.setdefault("mentioned_points", point_names(entry.get("points")))
        entry.setdefault("visited_points", points_in(str(entry.get("points_raw", ""))))
        entry.setdefault("sighting_points", [])  # 旧データから目撃地点を推測しない
        entry.setdefault("label_version", 1)

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
        visits = Counter()
        mentions = Counter()
        for r in reports:
            mentions.update(point_names(r.get("mentioned_points")))
            visits.update(point_names(r.get("visited_points")))
            if r.get("hammer_seen") is True:
                pts.update(point_names(r.get("sighting_points")))
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
            "sighting_points": sorted(pts),
            "visited_points": sorted(visits),
            "mentioned_points": sorted(mentions),
            "label_version": 2,
            "water_temp_lo": min(wts_lo) if wts_lo else None,
            "water_temp_hi": max(wts_hi) if wts_hi else None,
            "visibility_lo": min(vis_lo) if vis_lo else None,
            "visibility_hi": max(vis_hi) if vis_hi else None,
            "tropical_species": sorted(trop),
            "tide_memo": Counter(tide_memos).most_common(1)[0][0] if tide_memos else None,
            "flow_memo": Counter(flow_memos).most_common(1)[0][0] if flow_memos else None,
        })

    out = {
        "generated_at": now_jst().isoformat(),
        "observation_bias_note": BIAS_NOTE,
        "n_articles": len(logs),
        "n_days": len(daily),
        "logs": logs,
        "daily_summary": daily_summary,
    }
    if not logs:
        raise RuntimeError("構造化ログが空。既存データを上書きせず中止")
    write_json(out_path, out, indent=None)
    n_seen = sum(1 for s in daily_summary if s["hammer_seen"] is True)
    n_no = sum(1 for s in daily_summary if s["hammer_seen"] is False)
    n_amb = sum(1 for s in daily_summary if s["hammer_seen"] is None)
    print(f"articles={len(logs)} days={len(daily)} seen={n_seen} not_seen={n_no} ambiguous={n_amb}")
    print("date range:", daily_summary[0]["date"], "..", daily_summary[-1]["date"])


if __name__ == "__main__":
    structure()
