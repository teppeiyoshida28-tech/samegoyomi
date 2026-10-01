# 鮫暦AI — 引き継ぎドキュメント（GitHub 移行版）

神子元島（伊豆・下田沖）ハンマーヘッドシャーク出現予測サイト。
公開URL: https://teppeiyoshida28-tech.github.io/samegoyomi/

Genspark VM（旧環境 `/home/work/.openclaw/workspace/hammerhead/` + cron + Caddy 配信）から
GitHub Actions + GitHub Pages へ完全移行済み。外部サーバーは一切不要。

## 旧→新 パス対応

| 旧（Genspark VM） | 新（このリポジトリ） |
|---|---|
| `hammerhead/repo/` | `engine/` |
| `hammerhead/phase1/` | `data/`（**生記事テキストは除外** — 下記参照） |
| `hammerhead/analysis/` | `analysis/` |
| `hammerhead/brand/` | `assets/` |
| `/var/www/html/hammerhead/img/` | `docs/img/` |
| `daily_update.sh`（cron 6:30 JST） | `.github/workflows/daily.yml`（cron UTC 21:30 = JST 6:30） |
| `monitor.py`（異常時メール送信） | `engine/monitor.py`（Actions の job fail → GitHub 通知） |
| Caddy 配信（旧ドメイン） | GitHub Pages（main ブランチ `/docs`） |

## 運用（全部 GitHub 内で完結）

- **自動**: 毎朝 JST 6:30 に daily.yml 実行。スクレイプ→構造化→再学習→予報→ビルド→commit & push。
  Pages は push を検知して自動再デプロイ（数分ラグあり）
- **手動実行**: Actions タブ → daily-forecast → Run workflow
- **失敗監視**: ジョブ失敗時は GitHub からメール通知。`monitor.py` がデータ鮮度・ビルド結果・黒潮期限を検査して異常なら fail させる
- **黒潮予測の更新**（手動・2〜8週に1回程度）:
  1. https://www.jamstec.go.jp/aplinfo/kowatch/ を確認（毎週水曜更新）
  2. `engine/kuroshio_forecast.json` の `status` / `label` / `valid_until` / `score` / `updated` を編集して push
  - 期限切れが近いと Actions に warning、切れると monitor が error を出す
- **コード修正**: ローカルに clone して編集 → push。AI コーディングエージェント（Codex / Claude Code 等）でも可

## 著作権配慮: 生記事テキストは含まない

- 旧 phase1 の `articles.jsonl` / `dive_logs_raw_full.json`（ブログ記事の生テキスト 約18MB）は
  **public リポジトリに含めない**（.gitignore 済み）。`data/` にあるのは構造化済みの事実データのみ
- 増分スクレイプは `data/scrape_state.json`（URL管理）だけで新規記事から再開できる
- 新着分の生テキストは Actions 実行中に一時生成 → `structure_full.py` が既存の構造化ログへ URL マージ → 生ファイルはコミットされない

## スクレイパー運用上の注意

- ショップブログ3店への増分スクレイプは **2秒間隔のレート制限を厳守**（`data/full_scraper.py` 内に実装済み）
- GitHub Actions の IP はブロックされる可能性がゼロではない。scrape ステップは `continue-on-error` で、
  失敗しても既存データで予報生成は続行する設計
- スクレイプが4日以上止まると monitor が error を出す（ブログ構造変更の疑い → `full_scraper.py` のパーサを確認）

## 詳細

セットアップ手順・パイプライン構成・ドメイン知識・未完了タスクは README.md を参照。
