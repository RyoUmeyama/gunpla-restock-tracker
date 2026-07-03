#!/bin/bash
# ローカル実行のセットアップ（1回だけ実行）
#   1. Python仮想環境(.venv)を作成し依存を入れる
#   2. .env が無ければ雛形をコピー（値は自分で埋める）
#   3. launchd エージェント（10分おき実行）を登録
#
# 使い方:  bash local/setup_local.sh
# 解除:    launchctl bootout gui/$(id -u)/com.resale.gunpla-restock

set -eu
cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"

echo "== 1/3 Python仮想環境 =="
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
echo "OK: .venv"

echo "== 2/3 .env =="
if [ ! -f .env ]; then
  cp local/env.example .env
  echo "⚠ .env を作成しました。エディタで開いて SMTP_*/RECIPIENT_EMAIL/WEBHOOK_URL を埋めてください。"
  echo "  （GitHub Secrets に登録してあるものと同じ値でOK）"
else
  echo "OK: .env は既にあります"
fi

echo "== 3/3 launchd 登録（10分おき実行）=="
PLIST_DST="$HOME/Library/LaunchAgents/com.resale.gunpla-restock.plist"
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__REPO_DIR__|$REPO_DIR|g" local/com.resale.gunpla-restock.plist.template > "$PLIST_DST"
launchctl bootout "gui/$(id -u)/com.resale.gunpla-restock" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "OK: 登録完了。10分おきに実行されます（ログ: local_run.log）"
echo ""
echo "動作確認:  bash local/run_local.sh && tail -20 local_run.log"
