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
        # Discordは2000文字制限。content にまとめて送る。
        payload = {"content": body[:1900]}
    else:
        # Slack（および互換）
        payload = {"text": body}

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


def send_ntfy(topic, title, lines, urgent=False, timeout=15):
    """ntfy.sh にプッシュ通知を送る（アカウント登録不要の無料プッシュ）。

    スマホに ntfy アプリを入れ、topic（推測されにくいランダム文字列）を購読しておくと、
    メール/Discordを開かなくてもロック画面に即プッシュされる。
    urgent=True（在庫検知）は最優先プッシュ（サイレントモードでも鳴る端末設定が可能）。

    Args:
        topic: ntfy のトピック名（環境変数 NTFY_TOPIC）
        title: 見出し（日本語不可のヘッダのため本文先頭に入れる）
        lines: 本文の行リスト
        urgent: True なら priority=urgent（在庫検知用）
    Returns:
        bool: 送信成功なら True
    """
    if not topic:
        return False
    body = title + "\n" + "\n".join(lines)
    headers = {
        # HTTPヘッダはlatin-1制約があるため日本語タイトルは本文側に置き、ヘッダは固定文字列にする
        "Title": "Restock Alert" if urgent else "Restock Info",
        "Priority": "urgent" if urgent else "default",
        "Tags": "rotating_light" if urgent else "mega",
    }
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code == 200:
            print("✓ ntfyプッシュ 送信成功")
            return True
        print(f"⚠ ntfyプッシュ 失敗: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"✗ ntfyプッシュ 送信エラー: {e}")
        return False
