#!/usr/bin/env python3
"""ポイント別ハンマー実績出現率の集計

入力:
  phase1/dive_logs_structured_full.json  (4,026記事 / 2,727日, 2014-2026)
  phase1/moon_tide_calendar.json         (2015-2027, 月齢→潮名)
  phase1/kuroshio_path_history.json      (流路転換イベント列)

出力:
  analysis/point_stats.json  (機械可読: forecast_engine.py が読む)
  analysis/point_stats.md    (人間可読レポート)

定義:
  出現率 = P(hammer_seen==True | そのポイントに潜った記事, 条件)
  - hammer_seen が None (記載なし/曖昧) の記事は「目撃なし」として分母に含める
    (ショップログは目撃時にほぼ必ず言及するため、無言及≒不発の近似。
     厳密率 True/(True+False) は False が90記事しかなく分母が壊れるため不採用)
  - 1記事に複数ポイントが載る場合は全ポイントに帰属 (帰属曖昧性あり→mdに明記)
  - n<10 のセルは reliable=false としてマーク
"""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "analysis" / "point_stats.json"
OUT_MD = ROOT / "analysis" / "point_stats.md"

# ---- エイリアス統合対応表 (structure_full.py の POINT_MAP を拡張) ----
# 生ログ points_raw の表記ゆれ調査結果:
#   カメ根: カメ根/亀根/かメ根/かめ根/KAMENE/Kamene/kamene/Kame-ne
#   江の口: 江の口/江ノ口/Enokuchi/ENOKUCHI
#   Aポイント: Aポイント/A ポイント/Apoint/A
#   ザブ根: ザブ根/ZABUNE/Zabu-ne/Zabune
#   三ツ根: 三ツ根/三つ根/ミツネ/ミツ根
#   はしご段: はしご段/ハシゴ段/梯子段
#   カリト: カリトの鼻/karito
# ※ ao_ne(青根) と jab_ne(ジャブ根) は現地で同一根の別名 (青根=ジャブ根) → jab_ne に統合
ALIAS_TABLE = {
    "kame_ne":      ["カメ根", "亀根", "かメ根", "かめ根", "KAMENE", "Kamene", "kamene", "Kame-ne"],
    "jab_ne":       ["ジャブ根", "青根(=同一根の別名)"],
    "eno_kuchi":    ["江の口", "江ノ口", "Enokuchi", "ENOKUCHI"],
    "A_point":      ["Aポイント", "A ポイント", "Apoint"],
    "zabu_ne":      ["ザブ根", "ZABUNE", "Zabu-ne", "Zabune"],
    "mitsu_ne":     ["三ツ根", "三つ根", "ミツネ", "ミツ根"],
    "kado_ne":      ["カド根"],
    "hashigodan":   ["はしご段", "ハシゴ段", "梯子段"],
    "shirane":      ["白根"],
    "karito":       ["カリトの鼻", "karito"],
    "higashi_ne":   ["東の根"],
    "hammers_rock": ["ハンマーズロック"],
    "andoro":       ["アンドロ"],
    "plate":        ["プレート"],
    "third_takane": ["三つ目の高根"],
}
MERGE = {"ao_ne": "jab_ne"}  # 青根→ジャブ根 (同一根)

FLOW_UP_RE = re.compile(r"上げ潮|上り潮")
FLOW_DOWN_RE = re.compile(r"下げ潮|下り潮")

TIDE_NAMES = ["大潮", "中潮", "小潮", "長潮", "若潮"]


def classify_flow(text):
    """ログ本文由来の tide_flow/flow_memo → up/down/mixed/None"""
    if not text:
        return None
    up = bool(FLOW_UP_RE.search(text))
    down = bool(FLOW_DOWN_RE.search(text))
    if up and down:
        return "mixed"
    if up:
        return "up"
    if down:
        return "down"
    return None


def load_kuroshio_daily():
    """遷移イベント列 → date→type の階段関数"""
    kp = json.load(open(ROOT / "data" / "kuroshio_path_history.json"))
    events = sorted(kp["transition_events"], key=lambda e: e["date"])

    def type_on(date_str):
        t = None
        for e in events:
            if e["date"] <= date_str:
                t = e["type_after"]
            else:
                break
        return t
    return type_on


