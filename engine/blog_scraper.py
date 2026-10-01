"""
神子元 3ショップ ブログスクレイパ

サポートソース:
  1. 神子元ハンマーズ (mikomoto.com) — HTML table 完全構造化
  2. 海遊社 (290.jp) — 見出し行の regex 抽出
  3. 神子元マリンサービス系 (mikomotodivers.com) — 自由記述, regex 抽出

出力: dive_logs/<date>_<shop>.json + all_logs.json (時系列)
"""
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
LOGS_DIR = ROOT / "dive_logs"
LOGS_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MikomotoHammerForecast/0.5; research/personal)",
    "Accept-Language": "ja,en;q=0.7",
}

# ============================================================
# ① 神子元ハンマーズ (mikomoto.com) — HTML テーブル抽出
# ============================================================
def fetch_hammers_list(max_pages=3):
    """divelog カテゴリのページから記事URL一覧を取得"""
    urls = []
    for page in range(1, max_pages + 1):
        url = f"https://www.mikomoto.com/category/divelog/" if page == 1 else f"https://www.mikomoto.com/category/divelog/page/{page}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"  hammers list failed p{page}: {e}")
            break
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/divelog/']"):
            href = a.get("href", "")
            m = re.match(r"https?://www\.mikomoto\.com/divelog/(\d+)/?$", href)
            if m and href not in urls:
                urls.append(href)
        time.sleep(0.5)
    return urls


def parse_hammers_article(url):
    """1記事から table + 本文 を抽出"""
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # 日付: title や URL からは取れないので、pagetitle/entry-date から
    date_str = None
    date_el = soup.select_one(".entry-date, time.published, .post-date")
    if date_el:
        date_str = date_el.get_text(strip=True)
    else:
        # 本文冒頭の "2026.08.22" パターン
        body_text = soup.get_text()
        m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", body_text)
        if m:
            date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # data table を探す
    data = {"url": url, "shop": "hammers", "date": date_str, "raw_table": {}, "body": ""}
    for table in soup.select("table"):
        for row in table.select("tr"):
            ths = row.select("th")
            tds = row.select("td")
            for th, td in zip(ths, tds):
                key = th.get_text(" ", strip=True)
                val = td.get_text(" ", strip=True)
                data["raw_table"][key] = val

    # 本文: article配下から table/nav/aside/img を除外して抽出
    content = soup.select_one("article, .single-post, .post, main")
    if content is None:
        content = soup.body
    if content:
        for t in content.select("table, img, script, style, nav, aside, header, footer, .breadcrumb, .share, .tags"):
            t.decompose()
        text = content.get_text(" ", strip=True)
        # 冒頭のメニュー/日付ヘッダーを削除 (数字連続 と "Dive log" 等)
        data["body"] = text[:4000]

    return data


# ============================================================
# ② 海遊社 (290.jp) — 見出し行 regex
# ============================================================
def fetch_290_list(max_pages=3):
    urls = []
    for page in range(1, max_pages + 1):
        url = f"https://www.290.jp/archives/category/dive-log" if page == 1 else f"https://www.290.jp/archives/category/dive-log/page/{page}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"  290 list failed p{page}: {e}")
            break
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("h2 a, .entry-title a"):
            href = a.get("href", "")
            if re.match(r"https?://www\.290\.jp/archives/\d+", href) and href not in urls:
                urls.append(href)
        time.sleep(0.5)
    return urls


