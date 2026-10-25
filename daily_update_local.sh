#!/bin/bash
# 天気図VLM解説の毎日更新（Mac の launchd から実行）。
# daily_update.py を実行し、webui/history の変更を commit・push する（GitHub Pages が自動更新される）。
# 当日分（JST）が既に保存済みなら何もしない（リトライ時刻・手動再実行の重複防止）。
set -euo pipefail

cd "$(dirname "$0")"
PY=/opt/anaconda3/envs/met_env/bin/python
TODAY=$(TZ=Asia/Tokyo date +%Y-%m-%d)

# 失敗時は macOS 通知で知らせる（2026-10-25: git push が約31時間ハングし、以降の日次更新が全て止まった）
notify_fail() {
  osascript -e 'display notification "天気図VLMの日次更新が失敗しました（launchd.log を確認）" with title "weather_chart_vlm"' >/dev/null 2>&1 || true
}
trap notify_fail ERR

echo "=== $(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S JST') 開始 ==="

if [ -f "webui/history/$TODAY/data.json" ]; then
  echo "当日分($TODAY)は保存済み。スキップ"
  exit 0
fi

# 競合防止のため最新を取り込む（失敗しても続行しない）
git pull --rebase --autostash -q

$PY daily_update.py

git add webui/history
if git diff --cached --quiet; then
  echo "変更なし（スキップ）"
else
  git commit -q -m "デモ更新: $TODAY の天気図VLM判定（Mac実行）

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
  # 無応答でのハングを防ぐ: 転送が 30 秒間 1KB/s 未満なら中断し、最大 3 回まで再試行する
  for i in 1 2 3; do
    if git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 push -q; then break; fi
    if [ "$i" -eq 3 ]; then notify_fail; exit 1; fi
    echo "push 失敗。再試行 $i/3"; sleep 20
  done
  echo "push 完了"
fi
