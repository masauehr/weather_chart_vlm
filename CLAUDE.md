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
