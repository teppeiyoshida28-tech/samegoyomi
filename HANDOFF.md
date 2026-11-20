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
| Caddy 配信（旧ドメイン） | GitHub Pages（Actionsが `docs/` を直接deploy） |

## 運用（全部 GitHub 内で完結）

- **自動**: 毎朝 JST 6:30 に daily.yml 実行。スクレイプ→構造化→統計更新→再学習→予報→ビルド→公開前検査→commit/push→Pages直接deploy→公開記録保存。
  PagesのSourceは **GitHub Actions** に切り替える。標準トークンのpushでは旧ブランチ式ビルドは起動しない。
- **手動実行**: Actions タブ → daily-forecast → Run workflow
- **失敗監視**: ジョブ失敗時は GitHub からメール通知。`monitor.py` がデータ鮮度・ビルド結果・黒潮期限を検査して異常なら fail させる
- **黒潮資料の更新**: `engine/fetch_kuroshio.py`が公式RSSから日次取得。流路は日別の根拠がない限り未評価。資料期限後も天文・季節カレンダーは未評価を明示して継続する
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
- monitorは3店の新着確認結果・解析・未解決URLを検査する。記事日が4日以上前でも正常取得が確認できれば警告で継続する

## 整合性修正（2026-10）

海況判定・欠測・推薦根拠の不一致を修正。教師ラベルの未知を陰性にしない。
`history/` は公開済み予報を予測日数別に評価し、過去再計算と区別する。
`forecast_archive.py --save --published-at now` は公開成功後だけ実行する。記録保存のpushに失敗した場合は、該当Actions runの `issued-forecast-*` artifactから回収する。
最新の統計定義・検証限界・ローカル手順はREADME.mdとIMPLEMENTATION_STATUS.mdを参照。

2026-10-04の地図改善では、地点座標を`map_points.json`へ一元化し、表示都合で予想域を動かす処理を廃止。
スマホの月間カレンダー、地図の拡大・移動・地点詳細と、海況を先に読むトップを実装。
北側はオーナー指定により予測対象外。実測していない等深線・局所水温分布は表示しない。
地点のGPS校正・海底地形と局所流の実測検証は残る。詳細はAGENTS.mdのマップ節を参照。

## 2026-11-21 更新待ちの復旧

黒潮資料は`fetch_kuroshio.py`で公式RSSの最新号を日次確認する方式へ変更（上記手動更新手順を置換）。記事の発表日・対象期間と、黒潮流路の評価を分離し、未評価はnullで公開する。

ショップ記事が4日以上前でも、`scrape_health.json`で3店の一覧と新着処理を確認できれば警告で継続する（上記の一律停止を置換）。取得エラー・未処理・一覧巻き戻りは停止。2秒以上間隔、取得済み管理、生本文の除外は維持。

06:35の独立更新で海流に加えて183日分の参考カレンダーも再生成する。短期予報の有効期限とは別に表示でき、期限切れ短期スコアを重ねない。公開されたデータの照合後だけ予報を発表記録に追記する。

詳細は`analysis/REPORT_forecast_recovery.md`。旧予報が停止中という以下の2026-11-21海流導入時の記録は、復旧前の経緯である。

## 詳細

### 海流マップの独立更新（2026-11-21）

承認済みの地形・粒子表示を`engine/map_panel.html`、`map_ui.js`、`ocean_map.css`へ実装。
`ocean_current.js`は流速換算・0〜6kt表示・任意流向の合成・鮮度判定を担う。
新しい`ocean-map.yml`は毎朝06:35 JSTに海流だけを取得・検査・ビルド・直接公開する。
既存dailyと同じconcurrency groupで直列化し、ハンマー予報の検査や発表記録は迂回しない。
海流のみの公開は`ocean-published-*` artifact（90日）に保存し、`forecast_archive.py --save`を実行しない。

黒潮資料とショップログに依存する旧予報は依然として独自の更新条件を持つ。
`forecast_freshness.js`が期限切れのスコア・推奨・長期カレンダーを隠し、更新待ちと明示する。
海流の取得成功を旧予報全体の復旧と解釈しないこと。

セットアップ手順・パイプライン構成・ドメイン知識・未完了タスクは README.md を参照。
