#!/bin/bash
# 天気図VLM解説の毎日更新（Mac の launchd から実行）。
# daily_update.py を実行し、webui/history の変更を commit・push する（GitHub Pages が自動更新される）。
# 当日分（JST）が既に保存済みなら何もしない（リトライ時刻・手動再実行の重複防止）。
set -euo pipefail

cd "$(dirname "$0")"
PY=/opt/anaconda3/envs/met_env/bin/python
TODAY=$(TZ=Asia/Tokyo date +%Y-%m-%d)

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
  git push -q
  echo "push 完了"
fi
