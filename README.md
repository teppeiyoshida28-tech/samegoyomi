# 鮫暦AI — 神子元ハンマー潮予報

神子元島（伊豆・下田沖）のハンマーヘッドシャーク出現予測サイト。
毎朝 GitHub Actions が自動でデータ更新・再学習・予報生成・サイトビルドを行い、GitHub Pages で公開する。

- **公開URL**: https://teppeiyoshida28-tech.github.io/samegoyomi/
- **自動更新**: 毎日 JST 6:30（UTC 21:30）に `.github/workflows/daily.yml` が実行

## ディレクトリ構成

| パス | 内容 |
|---|---|
| `engine/` | 予測エンジン本体・HTMLテンプレ・ビルドスクリプト（旧 `repo/`） |
| `data/` | 過去データ基盤（旧 `phase1/`）。**構造化済みの事実データのみ**・hindcast環境データ・月齢/潮名カレンダー・黒潮流路履歴 + 収集/構造化/学習スクリプト |
| `analysis/` | ポイント別実績出現率の集計（point_stats.json ほか） |
| `assets/` | ブランド素材（浮世絵ヒーロー・家紋ロゴ・検証スクショ） |
| `docs/` | **ビルド出力 = GitHub Pages 公開ルート**。`docs/img/` に写真素材 |
| `.github/workflows/daily.yml` | 毎朝の自動更新パイプライン |

## engine/ の主要ファイル

- `forecast_engine.py` — 予測エンジン本体。Open-Meteo Marine/Weather 取得 → SST平年偏差・合成流（海流+潮汐）・海況・季節・実績事前確率でスコアリング → `forecast_data.json`
- `models.py` / `weight_learner.py` / `backtest.py` — 学習・検証系
- `enrich_forecast.py` — ML モデルの予測を forecast_data.json に追記
- `build_html.py` — forecast_data.json をテンプレに埋め込み 3ページ生成
- `build_longrange.py` / `build_history_html.py` — 長期狙い目カレンダー・検証履歴ページ
- `index.html` / `history_index.html` — ページテンプレ（JS/CSSインライン）
- `monitor.py` — パイプライン末尾の死活監視。異常時は `::error::` を出してジョブを fail させる
- `01_research_predictors.md` / `02_data_sources.md` — 学術知見まとめ・データソース仕様

## データソース（すべて無料・認証不要）

- **Open-Meteo Marine API** — 海流・SST・波・潮位。フル予報は約9日先まで
- **Open-Meteo Weather/Archive API** — 風・降水（16日先）、過去再解析
- **気象庁 下田(SD) 潮位表** — https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt/2026/SD.txt
- **JAMSTEC 黒潮親潮ウォッチ** — 黒潮流路の長期予測（週次更新・スクレイプ）
- **ショップブログ3店**（神子元ハンマーズ mikomoto.com ほか）— 実績ログ。**2秒間隔のレート制限を厳守**

### 著作権配慮（重要）

スクレイプしたブログ記事の**生テキスト（`data/articles.jsonl` / `data/dive_logs_raw_full.json`）はこのリポジトリに含まない**（public リポジトリのため著作権配慮、.gitignore 済み）。
`data/` には水温・透明度・出現有無などの**構造化済み事実データ**（`dive_logs_structured_full.json` 等）のみを収録する。
`scrape_state.json`（URLリストと取得済み管理）は含むため、増分スクレイプは新規記事からそのまま再開できる。
Actions 実行時に新着記事の生テキストが一時生成されるが、`structure_full.py` が構造化後に既存データへマージするためコミットされない。
- **NOAA OISST** — 2015-2022 の日次SST（hindcast用、取得済み）

## 自動更新パイプライン（daily.yml）

1. ショップログ増分スクレイプ（新着のみ、timeout 1h、失敗しても続行）
2. 構造化 → `engine/dive_logs_structured.json` へ反映
3. 重み再学習（全量）
4. バックテスト（隔週: 偶数ISO週の日曜 JST のみ）
5. `forecast_engine.py` → `enrich_forecast.py`
6. 潮汐ヒートマップ + 長期カレンダー
7. HTML ビルド → `docs/` へ配置
8. 黒潮予測の期限チェック（残り14日以下で `::warning::`）
9. データ・docs/ をリポジトリにコミット & push
10. `monitor.py` ヘルスチェック（異常なら job fail → 通知）

## セットアップ（新しい環境に clone した場合）

```bash
pip install -r requirements.txt
cd engine
python3 forecast_engine.py   # 予報生成
python3 enrich_forecast.py   # ML enrich
python3 build_longrange.py
python3 build_html.py        # HTML 生成
```

### GitHub Pages 設定（手動で行う場合）

Settings → Pages → Source: **Deploy from a branch** → Branch: `main` / フォルダ: `/docs` → Save

### 失敗通知

Actions のジョブが失敗すると GitHub から自動でメール通知が届く
（GitHub の Settings → Notifications → Actions で "Only notify for failed workflows" を有効にしておくと便利）。
個人のメールアドレスはリポジトリには含めない。

## 運用メモ

- **黒潮長期予測** (`engine/kuroshio_forecast.json`) は手動更新。JAMSTEC 黒潮親潮ウォッチ（毎週水曜更新）を確認し、`valid_until` と `status` を書き換える。期限が近づくと Actions に warning が出る
- **コード修正**はローカルに clone して編集 → push（Codex / Claude Code 等の AI コーディングエージェントも可）。次回の daily run から反映される
- 手動で即時更新したい場合: Actions タブ → daily-forecast → **Run workflow**

## ドメイン知識の要点

- ランク S〜E（おみくじ併記）。教師データはショップブログの目撃記録（観測バイアスあり、analysis/ に注記）
- 実績ベースの知見: Aポイント×下げ潮=出現率88%(n=201) / 下げ潮>上げ潮 / 黒潮大蛇行期>離岸期(▲20-30pt) / 潮名の影響は小
- ユーザー実測の確定ラベル: 2023-11-05 に300匹超の大群（SST24.9°C・流速2kt・北東流が1週間安定継続）
- 「強い流れ×同方向の暖流が安定継続」が大群条件という仮説を採用
- 潮汐は Open-Meteo の海流に含まれる（自前潮汐項との二重加算に注意 — 既知の課題）

## 未完了タスク（優先順）

1. マップの実地図化（地理院タイル航空写真+等深線、カメ根エリアのラベル密集解消）
2. 観測バイアス補正（出現と発見の分離）
3. 潮汐二重加算問題の検証・解消（Copernicus SMOC で分離検証）
4. 検証指標の常設表示（Brier/AUC、ベースライン比較）
