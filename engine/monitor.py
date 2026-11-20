#!/usr/bin/env python3
"""鮫暦AI 死活監視 — GitHub Actions 版。

daily.yml の公開前に実行する。異常があれば GitHub Actions のアノテーション
(::error:: / ::warning::) を出し、exit 1 でジョブを fail させる。
→ ジョブ失敗時は GitHub が自動でリポジトリ watch 者へ通知メールを送る
  (Settings > Notifications で Actions の失敗通知を有効にしておくこと)。
メール送信などの外部依存はなし。
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from domain import today_jst, now_jst, MODEL_VERSION
from validation import validate_forecast
from point_catalog import CATALOG

BASE = os.path.dirname(os.path.abspath(__file__))        # engine/
REPO_ROOT = os.path.dirname(BASE)                         # リポジトリルート

problems = []
warnings = []


def observation_health_errors(health, now):
    """Publication needs evidence of successful discovery, not a recent sighting."""
    errors = []
    if type(health.get('schema_version')) is not int or health['schema_version'] != 1:
        return ['ショップ取得結果の形式が無効です']
    sources = health.get('sources', {})
    # The configured three sources must all be checked. A missing source must
    # not make all([]) look like a successful scrape.
    expected = {'hammers', 'ms', '290'}
    if not expected or set(sources) != expected:
        return ['ショップ取得結果の対象が設定と一致しません']
    for shop, result in sources.items():
        try:
            checked = datetime.fromisoformat(result['checked_at'])
            fresh = checked.utcoffset() is not None and -timedelta(minutes=5) <= now-checked < timedelta(hours=36)
        except (KeyError, TypeError, ValueError):
            fresh = False
        counts = [result.get(key) for key in ('discovered_count', 'new_url_count', 'article_attempted',
                  'article_succeeded', 'article_failed', 'unresolved_url_count')]
        counts_valid = all(type(value) is int and value >= 0 for value in counts)
        if (not fresh or result.get('status') != 'ok' or result.get('listing_status') != 'ok'
                or not counts_valid or result['discovered_count'] < 1
                or result.get('article_failed') != 0 or result.get('unresolved_url_count') != 0
                or result.get('listing_regressed') is not False
                or result.get('article_attempted') != result.get('article_succeeded')
                or result['article_succeeded'] < result['new_url_count']):
            errors.append(f'{shop}: 新着一覧・記事の取得/解析を確認できません')
    return errors


def check_obs_freshness():
    """Separate observation date from verified source polling, including no-post days."""
    try:
        d = json.load(open(os.path.join(BASE, "dive_logs_structured.json")))
        latest = max(s["date"] for s in d["daily_summary"])
        age = (today_jst() - date.fromisoformat(latest)).days
        if age < 0:
            problems.append(f'ショップログに未来の日付があります: {latest}')
        health = json.loads(Path(REPO_ROOT, 'data', 'scrape_health.json').read_text(encoding='utf-8'))
        problems.extend(observation_health_errors(health, now_jst()))
        polled_latest = max((row.get('latest_article_date') or '' for row in health.get('sources', {}).values()), default='')
        if polled_latest != latest:
            problems.append('取得確認済みの最新記事日と構造化ログの日付が一致しません（構造化・同期を確認）')
        if age >= 4:
            warnings.append(f"ショップログ最新: {latest} ({age}日前)。取得処理の成否と区別し、無報告を欠航・非目撃に転用しません")
        elif age >= 3:
            warnings.append(f"ショップログ最新: {latest} ({age}日前) — 海況不良なら正常")
        return latest
    except Exception as e:
        problems.append(f"dive_logs_structured.json 読込失敗: {e}")
        return None


def check_forecast_freshness():
    """forecast_data.json が今日更新されているか"""
    try:
        d = json.load(open(os.path.join(BASE, "forecast_data.json")))
        gen = d.get("generated_at", "")[:10]
        if gen != today_jst().isoformat():
            problems.append(f"forecast_data.json が今日更新されていない (generated_at={gen})")
        daily = d.get("daily", [])
        problems.extend(validate_forecast(d, require_fresh=True))
        if not daily or daily[0]["date"] != today_jst().isoformat():
            problems.append("本日の予報がありません")
        if daily and "ml" not in daily[0]:
            problems.append("ML enrich が失敗 (daily[0] に ml キーなし) — Open-Meteo API 一時障害の可能性")
        scores = [x.get("score", 0) for x in daily]
        if len(scores) >= 5 and max(scores) - min(scores) < 1.0:
            warnings.append(f"14日予測スコアがほぼ均一 ({min(scores):.1f}-{max(scores):.1f}) — モデル異常の可能性")
    except Exception as e:
        problems.append(f"forecast_data.json 読込失敗: {e}")


def check_site_built():
    """docs/ に各ページが生成されているか + タイトル・データ埋め込み確認"""
    site = os.path.join(REPO_ROOT, "docs")
    for rel in ["index.html", "map/index.html", "aim/index.html", "history/index.html"]:
        p = os.path.join(site, rel)
        if not os.path.exists(p):
            problems.append(f"docs/{rel} が存在しない (ビルド失敗)")
            continue
        html = Path(p).read_text(encoding="utf-8", errors="replace")
        if "鮫暦" not in html:
            problems.append(f"docs/{rel} にタイトルがない (テンプレ破損?)")
        if rel != "history/index.html":
            try:
                match = re.search(r"const FORECAST = (.*?);\s*\n", html)
                embedded = json.loads(match.group(1))
                current = json.loads(Path(BASE, "forecast_data.json").read_text(encoding="utf-8"))
                if embedded.get("daily") != current.get("daily") or embedded.get("generated_at") != current.get("generated_at"):
                    problems.append(f"docs/{rel} が現在の予報と一致しません（古いビルド）")
                map_match = re.search(r"const MAP_CATALOG = (.*?);\s*\n", html)
                if json.loads(map_match.group(1)) != CATALOG:
                    problems.append(f"docs/{rel} の地点座標が予測計算の定義と一致しません")
            except (AttributeError, ValueError) as e:
                problems.append(f"docs/{rel} の埋込予報が無効: {e}")
        elif "/*__BACKTEST_JSON__*/ null" in html or MODEL_VERSION not in html:
            problems.append("検証ページのデータまたはモデル版が古いです")


def check_kuroshio():
    try:
        from longrange_contract import validate_kuroshio
        k = json.load(open(os.path.join(BASE, "kuroshio_forecast.json")))
        problems.extend(validate_kuroshio(k))
        days_left = (date.fromisoformat(k["valid_until"]) - today_jst()).days
        if days_left < 0:
            warnings.append(f"黒潮資料の対象期間が終了 ({k['valid_until']})。長期カレンダーでは黒潮未評価として扱います")
        elif days_left <= 14:
            warnings.append(f"黒潮長期予測の残り有効期間 {days_left}日 — そろそろ更新")
        if k.get('status') == 'unknown':
            warnings.append('黒潮流路は未評価。資料の発表日・対象期間を表示し、計算上は中立値を使用します')
        retrieved = datetime.fromisoformat(k.get('retrieved_at', ''))
        if retrieved.utcoffset() is None or now_jst()-retrieved >= timedelta(hours=36):
            warnings.append('黒潮資料の新着確認が36時間以上前です。資料の取得日を画面で確認してください')
    except Exception as e:
        problems.append(f"kuroshio_forecast.json 確認失敗: {e}")


def check_longrange():
    from longrange_contract import validate_longrange
    try:
        data = json.loads(Path(BASE, 'longrange_data.json').read_text(encoding='utf-8'))
        problems.extend(validate_longrange(data, now=now_jst()))
        for rel in ['index.html', 'aim/index.html']:
            html = Path(REPO_ROOT, 'docs', rel).read_text(encoding='utf-8')
            match = re.search(r'const LONGRANGE = (.*?);\s*\n', html, re.S)
            if not match or json.loads(match.group(1)) != data:
                problems.append(f'docs/{rel} の長期カレンダーが現在のデータと一致しません')
    except Exception as e:
        problems.append(f'長期カレンダーの検査失敗: {e}')


def check_backtest_age():
    """バックテストは2週に1回。backtest_data.json 内の generated_at で鮮度確認"""
    try:
        p = os.path.join(BASE, "backtest_data.json")
        bt = json.load(open(p))
        gen = bt.get("generated_at", "")[:10]
        if gen:
            age = (today_jst() - date.fromisoformat(gen)).days
            if age >= 20:
                warnings.append(f"backtest_data.json が {age}日更新されていない (2週サイクルのはず)")
    except Exception as e:
        warnings.append(f"backtest 鮮度確認失敗: {e}")


def main():
    if "--forecast-only" in sys.argv:
        check_forecast_freshness()
        if problems:
            raise RuntimeError("; ".join(problems))
        print("[monitor] forecast invariants OK")
        return
    latest_obs = check_obs_freshness()
    check_forecast_freshness()
    check_site_built()
    check_kuroshio()
    check_longrange()
    check_backtest_age()

    for w in warnings:
        print(f"::warning::{w}")
    if problems:
        for p in problems:
            print(f"::error::{p.splitlines()[0]}")
        print(f"[monitor] {len(problems)} problem(s) found")
        sys.exit(1)
    print(f"[monitor] all ok (latest obs: {latest_obs})")


if __name__ == "__main__":
    main()
