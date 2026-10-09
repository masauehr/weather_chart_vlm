# weather_chart_vlm（天気図VLM解説）運用マニュアル

気象庁の天気図（実況・予想24h/48h先）・ひまわり赤外・アメダス・府県予報概況をVLM（Claude Sonnet 5）に読ませ、気圧配置の判定と平文解説を毎日自動生成するプロジェクト。
公開: <https://masauehr.github.io/weather_chart_vlm/webui/index.html>（GitHub Pages、直近10日分の履歴を切替表示）

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
- 2026-10-10: 解説ページの見出し「明日」を「明日から明後日」に変更（解説が明後日までの見通しを含むため。データのキー名 `tomorrow` は変更なし）。
- 2026-10-10: ページ冒頭に「AIが天気図・衛星画像を自動収集して解説を自動生成する試みであり、誤りを含む場合がある」旨の注記を追加（webui/index.html・style.css）。Bluesky 自動投稿（OpenClaw cron `bsky-weather-am`）のリンク先として公開されるため。
- 2026-10-01: GitHub Actions の定期実行を廃止し、Mac の launchd（8:45 JST）へ移行。
  - 理由: GitHub の `schedule` が新規リポジトリで遅延・欠落（9/29 欠落、9/30 約3時間遅れ、10/1 の 6:13 分は未実行、臨時 9:05 も未実行）。
  - 時刻設定（UTC換算）の誤りではなく GitHub 側の仕様（高負荷時の遅延・取りこぼし、新規リポジトリの低優先度）が原因。

## トラブルシュート
- 更新されない: `launchd.log` を確認 → `launchctl list | grep weather_chart_vlm` → `bash daily_update_local.sh` で手動実行。
- push 失敗: git の認証状態、`git pull --rebase` の競合を確認。
