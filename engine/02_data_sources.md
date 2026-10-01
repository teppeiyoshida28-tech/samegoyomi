# 神子元島周辺 海洋データソース / API 一覧

作成日：2026-08-23 / 対象格子：神子元島 34.56°N, 138.94°E （下田港から南約9km）

---

## 0. 総括：まず何を使うか

このプロジェクトのMVPを最短で立ち上げるための3点セットは以下です。
それ以外は精度向上のオプションと考えてください。

| データ | ソース | 理由 |
|---|---|---|
| **海流ベクトル・SST・波・潮位** | **Open-Meteo Marine API** | 1点lat/lonでJSON、無料、16日先、hourly、認証不要 |
| **潮汐（時刻＋潮位）** | **気象庁 潮位表（下田／石廊崎）** | 公式・年間まとめて取得可・完全無料 |
| **黒潮位置・水温場** | **JAMSTEC 黒潮親潮ウォッチ（画像）＋Copernicus Marine GLORYS12（数値）** | 前者は流路タイプの週次判断に、後者は暖水舌の格子データ取得に |

以下、各カテゴリ別に詳細をまとめます。

---

## 1. 海流（Ocean Currents）

### 1-1. Open-Meteo Marine API ★推奨（無料・簡単）

- URL: https://open-meteo.com/en/docs/marine-weather-api
- 提供元: Open-Meteo（DWD, ECMWF, MeteoFrance 系モデルを統合）
- **提供変数**: `ocean_current_velocity`（速さ km/h / knots）、`ocean_current_direction`（0=北向き、90=東向き）— 潮汐込み（Eulerian + Waves + Tides）
- **空間解像度**: 0.08度（約8km） — 神子元単一格子でカバー
- **時間解像度**: hourly、15分単位の current conditions
- **予測範囲**: デフォルト7日、最大16日先
- **API 形式**: GETリクエスト、JSON レスポンス、認証不要（非商用）
- **リクエスト例**:
  ```
  GET https://marine-api.open-meteo.com/v1/marine?
     latitude=34.56&longitude=138.94
     &hourly=ocean_current_velocity,ocean_current_direction,sea_surface_temperature,
             wave_height,wave_period,sea_level_height_msl
     &forecast_days=14
     &timezone=Asia%2FTokyo
  ```
- **注意**: 沿岸域は「limited accuracy」の但し書きあり。神子元は絶海の孤島型なので精度期待は中〜高。
- **商用ライセンス**: 商用利用時は要有料契約（customer- プレフィックス）

### 1-2. Copernicus Marine GLORYS12 / SMOC ★精度向上用（無料・要登録）

- URL: https://data.marine.copernicus.eu/product/GLOBAL_ANALYSISFORECAST_PHY_001_024/description
- 提供元: EU Copernicus Marine Service（MeteoFrance）
- **Open-Meteo が背後で使っている元データそのもの**。直接叩けば潮汐と Eulerian 成分を分離できる
- **提供変数**: 三次元流速（u, v, w）、水温、塩分、海面高度
- **空間解像度**: 1/12度（約8km、全球）／1/36度は日本近海用の別プロダクトあり
- **時間解像度**: 3-hourly（潮汐込み SMOC は hourly）
- **API 形式**: `copernicusmarine` Python CLI／ERDDAP／DAP／NetCDF ダウンロード
- **アカウント**: 要無料登録（研究・非商用ならOK）

### 1-3. JCOPE-T (JAMSTEC) ★最高解像度・要問い合わせ

- URL: https://www.jamstec.go.jp/aplinfo/kowatch/
- 概要ページ: https://forecastocean.com/j/research.html
- **空間解像度**: **1/36度（約3km格子）** — 日本沿岸で最高クラス
- **時間解像度**: 1時間毎
- **提供変数**: 海底までの水温、塩分、潮流、水位（3D）
- **予測範囲**: 短期（JCOPE-T DA）は20日先、長期（JCOPE3M）は2ヶ月先
- **入手**: 公開の解説ページは画像のみ／数値データは JAMSTEC VENTURE（forecastocean.com）に法人問い合わせ必要
- **神子元用途**: 1/36度は神子元島を "islands as land-mask" として認識できる解像度なので、島陰渦流もある程度シミュレートされている