def cell(seen, n):
    rate = seen / n if n else None
    return {
        "n": n, "seen": seen,
        "rate": round(rate, 4) if rate is not None else None,
        "reliable": n >= 10,
    }


def main():
    d = json.load(open(ROOT / "data" / "dive_logs_structured_full.json"))
    logs = d["logs"]
    mt = json.load(open(ROOT / "data" / "moon_tide_calendar.json"))["calendar"]
    kuro_on = load_kuroshio_daily()

    # 集計コンテナ: point → dim → key → [seen, n]
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0])))
    overall = defaultdict(lambda: [0, 0])
    total_logs = 0
    total_seen = 0

    for l in logs:
        pts = l.get("points") or []
        pts = sorted({MERGE.get(p, p) for p in pts})
        if not pts:
            continue
        date = l["date"]
        seen = 1 if l.get("hammer_seen") is True else 0
        total_logs += 1
        total_seen += seen

        month = int(date[5:7])
        tide_name = mt.get(date, {}).get("tide_name")
        flow = classify_flow(l.get("tide_flow") or l.get("flow_memo"))
        kuro = kuro_on(date)

        for p in pts:
            overall[p][0] += seen
            overall[p][1] += 1
            if tide_name:
                agg[p]["by_tide_name"][tide_name][0] += seen
                agg[p]["by_tide_name"][tide_name][1] += 1
            agg[p]["by_month"][str(month)][0] += seen
            agg[p]["by_month"][str(month)][1] += 1
            if flow in ("up", "down"):
                agg[p]["by_flow"][flow][0] += seen
                agg[p]["by_flow"][flow][1] += 1
            if kuro:
                agg[p]["by_kuroshio"][kuro][0] += seen
                agg[p]["by_kuroshio"][kuro][1] += 1

    baseline = total_seen / total_logs if total_logs else 0.5

    points_out = {}
    for p, (s, n) in sorted(overall.items(), key=lambda kv: -kv[1][1]):
        entry = {"overall": cell(s, n)}
        for dim in ("by_tide_name", "by_month", "by_flow", "by_kuroshio"):
            entry[dim] = {k: cell(v[0], v[1])
                          for k, v in sorted(agg[p][dim].items())}
        points_out[p] = entry

    out = {
        "generated_at": datetime.now().isoformat(),
        "source": "phase1/dive_logs_structured_full.json (2014-2026, 4026 articles)",
        "definition": ("rate = P(hammer_seen==True | point dived, condition). "
                       "hammer_seen=None counted as not-seen. "
                       "Multi-point articles credit all listed points."),
        "n_articles_used": total_logs,
        "baseline_rate": round(baseline, 4),
        "alias_table": ALIAS_TABLE,
        "merged": MERGE,
        "observation_bias_note": (
            "hammer_points はガイドの行先選択に強く依存する観測バイアスあり。"
            "カメ根が全記事の約7割に登場するのは『ハンマーが出るから行く』と"
            "『行くから記録される』の両方の効果を含む。出現率の点間比較は参考値。"
        ),
        "points": points_out,
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")

    # ---------- markdown ----------
    JA = {"kame_ne": "カメ根", "jab_ne": "ジャブ根(青根)", "eno_kuchi": "江の口",
          "A_point": "Aポイント", "mitsu_ne": "三ツ根", "zabu_ne": "ザブ根",
          "shirane": "白根", "kado_ne": "カド根", "hashigodan": "はしご段",
          "higashi_ne": "東の根", "hammers_rock": "ハンマーズロック",
          "andoro": "アンドロ", "plate": "プレート", "karito": "カリトの鼻",
          "third_takane": "三つ目の高根"}

    def fmt(c):
        if c["n"] == 0:
            return "—"
        mark = "" if c["reliable"] else " ⚠"
        return f"{c['rate']*100:.0f}% (n={c['n']}){mark}"

    lines = []
    lines.append("# 神子元 ポイント別ハンマー実績出現率\n")
    lines.append(f"生成: {out['generated_at'][:19]} / 対象: 2014-2026 全{total_logs:,}記事 (ポイント記載あり)\n")
    lines.append(f"**全体ベースライン出現率: {baseline*100:.1f}%** "
                 "(定義: ポイントに潜った記事のうち hammer_seen=True の割合。"
                 "hammer_seen=None は不発扱い)\n")
    lines.append("## ⚠ 観測バイアスについて (重要)\n")
    lines.append("- **行先選択バイアス**: ガイドは「出そうな所」に客を連れて行く。カメ根が全記事の約70%に"
                 "登場するのは実力と選好の両方。**「カメ根の出現率が高い」≠「他ポイントで出ない」**。"
                 "低頻度ポイント (ハンマーズロック n=2 等) は「行っていないだけ」でデータが無い。\n"
                 "- **帰属曖昧性**: 1記事に複数ポイントが載る場合、どこでハンマーを見たか特定できないため"
                 "全ポイントに目撃をクレジットしている。併記されやすいポイントの率は互いに引っ張られる。\n"
                 "- **無言及=不発の近似**: hammer_seen=None (曖昧) を不発扱いにしている。"
                 "明示的な「見えなかった」記事は90件しかなく、None を除外すると出現率が96%に張り付いて無意味になるため。\n")
    lines.append("## エイリアス統合対応表\n")
    lines.append("| 正規キー | 統合した表記 |")
    lines.append("|---|---|")
    for k, aliases in ALIAS_TABLE.items():
        lines.append(f"| `{k}` ({JA.get(k, k)}) | {'、'.join(aliases)} |")
    lines.append("\n※ `ao_ne`(青根) は現地でジャブ根と同一根の別名のため `jab_ne` に統合。\n")

    lines.append("## ポイント別出現率 (⚠ = n<10 信頼性低)\n")
    lines.append("### 全期間\n")
    lines.append("| ポイント | 出現率 | 潜水記事数 |")
    lines.append("|---|---|---|")
    for p, e in points_out.items():
        o = e["overall"]
        lines.append(f"| {JA.get(p, p)} | {fmt(o)} | {o['n']} |")

    lines.append("\n### 潮名別 (大潮/中潮/小潮/長潮/若潮, 2015年以降)\n")
    header = "| ポイント | " + " | ".join(TIDE_NAMES) + " |"
    lines.append(header)
    lines.append("|---" * (len(TIDE_NAMES) + 1) + "|")
    for p, e in points_out.items():
        if e["overall"]["n"] < 30:
            continue
        row = [JA.get(p, p)]
        for t in TIDE_NAMES:
            row.append(fmt(e["by_tide_name"].get(t, {"n": 0})))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n### 月別\n")
    months = [str(m) for m in range(1, 13)]
    lines.append("| ポイント | " + " | ".join(f"{m}月" for m in months) + " |")
    lines.append("|---" * 13 + "|")
    for p, e in points_out.items():
        if e["overall"]["n"] < 100:
            continue
        row = [JA.get(p, p)]
        for m in months:
            row.append(fmt(e["by_month"].get(m, {"n": 0})))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n### 潮汐フェーズ別 (本文の上げ潮/下げ潮・上り潮/下り潮の記載から抽出)\n")
    lines.append("| ポイント | 上げ潮 | 下げ潮 |")
    lines.append("|---|---|---|")
    for p, e in points_out.items():
        if e["overall"]["n"] < 30:
            continue
        row = [JA.get(p, p), fmt(e["by_flow"].get("up", {"n": 0})),
               fmt(e["by_flow"].get("down", {"n": 0}))]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("\n※ 流向記載は下り潮に大きく偏る (下り潮1,748日 vs 上げ潮442日): "
                 "神子元のガイドは下り潮の時間帯を選んで潜る傾向。\n")

    lines.append("\n### 黒潮流路タイプ別\n")
    lines.append("| ポイント | nNLM (接岸) | oNLM (離岸) | LM (大蛇行) |")
    lines.append("|---|---|---|---|")
    for p, e in points_out.items():
        if e["overall"]["n"] < 30:
            continue
        row = [JA.get(p, p)]
        for t in ("nNLM", "oNLM", "LM"):
            row.append(fmt(e["by_kuroshio"].get(t, {"n": 0})))
        lines.append("| " + " | ".join(row) + " |")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