def parse_290_article(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # entry-content から本文取得
    body_el = soup.select_one(".entry-content, article, .post-content")
    body = body_el.get_text("\n", strip=True) if body_el else soup.get_text("\n", strip=True)

    # 見出し行のパース: 例
    #  2026/8/2 (日)
    #  天気晴れ　気温26−33℃　水温20-25℃　透明度5-15m
    #  潮流上げ潮 〜 下げ潮 〜 上げ潮　ポイントカメ根×4
    data = {"url": url, "shop": "290", "date": None, "raw": {}, "body": body[:3000]}

    m = re.search(r"(20\d{2})/(\d{1,2})/(\d{1,2})", body[:200])
    if m:
        data["date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 気温
    m = re.search(r"気温[\s　]*([\-−\d\.]+)[\-−~〜](\d+\.?\d*)[℃度C]", body)
    if m:
        data["raw"]["air_temp_lo"] = float(m.group(1).replace('−','-'))
        data["raw"]["air_temp_hi"] = float(m.group(2))
    # 水温
    m = re.search(r"水温[\s　]*([\-−\d\.]+)[\-−~〜](\d+\.?\d*)[℃度C]", body)
    if m:
        data["raw"]["water_temp_lo"] = float(m.group(1).replace('−','-'))
        data["raw"]["water_temp_hi"] = float(m.group(2))
    else:
        m = re.search(r"水温[\s　]*(\d+\.?\d*)[℃度C]", body)
        if m:
            data["raw"]["water_temp_lo"] = data["raw"]["water_temp_hi"] = float(m.group(1))
    # 透明度
    m = re.search(r"透明度[\s　]*([\-−\d\.]+)[\-−~〜](\d+\.?\d*)\s*m", body)
    if m:
        data["raw"]["visibility_lo"] = float(m.group(1).replace('−','-'))
        data["raw"]["visibility_hi"] = float(m.group(2))
    # 潮流
    m = re.search(r"潮流[\s　]*([^ポ　\n]+)", body)
    if m:
        data["raw"]["tide_flow"] = m.group(1).strip()
    # ポイント
    m = re.search(r"ポイント[\s　]*([^\n]{1,60})", body)
    if m:
        data["raw"]["points"] = m.group(1).strip()

    return data


# ============================================================
# ③ 神子元マリンサービス (mikomotodivers.com)
# ============================================================
def fetch_mikomotoms_list(max_pages=3):
    urls = []
    for page in range(1, max_pages + 1):
        url = f"https://mikomotodivers.com/logs/" if page == 1 else f"https://mikomotodivers.com/logs/page/{page}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"  ms list failed p{page}: {e}")
            break
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/logs/']"):
            href = a.get("href", "")
            m = re.match(r"https?://mikomotodivers\.com/logs/\d{4}/\d{2}/\d+\.html", href)
            if m and href not in urls:
                urls.append(href)
        time.sleep(0.5)
    return urls


def parse_mikomotoms_article(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # URL から日付抽出
    m = re.search(r"/logs/(\d{4})/(\d{2})/(\d{2})", url)
    date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

    body_el = soup.select_one(".entry-content, .post-content, article")
    body = body_el.get_text("\n", strip=True) if body_el else soup.get_text("\n", strip=True)

    data = {"url": url, "shop": "ms", "date": date_str, "raw": {}, "body": body[:3000]}
    # 表形式データテーブルがあれば抽出
    for table in soup.select("table"):
        for row in table.select("tr"):
            ths = row.select("th, td:first-child")
            tds = row.select("td")
            if len(tds) >= 2:
                key = tds[0].get_text(strip=True)
                val = tds[1].get_text(strip=True)
                if key and val:
                    data["raw"][key] = val

    # 本文中の数値
    m = re.search(r"水温[\s　]*(\d+)[\s～〜~\-−](\d+)[℃度]", body)
    if m:
        data["raw"]["water_temp_lo"] = float(m.group(1))
        data["raw"]["water_temp_hi"] = float(m.group(2))
    m = re.search(r"透[明視]度[\s　]*(\d+)[\s～〜~\-−](\d+)\s*m", body)
    if m:
        data["raw"]["visibility_lo"] = float(m.group(1))
        data["raw"]["visibility_hi"] = float(m.group(2))
    return data


# ============================================================
# 全体走査
# ============================================================
def scrape_all(max_pages=2):
    all_articles = []

    print("[1/3] 神子元ハンマーズ (mikomoto.com)...")
    urls = fetch_hammers_list(max_pages)
    print(f"  {len(urls)} articles")
    for u in urls[:15]:
        try:
            a = parse_hammers_article(u)
            all_articles.append(a)
            time.sleep(0.6)
        except Exception as e:
            print(f"    skip {u}: {e}")

    print("[2/3] 海遊社 (290.jp)...")
    urls = fetch_290_list(max_pages)
    print(f"  {len(urls)} articles")
    for u in urls[:15]:
        try:
            a = parse_290_article(u)
            all_articles.append(a)
            time.sleep(0.6)
        except Exception as e:
            print(f"    skip {u}: {e}")

    print("[3/3] 神子元マリンサービス系 (mikomotodivers.com)...")
    urls = fetch_mikomotoms_list(max_pages)
    print(f"  {len(urls)} articles")
    for u in urls[:15]:
        try:
            a = parse_mikomotoms_article(u)
            all_articles.append(a)
            time.sleep(0.6)
        except Exception as e:
            print(f"    skip {u}: {e}")

    # 保存
    out = ROOT / "dive_logs_raw.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"scraped_at": datetime.now().isoformat(), "articles": all_articles},
                  f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(all_articles)} articles → {out}")
    return all_articles


if __name__ == "__main__":
    scrape_all(max_pages=2)