**推奨アプローチ**: MVPは Open-Meteo で立ち上げ、精度と商用性が問題になったら Copernicus Marine か JCOPE-T に移行する。

---

## 2. 海面水温（SST）

### 2-1. Open-Meteo Marine API（再掲）

- `sea_surface_temperature` 変数、単位℃、hourly
- Copernicus MeteoFrance SST が背後、6時間毎更新の 0.08度メッシュ
- **無料で最も手軽**

### 2-2. Himawari-9 SST（気象衛星ひまわり9号）★リアルタイム

- 気象庁の日別海面水温: https://www.data.jma.go.jp/kaiyou/data/db/kaikyo/daily/sst_HQ.html
- **空間解像度**: 高解像度版で 0.02度（約2km）
- **時間解像度**: 日次
- **提供**: GRIB/PNG/CSV。API公式提供はないが FTP でファイル取得可
- **利用**: 神子元格子の日次SST を数年分バックフィルするならこれ

### 2-3. GHRSST OSTIA（英国気象局）★過去データ

- URL: https://podaac.jpl.nasa.gov/dataset/UKMO-L4HRFND-GLOB-OSTIA
- **空間解像度**: 0.054度（約5km）／L4 統合プロダクト
- **時間解像度**: 日次
- **入手**: NOAA PO.DAAC、Copernicus Marine、UK Met Office FTP のいずれか
- **強み**: 1985年〜現在まで再解析済みで、**過去10年の神子元SST時系列を作れる** — GAMM学習の教師データ用にほぼ必須

### 2-4. お天気ナビゲータ 神子元 海水温グラフ ★お手軽

- URL: https://s.n-kishou.co.jp/w/charge/tide/otca/otca_graph?ba=22&code=sh4010
- **強み**: 神子元スポット固定で1日1回更新、30日先予測、気象庁の平年値と重ねて表示
- **弱み**: 有料（PROコース登録）、水深1mのみ、API提供なし（HTML）
- **用途**: ユーザーが目で確認する日次判断用

### 2-5. NASA MODIS-Aqua / MUR SST ★研究用

- URL: https://podaac.jpl.nasa.gov/dataset/MUR-JPL-L4-GLOB-v4.1
- **空間解像度**: 0.01度（約1km） — 神子元スケールに最適
- **時間解像度**: 日次
- **入手**: `rerddap` / `xtractomatic` Rパッケージ、または PO.DAAC 直接
- **Logan 2020 が使っていたのはこの MUR SST**

---

## 3. 潮汐（Tides）

### 3-1. 気象庁 潮位表 ★推奨（公式・完全無料）

