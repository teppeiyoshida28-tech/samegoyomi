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

- **黒潮長期予測** (`engine/kuroshio_forecast.json`) は手動更新。JAMSTEC 黒潮親潮ウォッチ（毎週水曜更新）を確認し、`valid_until` と `status` を書き換える。期限が近づくと Actions に warning が出る
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

南側の概略図で、実測等深線や航行用地図ではない。地理院地図で島の位置を確認できるが、地点は未測量のため実地図へ重ねていない。
拡大・ドラッグ移動・矢印キー移動・全画面に対応。上位3地点以外も地図または選択欄から詳細を確認できる。
「潮陰・予想域の仮説」をオンにすると未検証の仮説領域を表示する。画面外の領域は移動せず、「予想域まで表示」で全体を見る。
広域海流と水温は選択した日付・時刻の値。地図の色は地点スコア0〜100の固定目盛りで、遭遇確率ではない。
カレンダーは日付を押すと、フル予報では海況・推奨地点、長期見込みでは潮・黒潮の前提を表示する。
