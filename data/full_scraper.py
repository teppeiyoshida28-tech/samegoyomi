"""
Phase1: 神子元3ショップ ブログ全量スクレイパ (レジューム対応)

- 神子元ハンマーズ mikomoto.com/divelog/ (paged, ~48p, 2023-01〜)
- 海遊社 290.jp/archives/category/dive-log (paged, ~281p, 2014-01〜)
- 神子元ダイバーズ mikomotodivers.com/logs/YYYY/MM/ (monthly, 2022-05〜)

出力:
  phase1/scrape_state.json   … 進捗状態 (URLリスト, 済みURL)
  phase1/articles.jsonl      … 記事1件=1行 (逐次追記)
  phase1/dive_logs_raw_full.json … 最終まとめ
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
STATE_F = ROOT / "scrape_state.json"
JSONL_F = ROOT / "articles.jsonl"
OUT_F = ROOT / "dive_logs_raw_full.json"

SLEEP = 2.1  # seconds between requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MikomotoHammerForecast/1.0; research/personal)",
    "Accept-Language": "ja,en;q=0.7",
}

session = requests.Session()
session.headers.update(HEADERS)

_last_req = [0.0]


def get(url, timeout=30):
    wait = SLEEP - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True)
            _last_req[0] = time.time()
            if r.status_code == 429:
                print(f"    429 on {url}, sleeping 60s", flush=True)
                time.sleep(60)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            _last_req[0] = time.time()
            if attempt == 2:
                raise
            print(f"    retry {attempt+1} {url}: {e}", flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


def load_state():
    if STATE_F.exists():
        s = json.load(open(STATE_F))
        s.setdefault("url_lists", {})
        s.setdefault("done_urls", [])
        s.setdefault("failed_urls", [])
        return s
    return {"url_lists": {}, "done_urls": [], "failed_urls": []}


def save_state(state):
    tmp = STATE_F.with_suffix(".tmp")
    json.dump(state, open(tmp, "w"), ensure_ascii=False)
    tmp.replace(STATE_F)


def append_article(rec):
    with open(JSONL_F, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ============================================================
# URL list collection
# ============================================================
def collect_hammers_urls(known=None, stop_after_known=8):
    """known: 既取得URLのset。新着ページは先頭に来るので、既知URLが
    stop_after_known 件連続したらリスト収集を打ち切る (インクリメンタル)。"""
    known = known or set()
    urls = []
    page = 1
    known_streak = 0
    while True:
        url = ("https://www.mikomoto.com/divelog/" if page == 1
               else f"https://www.mikomoto.com/divelog/page/{page}/")
        try:
            r = get(url)
        except Exception as e:
            print(f"  hammers list end p{page}: {e}", flush=True)
            break
        soup = BeautifulSoup(r.text, "html.parser")
        found = 0
        for a in soup.select("a[href*='/divelog/']"):
            href = a.get("href", "")
            m = re.match(r"https?://www\.mikomoto\.com/divelog/(\d+)/?$", href)
            if m:
                href = href.rstrip("/") + "/"
                if href not in urls:
                    urls.append(href)
                    found += 1
                    known_streak = known_streak + 1 if href in known else 0
        # max page detection
        maxp = 1
        for a in soup.select("a[href*='/divelog/page/']"):
            m = re.search(r"/page/(\d+)", a.get("href", ""))
            if m:
                maxp = max(maxp, int(m.group(1)))
        print(f"  hammers p{page}: +{found} urls (total {len(urls)}, maxp={maxp})", flush=True)
        if known and known_streak >= stop_after_known:
            print(f"  hammers incremental stop p{page} (known streak {known_streak})", flush=True)
            break
        if page >= maxp or found == 0:
            break
        page += 1
    return urls


def collect_290_urls(known=None, stop_after_known=10):
    known = known or set()
    urls = []
    page = 1
    maxp_known = 281
    known_streak = 0
    while True:
        url = ("https://www.290.jp/archives/category/dive-log" if page == 1
               else f"https://www.290.jp/archives/category/dive-log/page/{page}")
        try:
            r = get(url)
        except Exception as e:
            print(f"  290 list end p{page}: {e}", flush=True)
            break
        soup = BeautifulSoup(r.text, "html.parser")
        found = 0
        for a in soup.select("h1 a, h2 a, .entry-title a, a[rel=bookmark]"):
            href = a.get("href", "")
            if re.match(r"https?://www\.290\.jp/archives/\d+$", href) and href not in urls:
                urls.append(href)
                found += 1
                known_streak = known_streak + 1 if href in known else 0
        if found == 0:
            # fallback: any archives link
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if re.match(r"https?://www\.290\.jp/archives/\d+$", href) and href not in urls:
                    urls.append(href)
                    found += 1
                    known_streak = known_streak + 1 if href in known else 0
        for a in soup.select("a[href*='dive-log/page/']"):
            m = re.search(r"/page/(\d+)", a.get("href", ""))
            if m:
                maxp_known = max(maxp_known, int(m.group(1)))
        if page % 10 == 0 or page == 1:
            print(f"  290 p{page}: total {len(urls)} urls (maxp={maxp_known})", flush=True)
        if known and known_streak >= stop_after_known:
            print(f"  290 incremental stop p{page} (known streak {known_streak})", flush=True)
            break
        if page >= maxp_known or found == 0:
            print(f"  290 done p{page}: total {len(urls)}", flush=True)
            break
        page += 1
    return urls


def collect_ms_urls(known=None, recent_months=3):
    """インクリメンタル時 (known あり) は直近 recent_months ヶ月だけ見る。"""
    known = known or set()
    # get month list from /logs/
    r = get("https://mikomotodivers.com/logs/")
    months = sorted(set(re.findall(r"logs/(20\d{2}/\d{2})/", r.text)))
    if known and len(months) > recent_months:
        months = months[-recent_months:]
    print(f"  ms months: {len(months)} ({months[0] if months else '-'}..{months[-1] if months else '-'})", flush=True)
    urls = []
    for mo in months:
        page = 1
        while True:
            u = (f"https://mikomotodivers.com/logs/{mo}/" if page == 1
                 else f"https://mikomotodivers.com/logs/{mo}/page/{page}/")
            try:
                r = get(u)
            except Exception:
                break
            found = 0
            for m in re.finditer(r"logs/(20\d{2}/\d{2}/\d+\.html)", r.text):
                href = "https://mikomotodivers.com/logs/" + m.group(1)
                if href not in urls:
                    urls.append(href)
                    found += 1
            maxp = 1
            for m in re.finditer(rf"logs/{mo}/page/(\d+)", r.text):
                maxp = max(maxp, int(m.group(1)))
            if page >= maxp or found == 0:
                break
            page += 1
        print(f"  ms {mo}: total {len(urls)}", flush=True)
    return urls


# ============================================================
# Article parsers
# ============================================================
def parse_hammers_article(url, html):
    soup = BeautifulSoup(html, "html.parser")
    date_str = None
    date_el = soup.select_one(".entry-date, time.published, .post-date, time[datetime]")
    if date_el and date_el.get("datetime"):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_el["datetime"])
        if m:
            date_str = m.group(0)
    if not date_str:
        body_text = soup.get_text()
        m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", body_text)
        if m:
            date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    title_el = soup.select_one("h1.entry-title, h1, title")
    title = title_el.get_text(strip=True)[:200] if title_el else ""

    data = {"url": url, "shop": "hammers", "date": date_str, "title": title,
            "raw_table": {}, "body": ""}
    for table in soup.select("table"):
        for row in table.select("tr"):
            ths = row.select("th")
            tds = row.select("td")
            for th, td in zip(ths, tds):
                data["raw_table"][th.get_text(" ", strip=True)] = td.get_text(" ", strip=True)

    content = soup.select_one("article, .single-post, .post, main") or soup.body
    if content:
        for t in content.select("table, img, script, style, nav, aside, header, footer, .breadcrumb, .share, .tags"):
            t.decompose()
        data["body"] = content.get_text(" ", strip=True)[:6000]
    return data


def parse_290_article(url, html):
    soup = BeautifulSoup(html, "html.parser")
    date_str = None
    t = soup.select_one("time.published, time[datetime]")
    if t and t.get("datetime"):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t["datetime"])
        if m:
            date_str = m.group(0)
    body_el = soup.select_one(".entry-content, article, .post-content")
    body = body_el.get_text("\n", strip=True) if body_el else soup.get_text("\n", strip=True)
    if not date_str:
        m = re.search(r"(20\d{2})[./](\d{1,2})[./](\d{1,2})", body[:300])
        if m:
            date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    title_el = soup.select_one("h1.entry-title, h1, title")
    title = title_el.get_text(strip=True)[:200] if title_el else ""

    data = {"url": url, "shop": "290", "date": date_str, "title": title,
            "raw": {}, "body": body[:6000]}
    raw = data["raw"]
    m = re.search(r"気温[\s　]*([\-−\d\.]+)[\-−~〜](\d+\.?\d*)\s*[℃度C]", body)
    if m:
        raw["air_temp_lo"] = float(m.group(1).replace("−", "-"))
        raw["air_temp_hi"] = float(m.group(2))
    m = re.search(r"水温[\s　]*([\-−\d\.]+)[\-−~〜](\d+\.?\d*)\s*[℃度C]", body)
    if m:
        raw["water_temp_lo"] = float(m.group(1).replace("−", "-"))
        raw["water_temp_hi"] = float(m.group(2))
    else:
        m = re.search(r"水温[\s　]*(\d+\.?\d*)\s*[℃度C]", body)
        if m:
            raw["water_temp_lo"] = raw["water_temp_hi"] = float(m.group(1))
    m = re.search(r"透明度[\s　]*([\-−\d\.]+)[\-−~〜](\d+\.?\d*)\s*[mｍMメートル]", body)
    if m:
        raw["visibility_lo"] = float(m.group(1).replace("−", "-"))
        raw["visibility_hi"] = float(m.group(2))
    else:
        m = re.search(r"透明度[\s　]*(\d+\.?\d*)\s*[mｍMメートル]", body)
        if m:
            raw["visibility_lo"] = raw["visibility_hi"] = float(m.group(1))
    m = re.search(r"潮流[\s　]*([^ポ　\n]+)", body)
    if m:
        raw["tide_flow"] = m.group(1).strip()[:60]
    m = re.search(r"ポイント[\s　]*([^\n]{1,60})", body)
    if m:
        raw["points"] = m.group(1).strip()
    return data


def parse_ms_article(url, html):
    soup = BeautifulSoup(html, "html.parser")
    date_str = None
    t = soup.select_one("time[datetime]")
    if t and t.get("datetime"):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t["datetime"])
        if m:
            date_str = m.group(0)
    if not date_str:
        m = re.search(r"/logs/(\d{4})/(\d{2})/(\d{2})", url)
        if m:
            date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    title_el = soup.select_one("h1, title")
    title = title_el.get_text(strip=True)[:200] if title_el else ""

    body_el = soup.select_one(".entry-content, .post-content, article, main")
    body = body_el.get_text("\n", strip=True) if body_el else soup.get_text("\n", strip=True)
    data = {"url": url, "shop": "ms", "date": date_str, "title": title,
            "raw": {}, "body": body[:6000]}
    raw = data["raw"]
    # definition-list / table style pairs
    for table in soup.select("table, dl"):
        rows = table.select("tr")
        if rows:
            for row in rows:
                cells = row.select("th, td")
                if len(cells) >= 2:
                    k = cells[0].get_text(strip=True)
                    v = cells[1].get_text(strip=True)
                    if k and v:
                        raw[k] = v
        else:
            dts = table.select("dt")
            dds = table.select("dd")
            for dt, dd in zip(dts, dds):
                raw[dt.get_text(strip=True)] = dd.get_text(strip=True)
    m = re.search(r"水温[\s　]*(\d+\.?\d*)[\s～〜~\-−]+(\d+\.?\d*)\s*[℃度]", body)
    if m:
        raw["water_temp_lo"] = float(m.group(1))
        raw["water_temp_hi"] = float(m.group(2))
    else:
        m = re.search(r"水温[\s　]*(\d+\.?\d*)\s*[℃度]", body)
        if m:
            raw["water_temp_lo"] = raw["water_temp_hi"] = float(m.group(1))
    m = re.search(r"透[明視]度[\s　]*(\d+\.?\d*)[\s～〜~\-−]+(\d+\.?\d*)\s*[mｍ]", body)
    if m:
        raw["visibility_lo"] = float(m.group(1))
        raw["visibility_hi"] = float(m.group(2))
    else:
        m = re.search(r"透[明視]度[\s　]*(\d+\.?\d*)\s*[mｍ]", body)
        if m:
            raw["visibility_lo"] = raw["visibility_hi"] = float(m.group(1))
    return data


PARSERS = {"hammers": parse_hammers_article, "290": parse_290_article, "ms": parse_ms_article}


def main():
    state = load_state()
    # 既知URL (取得済み) — あればリスト収集はインクリメンタル (新着だけ探して早期打ち切り)
    known = set(state["done_urls"]) | set(state["failed_urls"])

    # Phase A: URL lists
    if "hammers" not in state["url_lists"]:
        print("[list] hammers (mikomoto.com)...", flush=True)
        new_urls = collect_hammers_urls(known=known)
        state["url_lists"]["hammers"] = sorted(set(new_urls) | {u for u in known if "mikomoto.com" in u})
        save_state(state)
    if "ms" not in state["url_lists"]:
        print("[list] ms (mikomotodivers.com)...", flush=True)
        new_urls = collect_ms_urls(known=known)
        state["url_lists"]["ms"] = sorted(set(new_urls) | {u for u in known if "mikomotodivers.com" in u})
        save_state(state)
    if "290" not in state["url_lists"]:
        print("[list] 290 (290.jp)...", flush=True)
        new_urls = collect_290_urls(known=known)
        state["url_lists"]["290"] = sorted(set(new_urls) | {u for u in known if "290.jp" in u})
        save_state(state)

    for shop, urls in state["url_lists"].items():
        print(f"[plan] {shop}: {len(urls)} articles", flush=True)

    done = set(state["done_urls"])
    failed = set(state["failed_urls"])

    # Phase B: fetch articles — newest first per shop, round-robin-ish by shop order
    t0 = time.time()
    n_since_save = 0
    for shop in ["hammers", "ms", "290"]:
        urls = state["url_lists"].get(shop, [])
        # list pages already yield newest-first order
        pending = [u for u in urls if u not in done and u not in failed]
        print(f"[fetch] {shop}: {len(pending)} pending", flush=True)
        for i, u in enumerate(pending):
            try:
                r = get(u)
                rec = PARSERS[shop](u, r.text)
                append_article(rec)
                done.add(u)
            except Exception as e:
                print(f"    FAIL {u}: {e}", flush=True)
                failed.add(u)
            n_since_save += 1
            if n_since_save >= 25:
                state["done_urls"] = sorted(done)
                state["failed_urls"] = sorted(failed)
                save_state(state)
                n_since_save = 0
            if (i + 1) % 100 == 0:
                el = time.time() - t0
                print(f"  {shop}: {i+1}/{len(pending)} elapsed {el/60:.1f}min", flush=True)
    state["done_urls"] = sorted(done)
    state["failed_urls"] = sorted(failed)
    save_state(state)

    # Phase C: compile jsonl -> final json (dedupe by url)
    if not JSONL_F.exists():
        print("[done] no articles.jsonl (no new articles scraped) — skip compile", flush=True)
        return
    seen = {}
    with open(JSONL_F, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            seen[rec["url"]] = rec
    articles = sorted(seen.values(), key=lambda a: (a.get("date") or "", a.get("shop") or ""))
    json.dump({"scraped_at": datetime.now().isoformat(), "n": len(articles),
               "articles": articles},
              open(OUT_F, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[done] {len(articles)} unique articles -> {OUT_F}", flush=True)


if __name__ == "__main__":
    main()
