#!/bin/bash
# ローカル実行ランナー（launchd から10分おきに呼ばれる）
#
# GitHub Actions は (1)cron間引きで実測2〜4時間に1回 (2)nyuka-nowがクラウドIPを遮断
# (3)privateリポの無料枠が月2,000分 という3つの理由で主戦にできない。
# 常時起動しているMacからの実行なら、10分間隔・nyuka-now到達可・無料。
#
# 前提: setup_local.sh を一度実行して .venv と .env を用意しておくこと。

set -u
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  # .env が無いまま実行すると「検知はするが通知されず、状態だけ進んで通知機会を失う」
  # ため、通知設定が無い場合は実行自体を拒否する。
  echo "[$(date '+%F %T')] .env がありません。local/setup_local.sh の手順で作成してください。実行中止。" >> local_run.log
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "[$(date '+%F %T')] .venv がありません。local/setup_local.sh を実行してください。実行中止。" >> local_run.log
  exit 1
fi

set -a
source .env
set +a

export LOOP_COUNT=1   # launchd 側が10分おきに起動するのでジョブ内ループは不要
echo "[$(date '+%F %T')] === local run ===" >> local_run.log
.venv/bin/python check_stock.py >> local_run.log 2>&1

# ログ肥大防止（末尾500KBだけ残す）
if [ "$(wc -c < local_run.log)" -gt 500000 ]; then
  tail -c 400000 local_run.log > local_run.log.tmp && mv local_run.log.tmp local_run.log
fi
