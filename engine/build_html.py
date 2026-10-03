"""forecast_data.json / longrange_data.json を index.html に埋め込み、
トップ・マップ・狙い目カレンダーの3ページを生成する。

- トップ (mikomoto_forecast.html): PAGE_MODE=''。hourly今日+明日、longrangeあり(ダイジェスト表示)
- マップ (mikomoto_map.html): PAGE_MODE='map'。hourly5日分、longrangeなし
- 狙い目 (mikomoto_aim.html): PAGE_MODE='aim'。hourly today のみ(ヒーロー用最小)、longrangeフル
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
tpl = (ROOT / "index.html").read_text(encoding="utf-8")
for marker, filename in [("<!--__MAP_PANEL__-->", "map_panel.html"),
                         ("/*__MAP_GEOMETRY__*/", "map_geometry.js"),
                         ("/*__MAP_UI__*/", "map_ui.js"),
                         ("/*__UI_REFRESH__*/", "ui_refresh.css")]:
    tpl = tpl.replace(marker, (ROOT / filename).read_text(encoding="utf-8"))
from point_catalog import CATALOG
tpl = tpl.replace("/*__MAP_CATALOG__*/ null", json.dumps(CATALOG, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c"))
data = json.loads((ROOT / "forecast_data.json").read_text(encoding="utf-8"))
lr_path = ROOT / "longrange_data.json"
lr_text = lr_path.read_text(encoding="utf-8") if lr_path.exists() else "null"


def slim(d, n_days):
    """hourly を先頭 n_days 日ぶんに絞ったコピー"""
    dates = [x["date"] for x in d["daily"][:n_days]]
    out = dict(d)
    out["hourly"] = [h for h in d["hourly"] if h["time"][:10] in dates]
    return out


SITE_URL = "https://teppeiyoshida28-tech.github.io/samegoyomi/"


def build(page_mode, fc, lr, title, outname):
    h = tpl.replace("/*__FORECAST_JSON__*/ null", json.dumps(fc, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c"))
    h = h.replace("/*__LONGRANGE_JSON__*/ null", lr.replace("<", "\\u003c"))
    h = h.replace("/*__PAGE_MODE__*/ ''", f"'{page_mode}'")
    # 画像パス: ルートページは img/、サブディレクトリ配下は ../img/
    h = h.replace('content="/hammerhead/img/', f'content="{SITE_URL}img/')  # og:image は絶対URL
    h = h.replace("/hammerhead/img/", "img/" if page_mode == "" else "../img/")
    if title:
        h = h.replace("<title>鮫暦AI — 神子元ハンマー潮予報</title>",
                      f"<title>{title}</title>")
    (ROOT / outname).write_text(h, encoding="utf-8")
    print(f"Built {outname} ({len(h):,} bytes)")


build("", slim(data, 2), lr_text, None, "mikomoto_forecast.html")
build("map", slim(data, 5), "null",
      "予測マップ (4日先まで) — 鮫暦AI 神子元ハンマー潮予報", "mikomoto_map.html")
build("aim", slim(data, 1), lr_text,
      "狙い目カレンダー (半年先) — 鮫暦AI 神子元ハンマー潮予報", "mikomoto_aim.html")
