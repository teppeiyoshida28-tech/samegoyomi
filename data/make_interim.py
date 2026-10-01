"""articles.jsonl (逐次追記) から dive_logs_raw_full.json を暫定生成する。
スクレイパー走行中でも安全 (読み取りのみ)。URL重複は後勝ちで排除。"""
import json
from pathlib import Path

ROOT = Path(__file__).parent

if not (ROOT / "articles.jsonl").exists():
    # 生記事はリポジトリに含まない (著作権配慮)。スクレイプ未実行/失敗時は何もしない。
    print("[interim] articles.jsonl not found — skip (no new articles)")
    raise SystemExit(0)

arts = {}
with open(ROOT / "articles.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            a = json.loads(line)
        except json.JSONDecodeError:
            continue  # 書き込み途中の行はスキップ
        if a.get("url"):
            arts[a["url"]] = a

articles = list(arts.values())
by_shop = {}
for a in articles:
    by_shop[a.get("shop")] = by_shop.get(a.get("shop"), 0) + 1
json.dump({"articles": articles}, open(ROOT / "dive_logs_raw_full.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"[interim] {len(articles)} unique articles {by_shop}")
