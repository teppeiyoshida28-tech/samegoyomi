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
from datetime import date, datetime
from pathlib import Path
from domain import today_jst, MODEL_VERSION
from validation import validate_forecast
from point_catalog import CATALOG

BASE = os.path.dirname(os.path.abspath(__file__))        # engine/
REPO_ROOT = os.path.dirname(BASE)                         # リポジトリルート

problems = []
warnings = []


def check_obs_freshness():
    """ショップログの鮮度: 最新報告が4日以上前なら異常 (スクレイパー故障の疑い)"""
    try:
        d = json.load(open(os.path.join(BASE, "dive_logs_structured.json")))
        latest = max(s["date"] for s in d["daily_summary"])
        age = (today_jst() - date.fromisoformat(latest)).days
        if age >= 4:
            problems.append(
                f"ショップログの最新報告が {latest} ({age}日前) — "
                "スクレイパー故障 or ブログ構造変更の疑い。Actionsのscrapeステップのログを確認")
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
        problems.extend(validate_forecast(d))
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
        k = json.load(open(os.path.join(BASE, "kuroshio_forecast.json")))
        days_left = (date.fromisoformat(k["valid_until"]) - today_jst()).days
        if days_left <= 0:
            problems.append(
                f"黒潮長期予測が期限切れ ({k['valid_until']}) — JAMSTEC黒潮親潮ウォッチ"
                "(毎週水曜更新)を確認して engine/kuroshio_forecast.json を更新してください")
        elif days_left <= 14:
            warnings.append(f"黒潮長期予測の残り有効期間 {days_left}日 — そろそろ更新")
    except Exception as e:
        problems.append(f"kuroshio_forecast.json 確認失敗: {e}")


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
