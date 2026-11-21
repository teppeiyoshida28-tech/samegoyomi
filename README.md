# 鮫暦AI — 神子元ハンマー潮予報

神子元島（伊豆・下田沖）のハンマーヘッドシャーク出現予測サイト。
毎朝 GitHub Actions が自動でデータ更新・再学習・予報生成・サイトビルドを行い、GitHub Pages で公開する。

- **公開URL**: https://teppeiyoshida28-tech.github.io/samegoyomi/
- **自動更新**: 毎日 JST 6:30（UTC 21:30）に `.github/workflows/daily.yml` が実行
- **海流マップ・参考カレンダー**: 毎日 JST 6:35（UTC 21:35）に `ocean-map.yml` が独立更新。短期予報停止時も有効な海流・天文カレンダーを提供し、期限切れの短期スコアは非表示

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

- `forecast_engine.py` — 予測エンジン本体。Open-Meteo Marine/Weather 取得 → SST平年偏差・SMOC総流（潮汐を含み再加算なし）・海況・季節・実績事前確率でスコアリング → `forecast_data.json`
- `models.py` / `weight_learner.py` / `backtest.py` — 学習・検証系
- `enrich_forecast.py` — ML モデルの予測を forecast_data.json に追記
- `build_html.py` — forecast_data.json をテンプレに埋め込み 3ページ生成
- `build_longrange.py` / `build_history_html.py` — 長期狙い目カレンダー・検証履歴ページ
- `index.html` / `history_index.html` — ページテンプレ（JS/CSSインライン）
- `map_points.json` / `point_catalog.py` — 予測計算と地図表示で共有する26地点の概略座標。GPS校正前。南側のみを予測対象とする
- `map_geometry.js` / `map_ui.js` / `map_panel.html` / `ui_refresh.css` — 地図の幾何・操作・構造・表示。ビルド時にHTMLへ埋め込む
- `monitor.py` / `validation.py` — 公開前の整合性・鮮度検査。異常時は公開を停止する
- `forecast_archive.py` — Pages 公開成功後の予報を `forecast_archive/` に追記保存。生成日時・公開日時・モデル版・コード SHA・予報ハッシュ・予測日数を記録
- `evaluation.py` / `domain.py` — 評価指標・教師ラベル・海況判定・JST の共通定義
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

1. 回帰テスト → ショップログ増分スクレイプ（2秒以上間隔、失敗URLは回数・期限付き再試行）
2. 構造化済みデータへマージ → engine 同期 → ポイント統計を再集計（破損した既存データは上書きしない）
3. 重み再学習 → 過去再計算（偶数ISO週の日曜JST、またはモデル版変更時）
4. 海況取得 → 予報 → ML追記（海況判定・推奨地点と根拠は一貫させる）
5. 潮汐ヒートマップ・長期カレンダー・公開予報の実績照合 → HTMLビルド
6. 黒潮期限・データ鮮度・欠測・推奨整合性を **公開前** に検査
7. 検査済みデータ・HTMLをcommit/push（競合時は停止）→ Pagesへ直接deploy
8. **公開成功後だけ** 予報スナップショットを追記保存・commit/push。別途Actions artifactにも90日保管

日次処理はGitHub上で動くためPC停止中も実行される。Codexのローカル開発作業にはPCの起動が必要。
mainへの関連コードpush時にも同じパイプラインを実行する。

## 検証と教師データの読み方

- `history/` の初期表示は **公開済み予報**。予測日数別に評価し、同じ対象日・予測日数では最初の発表を採る。公開前の既存予報は遡って登録しない。
- 「過去再計算」は固定した学習期間と過去の環境値による別評価。発表時点のAPI予報を再現した成績ではない。
- 常時目撃あり・過去同月の目撃率・前日実績との比較、Brier/AUC・感度/特異度・確率帯別実測率を表示。各指標で有効件数を示す。
- 目撃あり・なし・不明を区別。目撃ありでも規模不明なら規模学習/評価から除外する。記事の言及地点、訪問地点、明記された目撃地点を分離し、旧地点ラベルを確定目撃に転用しない。
- ガイドの行先選択・透明度・記事の記載頻度による偏りは未解消。記事目撃率は自然界の出現確率ではない。スコアも遭遇確率ではない。
- フル予報は8〜14時の必須値が全て揃う日だけ。欠測日は長期見込みとして区別。波/風の海況判定は運航判断を保証しない。
- `data/recent_environment.json` は取得できた過去環境値の補助キャッシュ。hindcastとの隙間は無理に埋めず評価対象から除外する。

