# CLAUDE.md — weather_chart_vlm

## このプロジェクトの位置づけ
気象庁の天気図・衛星画像をVLM（画像対応LLM）に読ませ、気圧配置を判定して平文で解説する。
[weather_hackathon_ideas](https://github.com/masauehr/weather_hackathon_ideas) のアイデア ID-32 として検証しGOだったため独立。

## 🔑 データ利用の絶対ルール
- **気象データは気象庁HPから取得する。** 過去データは「気象庁｜過去の気象データ・ダウンロード」
  <https://www.data.jma.go.jp/risk/obsdl/index.php>、実況・予報・天気図・衛星は気象庁 bosai。
  Open-Meteo / ERA5 など再解析・第三者APIは使わない。
- **個人データ・非公開データは使わない。**
- GRIB（数値予報GPV）は容量のため扱わない。
- 海岸線オーバーレイは Natural Earth（パブリックドメイン）を使用。他の地図・境界データを追加する場合も
  ライセンスが明確なもの（パブリックドメイン or 出典明記で再配布可）のみ使う。

## 🌬️ 風の扱い（ルール）
- **風の強弱は平均風速（10分間の平均）で判断・記述する。** 最大瞬間風速（突風）で強弱を分類しない。瞬間風速に触れるときは「瞬間風速」と明記する。
- 気象庁の「風の強さと吹き方」の表（平均風速）に従う: やや強い風=10以上15未満m/s、強い風=15以上20未満、非常に強い風=20以上30未満、猛烈な風=30以上（10m/s未満に予報用語はない）。<https://www.jma.go.jp/jma/kishou/know/yougo_hp/kazehyo.html>
- 雨は「雨の強さと降り方」の表（1時間雨量）に従う: やや強い雨=10以上20未満mm、強い雨=20以上30未満、激しい雨=30以上50未満、非常に激しい雨=50以上80未満、猛烈な雨=80以上。<https://www.jma.go.jp/jma/kishou/know/yougo_hp/amehyo.html>
- アメダスの `wind` は平均風速。プロンプト・ガードレール（`validate.py`）・解説文の表現はこのルールに合わせる。

## 作業ルール
- ドキュメントは日本語。Markdown。コードコメントも日本語。
- 検証コードはリポジトリ直下、生データ・出力画像は `data/`（git 除外）。結果図・集計は `webui/history/`（git 追跡）。
- Python は met_env 相当（3.11、requests / Pillow / anthropic / python-dotenv / pdfplumber / pdf2image）。
- Web UI は Vanilla JS / HTML / CSS（フレームワーク不使用）。
- 気象庁API・「日々の天気図」・Natural Earth の利用規約を遵守。図表・画像には出典を明記。

## 毎日の自動更新（Mac の launchd）
- `~/Library/LaunchAgents/com.user.weather_chart_vlm.plist` が毎朝8:45 JST（リトライ9:15）に `daily_update_local.sh` を実行（`daily_update.py` → commit → push）。当日分が保存済みならスキップ。ログは `launchd.log`（git除外）。
- GitHub Actions の `schedule` は遅延・欠落が多いため2026-10-01に廃止。`.github/workflows/daily-update.yml` は手動実行（`workflow_dispatch`）用に残している。
- `ANTHROPIC_API_KEY` は `.env`（`.env` の内容は読まない・表示しない・コミットしない）。GitHub Secrets は手動実行時のみ使用。
- 実行結果は `webui/history/<日付>/` に保存、10日より古い履歴は自動削除。GitHub Pages（root配信）で公開。
- ワークフローやスケジュールを変更したら、README.md の該当節も更新する。

## 🔒 セキュリティ
- `.env`, `*.pem`, `*.key` の内容を読み込まない・表示しない・編集しない。
- APIキーはコード・コミット・ログに残さない。

## GitHub更新ルール
- ファイルを変更・追加した場合は、必ず `git add` → `git commit` → `git push` まで行う。
- push前にユーザーの確認を求める（破壊的操作の場合は特に）。
