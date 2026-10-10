# weather_chart_vlm（天気図VLM解説）運用マニュアル

気象庁の天気図（実況・予想24h/48h先）・ひまわり赤外・アメダス・府県予報概況をVLM（Claude Sonnet 5）に読ませ、気圧配置の判定と平文解説を毎日自動生成するプロジェクト。
公開: <https://masauehr.github.io/weather_chart_vlm/webui/index.html>（GitHub Pages、直近10日分の履歴を切替表示）

## 解説の生成の仕組み（局地的な雨域の拾い方）
`daily_update.py` は次の入力を1回の API 呼び出しで VLM に渡す。

| 入力 | 内容 |
|---|---|
| 地上天気図 | 実況2枚（アジア太平洋・日本近海）＋予想4枚（24h/48h先） |
| 衛星赤外 | 広域 `ir_japan.png`（z=4）＋九州〜南西諸島の拡大 `ir_nansei.png`（z=5・2倍拡大、海岸線・緯経度5度格子つき） |
| アメダス | 主要8地点＋**全国約1300地点の地域別集計** |
| 府県予報概況 | 東京都・新潟県・石川県・北海道(石狩) |

**気圧の谷の判読手順**（プロンプトで指示）: ①等圧線の凹みを探す → ②拡大赤外の雲域と位置で照合 → ③アメダス地域別集計の降水あり地点数で裏取り → ④一致すれば地域名つきで雨域を解説、片方だけなら断定せず `cloud_match=partial`。結果は `troughs` に入り、WebUI の根拠欄に表示される。

### アメダス地域別集計（`amedas.json`）
| キー | 内容 |
|---|---|
| `主要地点` | 従来の8地点（札幌・新潟・金沢・東京・名古屋・大阪・福岡・那覇）の気温・風・降水・気圧 |
| `全国` | 地点数・降水あり地点数 |
| `地域別` | 地域ごとの地点数・降水あり地点数・最大1時間降水量（地点名つき）・最大風速 |
| `降水上位` | 1時間降水量の上位10地点 |

地域区分は観測所番号の上2桁（府県ブロック）で決め、島しょ部は緯度で補正する: 北海道／東北／関東／甲信／東海／北陸／近畿／中国／四国／九州北部／九州南部／鹿児島(本土・大隅)／奄美・トカラ（鹿児島で北緯30度未満）／沖縄／伊豆・小笠原。地点を足す・区分を変える場合は `fetch_chart.py` の `region_of` と `BLOCK_REGIONS`、`validate.py` の `REGION_KEYWORDS` を合わせて直す。追加の HTTP リクエストは地点表 `amedas/const/amedastable.json` の1件のみ。

### ガードレール（`validate.py`）の主な基準
- 実況突合: 降水は全国の最大1時間降水量、風は全国最大風速が12m/s未満で weak（離島・山岳を含むため主要8地点時の8m/sより緩い）。
- 数値引用: 解説中の数値はアメダス集計・予報概況に実在すること（四捨五入・切り捨ての引用は許容）。
- 雨域の言及漏れ: 降水あり3地点以上かつ最大1mm以上の地域が解説・根拠に出てこなければ「注意」。

## 毎日の自動更新（Mac の launchd）
- 設定: `~/Library/LaunchAgents/com.user.weather_chart_vlm.plist`（Label: `com.user.weather_chart_vlm`）
- 実行時刻: **毎朝 8:45 JST、リトライ 9:15 JST**（Mac のローカル時刻）。
- 処理: `daily_update_local.sh` → `git pull --rebase` → `daily_update.py`（取得・VLM判定・ガードレール検証・`webui/history/<日付>/` 保存、10日超は削除）→ commit → push → GitHub Pages 自動再ビルド。
- 当日（JST）分が `webui/history/<日付>/data.json` にあればスキップ（リトライ・手動再実行の重複防止）。
- コスト: Claude API 約 $0.02〜0.04/日。`ANTHROPIC_API_KEY` は `.env`。
- ログ: `launchd.log`（git除外）。
- 手動実行: `bash daily_update_local.sh`。
- 注意: Mac がスリープ中／電源オフだと実行されない（スリープ復帰後に launchd が実行する場合あり）。
- 再登録: `launchctl bootout gui/$(id -u)/com.user.weather_chart_vlm` → `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.weather_chart_vlm.plist`

