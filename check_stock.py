#!/usr/bin/env python3
"""
転売検証 在庫チェッカー（小額モデル検証 Q1 用）

毎回:
  1. WATCH_ITEMS の各商品を、item["method"] に応じた方式で取得・在庫判定
     - gdb_soldout: GunplaDatabase（ガンプラ。soldout/「売切」で判定）
     - toei_stock_status: 東映アニメ公式（OP-16等。埋め込みJSONの stock_status で判定）
  2. 「前回=在庫なし → 今回=在庫あり」に変化した商品だけ通知（メール＋Discord）

新サイトを足す場合は _check_<method> 関数を追加し、config の method を増やす。
"""

import os
import re
import sys
import json
import time

import requests

import config
from email_utils import send_email_with_retry
from webhook_utils import send_webhook


def fetch(url, encoding):
    headers = {"User-Agent": config.USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content.decode(encoding, errors="replace")


def check_item(item):
    """
    1商品の在庫を判定。item["method"] で判定方式を振り分ける。
    返り値: (in_stock: bool, ok: bool, detail: str)
      ok=False は取得失敗（判定不能）。detail は通知用の補足（在庫店名や価格など）。
    """
    method = item.get("method")
    if method == "gdb_soldout":
        return _check_gdb_soldout(item)
    if method == "toei_stock_status":
        return _check_toei_stock_status(item)
    print(f"  ⚠ 未知のmethod {method}（{item['name']}）")
    return False, False, ""


def _check_gdb_soldout(item):
    """GunplaDatabase: shop_status_container ブロックの soldout/「売切」で判定。"""
    try:
        html = fetch(item["url"], config.DEFAULT_ENCODING)
    except Exception as e:
        print(f"  ⚠ 取得失敗 {item['name']}: {e}")
        return False, False, ""

    positions = [m.start() for m in re.finditer(config.GDB_SHOP_BLOCK_CLASS, html)]
    if not positions:
        print(f"  ⚠ 店舗ブロック検出できず {item['name']}（ページ構造変化の可能性）")
        return False, False, ""
    positions.append(len(html))

    in_stock_shops = []
    for i in range(len(positions) - 1):
        seg = html[positions[i] : positions[i + 1]]
        is_sold = (config.GDB_SOLDOUT_MARKER in seg) or ("売切" in seg) or ("売り切れ" in seg)
        m = re.search(
            r"(amazon|yodobashi|あみあみ|amiami|surugaya|駿河屋|rakuten|楽天|dmm|"
            r"プレミアムバンダイ|p-bandai|ホビーサーチ|hobbysearch)",
            seg,
            re.I,
        )
        shop = m.group(0) if m else "?"
        if not is_sold:
            in_stock_shops.append(shop)

    in_stock = len(in_stock_shops) > 0
    return in_stock, True, ", ".join(in_stock_shops)


def _check_toei_stock_status(item):
    """東映アニメ公式: 埋め込みJSONの stock_status の値で判定。
    stock_status が TOEI_INSTOCK_MEANS_NOT("0") 以外なら在庫あり。"""
    try:
        html = fetch(item["url"], config.TOEI_ENCODING)
    except Exception as e:
        print(f"  ⚠ 取得失敗 {item['name']}: {e}")
        return False, False, ""

    # &quot; エスケープされた stock_status を読む
    m = re.search(r"stock_status(?:&quot;|\")\s*:\s*(?:&quot;|\")?([0-9]+)", html)
    if not m:
        print(f"  ⚠ stock_status 検出できず {item['name']}（ページ構造変化の可能性）")
        return False, False, ""

    status = m.group(1)
    in_stock = status != config.TOEI_INSTOCK_MEANS_NOT
    return in_stock, True, f"東映公式 stock_status={status}"


def load_state():
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    print("=== 転売検証 在庫チェック開始 ===")
    prev = load_state()
    new_state = {}
    restocked = []

    for item in config.WATCH_ITEMS:
        in_stock, ok, detail = check_item(item)
        time.sleep(config.REQUEST_INTERVAL)
        key = item["key"]
        if not ok:
            new_state[key] = prev.get(key, False)  # 取得失敗は前回維持（誤通知防止）
            print(f"  {item['name']}: 判定不能（前回状態を維持）")
            continue

        new_state[key] = in_stock
        was = prev.get(key, False)
        status = "在庫あり🟢" if in_stock else "在庫なし🔴"
        change = ""
        if in_stock and not was:
            change = f"  ← 復活！（{detail}）"
            restocked.append((item, detail))
        detail_note = f" [{detail}]" if detail else ""
        print(f"  {item['name']}: {status}{detail_note}{change}")

    save_state(new_state)

    if restocked:
        print(f"\n🎉 在庫復活 {len(restocked)}件 → 通知")
        notify(restocked)
    else:
        print("\n在庫復活なし。通知しません。")
    print("=== 完了 ===")


def build_messages(restocked):
    """restocked: [(item, detail)] → (subject, text, html, webhook_title, webhook_lines)"""
    n = len(restocked)
    subject = f"🤖【在庫検知】転売検証 {n}件 在庫あり！（要・定価確認）"

    text_lines = ["狙いの商品の在庫を検知しました！", config.NOTE, ""]
    web_lines = [config.NOTE, ""]
    html_rows = []
    for item, detail in restocked:
        line = f"・{item['name']}（定価{item['retail_price']:,}円）{detail}"
        text_lines.append(line)
        text_lines.append(f"  {item['url']}")
        text_lines.append("")
        web_lines.append(line)
        web_lines.append(item["url"])
        html_rows.append(
            f'<li style="margin-bottom:10px;"><strong>{item["name"]}</strong>'
            f'（定価{item["retail_price"]:,}円）{detail}<br>'
            f'<a href="{item["url"]}">{item["url"]}</a></li>'
        )
    text = "\n".join(text_lines)
    html = (
        '<html><body style="font-family:sans-serif;">'
        '<h2 style="color:#c00;">🤖 転売検証 在庫検知！</h2>'
        f"<p>{config.NOTE}</p><ul>{''.join(html_rows)}</ul>"
        '<p style="color:#888;font-size:12px;">転売検証 在庫トラッカーより自動送信</p>'
        "</body></html>"
    )
    return subject, text, html, subject, web_lines


def notify(restocked):
    subject, text, html, web_title, web_lines = build_messages(restocked)
    mail_ok = _notify_email(subject, text, html)
    hook_ok = _notify_webhook(web_title, web_lines)
    if not mail_ok and not hook_ok:
        print("✗ メール・Webhookとも通知できませんでした")
        sys.exit(1)


def _notify_email(subject, text, html):
    server = os.environ.get("SMTP_SERVER")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USERNAME")
    pw = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("RECIPIENT_EMAIL")
    if not all([server, user, pw, to]):
        print("⚠ SMTP設定不足。メール送信スキップ。")
        return False
    try:
        send_email_with_retry(
            smtp_server=server, smtp_port=port, username=user, password=pw,
            from_email=user, to_email=to, subject=subject,
            text_content=text, html_content=html,
        )
        return True
    except Exception as e:
        print(f"✗ メール送信失敗: {e}")
        return False


def _notify_webhook(title, lines):
    url = os.environ.get("WEBHOOK_URL")
    if not url:
        print("（WEBHOOK_URL未設定。Webhook通知スキップ）")
        return False
    return send_webhook(url, title, lines)


if __name__ == "__main__":
    main()