## セットアップ（新しい環境に clone した場合）

```bash
pip install -r requirements.txt
cd engine
python3 forecast_engine.py   # 予報生成
python3 enrich_forecast.py   # ML enrich
python3 build_longrange.py
python3 build_html.py        # HTML 生成
python3 forecast_archive.py  # 既存の公開記録と実績を照合（保存はしない）
python3 build_history_html.py
```

### GitHub Pages 設定（手動で行う場合）

Settings → Pages → Source: **GitHub Actions** に設定する。daily.yml が検査済み `docs/` を直接デプロイする。

`GITHUB_TOKEN` によるpushはブランチ方式のPagesビルドを起動しないため、旧 `main /docs` 設定から切り替える。
参考: [GitHub公式の公開設定](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)。

### 失敗通知

Actions のジョブが失敗すると GitHub から自動でメール通知が届く
（GitHub の Settings → Notifications → Actions で "Only notify for failed workflows" を有効にしておくと便利）。
個人のメールアドレスはリポジトリには含めない。

## 運用メモ

- **黒潮資料** (`engine/kuroshio_forecast.json`) は`fetch_kuroshio.py`が公式RSSから発表日・対象期間・URLを日次取得。日別流路は自動分類せず、未評価の期間は公開値null・長期計算上だけ中立0.5と明記する。本文確認なしの期限延長・接岸扱いは禁止
- **コード修正**はローカルに clone して編集 → push（Codex / Claude Code 等の AI コーディングエージェントも可）。次回の daily run から反映される
- 手動で即時更新したい場合: Actions タブ → daily-forecast → **Run workflow**

## ドメイン知識の要点

- ランク S〜E（おみくじ併記）。教師データはショップブログの目撃記録（観測バイアスあり、analysis/ に注記）
- 旧集計のAポイント×下げ潮88%(n=201)等は、未知を非目撃に含めていた旧ラベル定義の参考値。現行の率・分母は `analysis/point_stats.md` を参照し、異なる定義の率を直接比較しない
- ユーザー実測の確定ラベル: 2023-11-05 に300匹超の大群（SST24.9°C・流速2kt・北東流が1週間安定継続）
- 「強い流れ×同方向の暖流が安定継続」が大群条件という仮説を採用
- 潮汐は Open-Meteo の海流に含まれる。運用は追加加算なし。旧方式との比較と未解決の局所流校正は `analysis/REPORT_current_validation.md` を参照

## 未完了タスク（新しい観測・蓄積が必要）

1. 公開予報のサンプルを蓄積し、ベースラインを超える識別力・確率校正を検証する
2. 観測バイアス補正（潜在的な出現と発見の分離。非目撃・透明度・潜水地点の系統的ログが必要）
3. 島周辺の局所流を地点・時刻・深度別観測で校正する。SMOC総流だけで岬効果を再現できるとは限らない
4. ポイント座標の現地校正・実地図化（今回の信頼性修正の対象外）

回帰テスト: `python -m unittest discover -s tests -v`。構文検査: `python -m compileall -q engine data analysis`。

地図の回帰テスト: `node --test tests/map_geometry.test.js`（Node.js 22）。位置を動かさない表示範囲調整・方位・縮尺・拡大と固定スコア色を検査する。
公開前monitorは、公開HTMLの地点定義と予測計算用の定義の一致も検査する。テンプレ変更後は必ずbuild_html.pyを実行し、生成HTMLをdocsへコピーする。

## マップの読み方

神子元島南側の、ユーザー提供図をトレースした地図です。海上の島・露出岩と海底の根を別色で表し、灯台と原図の深度を保持しています。GPS・測深による校正は未実施で、航行用地図ではありません。ポイント拡大はありません。

日付と時刻を選ぶと、Open-Meteo経由のCopernicus Marine SMOC表面流（約8km格子）の流速・流向に切り替わります。色と線の長さは0〜6ktの固定尺度。6kt超も数値は実値のままで、色・長さだけが上限になります。0.2kt未満では方向を描きません。欠測を0ktに置き換えません。