- 潮位表トップ: https://www.data.jma.go.jp/kaiyou/db/tide/suisan/index.php
- 東海地方: https://www.data.jma.go.jp/kaiyou/db/tide/suisan/s_tokai.php
- **下田**（験潮所コード：**SM**）と **石廊崎**（近隣）が対象。神子元用途は下田で十分
- **提供内容**: 満潮／干潮の時刻と潮位（cm）、1年分の推算値をまとめて取得可能
- **形式**: PDF、テキスト（[フォーマット仕様](https://www.data.jma.go.jp/kaiyou/db/tide/suisan/readme.html)）
- **一括ダウンロード**: https://www.data.jma.go.jp/kaiyou/db/tide/sea_lev_var/index_download.php
- **精度**: 天文潮位（推算）— 実測は別（3-2）

**Python 実装例**：
- Qiita [気象庁潮位データを1年分まとめて読み込む](https://qiita.com/onodera/items/1e5378671f1ed182f811)
- GitHub [hydrocoast/JMAtide](https://github.com/hydrocoast/JMAtide) — 潮位・潮位偏差を CSV 出力するスクレイパ（既製で使える）

### 3-2. 気象庁 潮汐観測資料（実測）

- URL: https://www.data.jma.go.jp/kaiyou/db/tide/genbo/index.php
- **提供内容**: 全国験潮所の**実測潮位**（毎時 & 品質管理済み）
- **更新頻度**: 翌月20日頃に前月分が確定
- **用途**: 過去実績の再検証に。当日予測には推算値で十分

### 3-3. Open-Meteo Marine API `sea_level_height_msl`（再掲）

- Open-Meteo が返す潮位（tides を含む）— 気象庁とは基準面が違うので比較時は要注意
- **利便性**: 一つのAPI で潮位も海流も取れるので開発が楽

---

## 4. 黒潮・海況（Kuroshio Path & Circulation）

### 4-1. JAMSTEC 黒潮親潮ウォッチ ★予測の主軸

- URL: https://www.jamstec.go.jp/aplinfo/kowatch/
- **提供内容**:
  - **JCOPE-T DA 短期予測（20日先）**: 週2回更新、水温＋流速の画像
  - **JCOPE3M 長期予測（2か月先）**: 週1回更新、流路型（N/C/大蛇行）の解説
- **形式**: 解説記事＋画像（PNG）／数値は非公開
- **強み**: **本邦最高権威の黒潮予測**。神子元付近の暖水舌の予兆をここで読める
- **弱み**: API なし。画像＋テキスト解説を人間 or LLM で解釈する運用になる
- **想定運用**: 週1回、この予測記事を LLM に読ませて「今週の黒潮神子元寄り度」を 0-10 で数値化、モデルに feature として入れる

### 4-2. 海上保安庁 海洋速報 & 海流推測図 ★流路タイプの公式判断

- URL: https://www1.kaiho.mlit.go.jp/KANKYO/KAIYO/qboc/
- **提供内容**: 週2回発行の海流推測図（画像）、黒潮流路の N型／C型／大蛇行の分類、接岸地点リスト（潮岬・室戸岬・新島〜八丈島・房総半島など）
- **直近**: 2026-08-21 現在、黒潮は「都井岬」「足摺岬」「室戸岬」「潮岬」「新島から八丈島」「房総半島南東岸」に接近（＝**神子元は本流の内側にあり接岸型**）
- **形式**: 画像（PNG）、解説テキスト
- **API**: 公式APIなし。ページの HTML から接岸地点を LLM で抽出する運用

### 4-3. 神奈川県水産技術センター 一都三県漁海況速報 & 関東東海海況速報 ★週次数値化

- 一都三県: https://sui-kanagawa.jp/Kaikyozu/1to3ken/
- 関東東海: https://sui-kanagawa.jp/Kaikyozu/KantoTokaiIZ/
- **提供内容**: 相模湾・伊豆諸島海域の海況速報図（黒潮位置＋水温分布）
- **強み**: **神子元を含む海域に特化した**週次速報。伊豆エリアのローカル暖水パッチが図示されている
- **形式**: 画像（PNG）
- **API**: なし
- **想定運用**: 週次で最新図を LLM に読ませて「伊豆エリアの暖水舌の到達度」を feature 化

### 4-4. JAFIC 一都三県漁海況情報 ★会員制

- URL: https://www.jafic.or.jp/
- 一般社団法人 漁業情報サービスセンター
- **提供**: 会員向けの漁海況情報、衛星水温、船舶運航データの水温
- **API**: 会員限定
- **用途**: 商用化する場合の高精度データソース候補

### 4-5. Copernicus Marine SST + Currents（再掲）

- 黒潮の輸送量や流路を数値的に扱いたいなら Copernicus GLORYS12 の u,v 場をダウンロードして自前で「27℃等温線が神子元に到達しているか」を判定するのが一番確実
- 参照: https://data.marine.copernicus.eu/

---

## 5. 気象（Weather）

### 5-1. Open-Meteo Weather API ★推奨

- URL: https://open-meteo.com/en/docs
- **提供変数**: 風速・風向・気温・気圧・視程・降水量、hourly、16日先
- **形式**: JSON、認証不要（非商用）
- **神子元格子**: 34.56, 138.94 で単一リクエスト

### 5-2. 気象庁 予報・天気図

- 概況: https://www.jma.go.jp/bosai/forecast/
- 静岡県天気予報の下田・石廊崎地点：JSON で取得可 (`https://www.jma.go.jp/bosai/forecast/data/forecast/220000.json` を parse)

### 5-3. 波・海況（Marine Forecast）

- Open-Meteo Marine の `wave_height` / `wave_direction` / `wave_period`（1-4 でカバー済み）
- 補完: [お天気.com 神子元](https://www.otenki.com/0001_wind_wave_main.htm?chiten_code=22Z70065&lat=34.56&lon=138.94&city=219&subtitle=%90_%8Eq%8C%B3)、[釣り天気.jp 神子元](https://www.tsuritenki.jp/tenki/8774/wave?genre=2)
- **注意**: **神子元は海況が悪いと欠航** — 波高 2m 以上 or 風速 12m/s 以上でショップが欠航判断する現地基準あり

---

## 6. 現地目撃データ（Ground Truth）★モデル学習の核

### 6-1. 神子元マリンサービス ダイブログ

- URL: https://www.mikomoto.com/divelog/
- **更新**: 潜航日ごとに1本、写真＋テキスト
- **抽出できる情報**: 潮の向き・強さ（上げ/下げ、順潮/逆潮）、水温、透明度、ハンマー目撃有無・規模・場所（Aポイント／中層／カメ根／ジャブ根）、南方系魚類の目撃
- **取得**: HTMLスクレイピング → LLM構造化抽出（`gsk task create docs` や独自 GPT prompt）

### 6-2. 神子元ハンマーズ ダイブログ

- URL: https://mikomotodivers.com/logs/
- 上と同じ構造、独立ソース。両方取ってアンサンブルすると1日あたり2〜4本のデータポイントが取れる

### 6-3. 海遊社 神子元ブログ

- URL: https://www.290.jp/archives/category/mikomoto （潮の状況を数値レベル：「秒速2〜3m」など、で書いてくれることがあり価値高）

### 6-4. Sea Guide / Sunla / 潜楽屋 など他ショップ

- 神子元ダイビング協議会加盟の4ショップ以外にも、都心の複数ショップが日々ログを出している。網羅すれば1日10本以上取れる可能性

**推奨実装**:
1. 対象ショップのブログRSSまたはHTMLを日次でスクレイプ
2. LLM で以下のスキーマに構造化：
   ```json
   {
     "date": "2026-08-23",
     "shop": "mikomoto_hammers",
     "water_temp": 26,
     "tide_direction": "down",  // up | down | slack
     "current_strength": "moderate",  // weak | moderate | strong | very_strong
     "visibility_m": 15,
     "hammer_observed": true,
     "hammer_location": "A_point",  // A_point | kame_ne | jab_ne | mid_water | other
     "hammer_school_size": "50-100",
     "tropical_species": ["dorado", "tsumburi"],
     "notable_events": ["down_current", "kuroshio_hit"]
   }
   ```
3. 数年分ためて GAMM で環境変数と回帰

---

## 7. 海底地形・島形状（Bathymetry & Coastline）

### 7-1. 海上保安庁 海底地形デジタルデータ / M7000 シリーズ

- 有償だが神子元島周辺の詳細海底地形図を取得可能
- **用途**: 潮陰ジオメトリの精度向上（前回設計 §5-5）

### 7-2. GEBCO 全球海底地形 ★無料

- URL: https://www.gebco.net/
- **空間解像度**: 15秒（約450m）
- 神子元単体の海底構造を出すには粗いが、島輪郭は取れる

### 7-3. OpenStreetMap 海岸線

- 神子元島の陸地ポリゴンは OSM から取得可能（無料）
- Overpass API: `way["natural"="coastline"](34.55,138.93,34.58,138.96);`

---

## 8. 実装優先度まとめ（MVP → V2 → V3）

| Tier | データソース | 使い所 | 実装コスト |
|---|---|---|---|
| **MVP** | Open-Meteo Marine API | 海流・SST・波・潮位 (all-in-one) | 1日 |
| **MVP** | 気象庁 潮位表（テキスト直リンク or hydrocoast/JMAtide） | 満干潮時刻＋潮位、下田基準 | 1日 |
| **MVP** | JAMSTEC 黒潮親潮ウォッチの週次記事を LLM 読解 | 週次の暖水到達度スコア | 半日 |
| **MVP** | 神子元マリンサービス／ハンマーズのブログ日次スクレイプ | 教師データ蓄積開始 | 1〜2日 |
| **V2** | JCOPE-T DA（要問い合わせ）or Copernicus GLORYS12 | 深度別水温 → 冷水塊検出 | 数日〜数週 |
| **V2** | 神奈川県水産技術センター 海況図の週次読解 | 伊豆エリア暖水舌 | 半日 |
| **V2** | GHRSST OSTIA 過去10年 | 過去SST再学習用 | 1週 |
| **V3** | JAFIC 会員データ（漁船水温リアルタイム） | 商用向け高精度化 | 契約次第 |
| **V3** | 海上保安庁 海底地形詳細 | 潮陰ジオメトリ本格化 | 数週 |

---

## 9. 認証・利用規約・料金早見表

| ソース | 認証 | 料金 | 商用可否 |
|---|---|---|---|
| Open-Meteo | 不要 | 無料（非商用） | 有料契約で商用可 |
| Copernicus Marine | 要無料登録 | 無料 | 商用可（要 attribution） |
| JCOPE-T (JAMSTEC VENTURE) | 要問い合わせ | 有料 | 商用可（契約次第） |
| 気象庁 潮位表 | 不要 | 無料 | **商用可**（出典明記） |
| 気象庁 SST | 不要 | 無料 | 商用可（出典明記） |
| GHRSST OSTIA / NASA MUR | 不要（またはEarthdata登録） | 無料 | 商用可 |
| 海上保安庁 海洋速報 | 不要 | 無料 | 商用可（出典明記） |
| 神奈川県 海況図 | 不要 | 無料 | 商用要問い合わせ |
| お天気ナビゲータ | 要有料登録 | PROコース有料 | 個人利用のみ |
| JAFIC | 要会員 | 有料 | 会員規約に従う |
| ダイビングショップ・ブログ | 不要（公開情報） | 無料 | **商用利用は要各ショップの許可**（構造化データを再配布する形なら明確な合意が必要） |

**特に注意**：**ダイビングショップさんのブログを学習データにする場合は、事前に相談・許可**を得るのが強く推奨。将来公開ツール化するなら、対価的なもの（例：協賛ショップとして予約リンクを載せる）を用意しておくとスムーズです。

---

## 10. サンプル：Open-Meteo でMVPの1日分データを取る

```python
import requests
import json

def get_mikomoto_daily(date=None, days=7):
    r = requests.get(
        "https://marine-api.open-meteo.com/v1/marine",
        params={
            "latitude": 34.56,
            "longitude": 138.94,
            "hourly": ",".join([
                "ocean_current_velocity",
                "ocean_current_direction",
                "sea_surface_temperature",
                "wave_height",
                "wave_period",
                "sea_level_height_msl",
            ]),
            "forecast_days": days,
            "timezone": "Asia/Tokyo",
            "cell_selection": "sea",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

data = get_mikomoto_daily(days=7)
# data["hourly"]["time"], ["ocean_current_velocity"], etc.
```

これだけで、神子元における今後7日間の 168時間分の海流・SST・波・潮位が JSON で取れます。**MVPを動かすには、この1本のAPIで足りる**ということです。
