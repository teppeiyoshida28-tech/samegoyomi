"""backtest_data.json を history_index.html に埋め込んで検証ページを作る"""
import json
from pathlib import Path
from domain import read_json

ROOT = Path(__file__).parent
tpl = (ROOT / "history_index.html").read_text(encoding="utf-8")
payload = read_json(ROOT / "backtest_data.json")
payload["issued"] = read_json(ROOT / "issued_history.json") if (ROOT / "issued_history.json").exists() else {"days": [], "by_lead": {}}
data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
out = tpl.replace("/*__BACKTEST_JSON__*/ null", data)
out = out.replace("/hammerhead/img/", "../img/")  # history/ 配下に置くので相対パス
(ROOT / "mikomoto_history.html").write_text(out, encoding="utf-8")
print(f"Built {ROOT / 'mikomoto_history.html'} ({len(out):,} bytes)")