「回り込み（模式）」は、広域の予報方向を島・露出岩に沿わせたイメージです。根ごとの流速・反流・水深別の予報ではなく、海底根を陸として扱いません。ユーザー指定により下部の操作パネルを削除し、回り込みと深度を常時表示しています。一時停止は地図内のボタンで操作できます。北側は対象外。旧モデルの地点スコアと未校正のトレース座標を結合していません。

## トップとカレンダーの表示

トップはサイトタイトル、4ページへのサイトマップ、神子元島とアカシュモクザメの紹介、ハンマー予報の順です。タイトル・ナビ・紹介は予報が期限切れでも残ります。原稿の出典は本文にリンクしています。

月間カレンダーは各日に概略月相と8〜15時（JST）の潮位曲線を表示し、日付を選ぶと月齢と軸付き曲線を読めます。既存の天文潮位調和近似（平均海面基準）を使い、全183日で縦軸を統一しています。現地の潮流や潮止まり時刻とは区別してください。

ファーストビュー5案は[比較ページ](https://teppeiyoshida28-tech.github.io/samegoyomi/design/first-view/)に分離しています。最終選択前のデザイン候補であり、予報値のデモは含めません。`build_html.py`が同時に生成します。

対象時刻・取得時刻・取得データの有効期限・返却格子中心を表示します。モデル発表時刻はAPIから取得できず「不明」。取得後36時間で表示を失効させ、古い流速や粒子を描き続けません。取得の鮮度はモデル自体の更新や予報精度の保証ではありません。

JAMSTEC・JAXAの海中天気予報と気象庁の50m深海流予想図は比較用リンクです。数値を本図へ合成せず、SMOCに潮汐流を再加算しません。詳細は`analysis/REPORT_live_ocean_map.md`を参照。

### 海流・参考カレンダーの独立更新・公開

```bash
python engine/fetch_ocean_current.py
python engine/fetch_ocean_current.py --validate-only
python engine/fetch_kuroshio.py
python engine/build_longrange.py
python engine/build_html.py
cp engine/mikomoto_forecast.html docs/index.html
cp engine/mikomoto_map.html docs/map/index.html
cp engine/mikomoto_aim.html docs/aim/index.html
python engine/validate_ocean_publication.py --marine-only
```

`ocean-map.yml`は毎朝06:35 JSTと関連コードのmainへのpushで実行します。既存dailyと公開処理を直列化し、取り込み失敗・欠測・48時間先までの有効範囲不足・埋め込み不一致では公開しません。既存full予報のmonitorは維持し、海流専用経路から未発表のハンマー予報を公開しないことを発表記録のハッシュで検査します。

公開成功後は海流・長期カレンダー・黒潮資料を`ocean-published-*` Actions artifactへ90日保存。ハンマー予報アーカイブは追記しません。短期・長期それぞれの期限で表示を制御し、短期が止まっても有効な天文・季節カレンダーは利用できます。

回帰検査は `python -m unittest discover -s tests -v` と `node --test tests/*.test.js`。Windowsでは `PYTHONUTF8=1` を設定してください。

### 予報と長期カレンダーの復旧（2026-11-21）

`environment_inputs.py`がSMOCと波・気象を明示して取得し、時刻で結合・単位検査します。最新取得に失敗した未来入力を過去キャッシュで置き換えません。観測の古さはモデル入力と画面の両方で扱います。

`data/full_scraper.py --refresh-lists`は増分管理を保ち、3店の確認結果を`data/scrape_health.json`へ保存。取得・解析が正常な無投稿日は古い記事日だけで予報を止めず、取得失敗や未解決URLは公開検査で停止します。無報告を欠航・非目撃に変換しません。

`longrange_contract.py`で当日から183日の連続性と因子を検査。カレンダーは短期予報から独立し、06:35の海流更新経路でも再生成します。黒潮未評価日は計算上中立として明示し、天文潮位から局所流速や出航可否を断定しません。

公開後の`verify_publication.py`は3ページの実配信データを照合し、成功後だけ発表記録を保存します。旧版公開記録は保持します。詳細・限界は`analysis/REPORT_forecast_recovery.md`。