## GitHub Actions（手動実行用に残置）
- `.github/workflows/daily-update.yml` は `workflow_dispatch` のみ。`gh workflow run daily-update.yml` で実行（Secrets の `ANTHROPIC_API_KEY` が必要）。

## 変更履歴
- 2026-10-10: 局地的な雨域（気圧の谷に伴う南西諸島付近の雨など）が解説から抜ける問題に対応。
  - 原因: アメダスが主要8地点（札幌・新潟・金沢・東京・名古屋・大阪・福岡・那覇）のみで鹿児島〜奄美の雨を捉えられず、プロンプトにも谷の判読手順が無かった。
  - アメダス: 全国約1300地点を地域別（北海道〜沖縄、鹿児島・奄美は分割）に集計して渡す（降水あり地点数・最大1時間降水量・最大風速・降水上位10地点）。`amedas.json` は `主要地点`／`全国`／`地域別`／`降水上位` の構造。追加リクエストなし（既存の全国マップ＋`amedastable.json`）。
  - 衛星: 九州〜南西諸島の拡大赤外 `ir_nansei.png`（z=5・2倍拡大・海岸線と緯経度格子つき）を追加。
  - プロンプト: 等圧線の凹み→衛星の雲域→アメダス降水の順で照合し、スキーマに `troughs` を追加。局地的な雨域の省略を禁止。
  - ガードレール: 実況突合を全国集計に変更（風は12m/s未満を weak）、数値引用は四捨五入を許容、降水あり3地点以上の地域が解説に出ない場合「注意」（雨域の言及漏れ）。
  - WebUI: 根拠欄に気圧の谷の判読結果を追記。
- 2026-10-10: 解説ページの見出し「明日」を「明日から明後日」に変更（解説が明後日までの見通しを含むため。データのキー名 `tomorrow` は変更なし）。
- 2026-10-10: ページ冒頭に「AIが天気図・衛星画像を自動収集して解説を自動生成する試みであり、誤りを含む場合がある」旨の注記を追加（webui/index.html・style.css）。Bluesky 自動投稿（OpenClaw cron `bsky-weather-am`）のリンク先として公開されるため。
- 2026-10-01: GitHub Actions の定期実行を廃止し、Mac の launchd（8:45 JST）へ移行。
  - 理由: GitHub の `schedule` が新規リポジトリで遅延・欠落（9/29 欠落、9/30 約3時間遅れ、10/1 の 6:13 分は未実行、臨時 9:05 も未実行）。
  - 時刻設定（UTC換算）の誤りではなく GitHub 側の仕様（高負荷時の遅延・取りこぼし、新規リポジトリの低優先度）が原因。

## トラブルシュート
- 更新されない: `launchd.log` を確認 → `launchctl list | grep weather_chart_vlm` → `bash daily_update_local.sh` で手動実行。
- push 失敗: git の認証状態、`git pull --rebase` の競合を確認。

## 運用上の注意・トラブルシュート（雨域まわり）
- 「雨があるのに全国的に晴れ」と書かれた: 当日の `data/<基準時刻>/amedas.json` の `地域別` で降水あり地点数を確認 → `webui/history/<日付>/data.json` の `guardrail_checks`（「雨域の言及」が注意になっていないか）を確認。
- 谷の判読（`troughs`）はVLMの画像読み取りで、実行ごとにぶれる（`cloud_match` が partial/yes で揺れる）。雨の事実は地域別集計が担保する。
- `ir_nansei.png` が無い／タイル取得失敗: `fetch_chart.py` の `fetch_ir_zoom`（z=5、x=27,28／y=12,13）。衛星タイルは全球 `fd` を使う。
- 過去日の `amedas.json` は旧形式（8地点のみ）の場合がある。`validate.py` は両形式を読める。
- 入力トークンは約11,000/回（拡大赤外・地域集計の追加で従来比 約+4,000）。
