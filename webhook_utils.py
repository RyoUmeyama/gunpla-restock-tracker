#!/usr/bin/env python3
"""
Webhook通知ユーティリティ（Discord / Slack 両対応）

メール通知とは別チャンネルで、在庫復活を push 通知する。
メール全体の通知をオフにしていても、Discord/Slackアプリのプッシュ通知で
（Apple Watch含め）確実に気づけるようにするのが目的。

環境変数 WEBHOOK_URL を見て自動判別する:
  - discord.com / discordapp.com を含む → Discord 形式 ({"content": ...})
  - hooks.slack.com を含む            → Slack 形式 ({"text": ...})
  - それ以外                          → Slack互換 ({"text": ...}) として送る
"""

import json
import time

import requests


def _is_discord(url):
    return "discord.com" in url or "discordapp.com" in url


def send_webhook(webhook_url, title, lines, timeout=15):
    """
    Webhookにメッセージを送る。

    Args:
        webhook_url: Discord/Slack の Incoming Webhook URL
        title: 見出し（1行目に太字で出す）
        lines: 本文の行リスト（商品名・URLなど）
    Returns:
        bool: 送信成功なら True
    """
    if not webhook_url:
        return False

    body = title + "\n" + "\n".join(lines)

    if _is_discord(webhook_url):
        # Discordは2000文字制限。差分つき通知で本文が伸びると従来は1900字で
        # ぶった切られていた（URL途中欠け）ため、行境界で分割して複数メッセージで送る。
        chunks = _split_chunks(body, 1900, max_chunks=3)
        ok = False
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(0.5)  # Discordのレート制限(5req/2s)に配慮
            sent = _post(webhook_url, {"content": chunk}, timeout)
            ok = ok or sent
        return ok
    # Slack（および互換）
    return _post(webhook_url, {"text": body}, timeout)


def _split_chunks(body, limit, max_chunks=3):
    """本文を行境界で limit 文字以内のチャンクに分割する。1行が limit を超える場合は行内で切る。
    max_chunks を超える分は末尾を省略する（通知の洪水防止）。"""
    chunks, cur = [], ""
    for line in body.split("\n"):
        while len(line) > limit:  # 超長行は行内で分割
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = line if not cur else cur + "\n" + line
    if cur:
        chunks.append(cur)
    if len(chunks) > max_chunks:
        chunks = chunks[:max_chunks]
        chunks[-1] = chunks[-1][: limit - 20] + "\n…（以下省略）"
    return chunks


def _post(webhook_url, payload, timeout):
    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        # Discordは204、Slackは200を返す
        if resp.status_code in (200, 204):
            print("✓ Webhook通知 送信成功")
            return True
        print(f"⚠ Webhook通知 失敗: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"✗ Webhook通知 送信エラー: {e}")
        return False
