# weather_chart_vlm（天気図VLM解説）運用マニュアル

気象庁の天気図・ひまわり赤外・アメダス・府県予報概況をVLM（Claude Sonnet 5）に読ませ、気圧配置の判定と平文解説を毎日自動生成するプロジェクト。
公開: <https://masauehr.github.io/weather_chart_vlm/webui/index.html>（GitHub Pages、直近10日分の履歴を切替表示）

## 毎日の自動更新（GitHub Actions）
- ワークフロー: `.github/workflows/daily-update.yml` → `daily_update.py` を実行し `webui/history/<日付>/` に保存。10日より古い履歴は自動削除。
- 実行時刻: **毎朝 8:45 JST（23:45 UTC）**。2026-10-01 に 6:13 JST から変更。
- コスト: Claude API 約 $0.02〜0.04/日。`ANTHROPIC_API_KEY` はリポジトリ Secrets。
- 手動実行: `gh workflow run daily-update.yml`（`workflow_dispatch`）。

## 変更履歴
- 2026-10-01: 6:13 JST の定時実行が走らなかったため 8:45 JST に変更。あわせて 2026-10-01 9:05 JST（`5 0 1 10 *`）の臨時実行を1回追加（確認後に削除可）。

## トラブルシュート
- **スケジュールが走らない／遅れる**: GitHub Actions の `schedule` は混雑で数分〜数時間遅延、または欠落することがある（9/29 朝は欠落、9/30 は約3時間遅れで 09:22 JST に実行）。
  - 確認: `gh run list --workflow daily-update.yml`
  - 対処: `gh workflow run daily-update.yml` で手動実行。毎時0分ちょうどは避ける。
- 当日分の更新が無い場合は `webui/history/index.json` に当日の日付があるかを確認。
