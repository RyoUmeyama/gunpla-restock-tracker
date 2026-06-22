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
import hashlib

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
    if method == "spec_stock_msg":
        return _check_toei_spec_stock_msg(item)
    if method == "soldout_text":
        return _check_suruga_soldout_text(item)
    if method == "cart_button":
        return _check_cart_button(item)
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


def _check_toei_spec_stock_msg(item):
    """東映の旧movic系テンプレ（gDBS系）: stock_status JSONが無く spec_stock_msg で判定。
    × (&#215;)=在庫なし / ◎=在庫あり。補助として soldout.gif の有無も見る。"""
    try:
        html = fetch(item["url"], config.TOEI_ENCODING)
    except Exception as e:
        print(f"  ⚠ 取得失敗 {item['name']}: {e}")
        return False, False, ""

    # spec_stock_msg のセル内容を取る
    m = re.search(r'id="spec_stock_msg"[^>]*>\s*(.*?)\s*</', html, re.S)
    cell = m.group(1) if m else ""
    # 在庫なしマーカー: × (&#215; / ×) または soldout.gif
    sold = ("&#215;" in cell) or ("×" in cell) or ("soldout.gif" in html)
    has_cart = ("cart.gif" in html) or ("注文する" in html)
    # soldマーカーがなく、かつカート系があれば在庫あり
    in_stock = (not sold) and has_cart
    return in_stock, True, f"東映movic spec_stock_msg(sold={sold},cart={has_cart})"


def _check_suruga_soldout_text(item):
    """駿河屋の検索結果ページ: 商品ブロックの p.price が「品切れ」なら在庫なし、
    価格表示なら在庫あり。item['filter'] で対象商品ブロックを絞る（商品名/ID）。"""
    try:
        html = fetch(item["url"], config.DEFAULT_ENCODING)
    except Exception as e:
        print(f"  ⚠ 取得失敗 {item['name']}: {e}")
        return False, False, ""

    flt = item.get("filter", "")
    # item_box ブロックに分割し、filter に合致するブロックだけ判定
    blocks = re.split(r'class="item_?box', html)
    target_blocks = [b for b in blocks if (not flt) or (flt in b)]
    if not target_blocks:
        # フィルタに合致する商品が一覧に無い＝そもそも未掲載。判定不能扱い（誤通知防止）
        print(f"  ⚠ 対象商品が一覧に見つからず {item['name']}（filter='{flt}'）")
        return False, False, ""

    # 合致ブロックのどれかが「品切れ」でない（=価格表示で買える）なら在庫あり
    in_stock = False
    for b in target_blocks:
        # 価格ブロックを取り出す
        pm = re.search(r'class="price[^"]*"[^>]*>(.*?)</', b, re.S)
        price_txt = pm.group(1) if pm else b[:200]
        if "品切れ" not in price_txt and re.search(r"[0-9,]+\s*円|￥", price_txt):
            in_stock = True
            break
    return in_stock, True, f"駿河屋検索(対象{len(target_blocks)}件)"


def _check_cart_button(item):
    """カードラッシュ/コトブキヤ等のEC: 「カートに入れる」ボタンがあり soldout でなければ在庫あり。
    item['filter'] で対象商品ブロックを絞る。文字コードは item['encoding'] 優先。"""
    enc = item.get("encoding", config.DEFAULT_ENCODING)
    try:
        html = fetch(item["url"], enc)
    except Exception as e:
        print(f"  ⚠ 取得失敗 {item['name']}: {e}")
        return False, False, ""

    flt = item.get("filter", "")
    # filter があれば、その語の周辺（商品ブロック相当）に絞って判定する
    scope = html
    if flt and flt in html:
        idx = html.find(flt)
        scope = html[max(0, idx - 1500): idx + 1500]
    elif flt:
        print(f"  ⚠ 対象商品が見つからず {item['name']}（filter='{flt}'）")
        return False, False, ""

    sold = ("soldout" in scope) or ("売り切れ" in scope) or ("SOLD OUT" in scope) or ("品切" in scope)
    has_cart = ("カートに入れる" in scope) or ("カートに追加" in scope) or ("cartinput" in scope)
    in_stock = has_cart and not sold
    return in_stock, True, f"cart_button(cart={has_cart},sold={sold})"


def discover_from_rss():
    """nyuka-now のRSSフィードから、監視キーワードに合致する商品(入荷/再販)を発見する。
    返り値: (found: dict[link->{title,link}], ok: bool)。ok=False は全フィード取得失敗。
    ※規約配慮: 低頻度・キャッシュTTLで運用（FEED_URLSは最小限に絞る）。"""
    headers = {"User-Agent": config.USER_AGENT}
    found = {}
    any_ok = False
    for feed_url in config.FEED_URLS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            xml = resp.content.decode("utf-8", errors="replace")
            any_ok = True
            for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
                tm = re.search(r"<title>(.*?)</title>", block, re.S)
                lm = re.search(r"<link>(.*?)</link>", block, re.S)
                if not (tm and lm):
                    continue
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", tm.group(1)).strip()
                link = lm.group(1).strip()
                # 監視キーワードに合致するものだけ拾う（ポケカ別格＝ポケカ語は広く）
                if any(kw in title for kw in config.WATCH_KEYWORDS):
                    found[link] = {"title": title, "link": link}
            time.sleep(config.REQUEST_INTERVAL)
        except Exception as e:
            print(f"  ⚠ RSS取得失敗({feed_url[:40]}): {e}")
    return found, any_ok


def fetch_pokecard_new_products():
    """ポケカ公式の商品APIから現在の商品リストを取得する。
    resultAPI.php の4カテゴリ(expansion/construction/others/peripheral)を叩き、
    各商品を (productTitle, releaseDate) のキーで返す。
    返り値: (products: dict[key->info], ok: bool)。ok=False は全カテゴリ取得失敗。"""
    base = "https://www.pokemon-card.com/products/resultAPI.php"
    headers = {"User-Agent": config.USER_AGENT}
    products = {}
    any_ok = False
    for ptype in config.POKECARD_PRODUCT_TYPES:
        try:
            resp = requests.get(
                base, params={"productType": ptype, "page": "1"},
                headers=headers, timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            any_ok = True
            for p in data.get("products", []):
                title = (p.get("productTitle") or "").strip()
                rdate = (p.get("releaseDate") or "").strip()
                if not title:
                    continue
                key = f"{title}|{rdate}"
                products[key] = {
                    "title": title,
                    "releaseDate": rdate,
                    "price": (p.get("priceTxt") or "").strip(),
                    "link": p.get("link_detailPage") or "",
                    "type": ptype,
                }
            time.sleep(config.REQUEST_INTERVAL)
        except Exception as e:
            print(f"  ⚠ ポケカAPI取得失敗({ptype}): {e}")
    return products, any_ok


def compute_page_signature(item):
    """page_update方式: ページから再販関連の本文だけを抽出・正規化してハッシュを返す。
    広告・カウンタ等のノイズを避けるため、再販キーワードと日付を含む行に絞る。
    返り値: (signature: str|None, ok: bool)。ok=False は取得失敗。"""
    try:
        html = fetch(item["url"], config.DEFAULT_ENCODING)
    except Exception as e:
        print(f"  ⚠ 取得失敗 {item['name']}: {e}")
        return None, False

    # タグを除去してテキスト化
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&[a-z]+;", " ", text)

    # 再販関連 or 日付を含む行だけを抽出（ノイズ除去）。
    # date_re は年付き・スラッシュ・ドット型まで拡張（pokecazilla等のスラッシュ型対策）。
    date_re = re.compile(
        r"\d{1,2}月\s*\d{1,2}日|202\d/\d{1,2}/\d{1,2}|202\d\.\d{1,2}\.\d{1,2}"
    )
    # 揮発行: 「○月○日更新」「○時○分時点」「現在、」等を含む行は
    # 再販ゼロでも毎回変わる＝誤検知の元なので、行ごと除外する。
    volatile_line_re = re.compile(r"更新】|更新\)|時点|現在[、,]|最終更新|本日|今日")
    # 揮発トークン: 日付・時刻の数値そのものをハッシュから除去（行は残しつつ数値だけ消す）
    volatile_token_re = re.compile(
        r"【?\d{4}年\d{1,2}月\d{1,2}日.*?更新】?"
        r"|\d{1,2}時\d{1,2}分.*?時点"
        r"|\d{1,2}:\d{2}\s*時点"
    )

    picked = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if volatile_line_re.search(s):
            continue  # 揮発行はまるごと除外
        if any(kw in s for kw in config.PAGE_UPDATE_KEYWORDS) or date_re.search(s):
            s = volatile_token_re.sub("", s)  # 行内の揮発トークンも除去
            s = re.sub(r"\s+", " ", s).strip()
            if s:
                picked.append(s)

    # 正規化（重複除去・ソート）してハッシュ化。順序揺れに強くする。
    normalized = "\n".join(sorted(set(picked)))
    sig = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return sig, True


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


def run_discovery(prev, new_state, alerts):
    """Phase2: RSS発見器。フィードから監視キーワード合致の新規商品を発見し、
    既知(prev['discovered'])にない新規を通知＋状態に記録する。
    発見した商品は次サイクル以降 discovered として記録され、重複通知しない。"""
    found, ok = discover_from_rss()
    discovered = dict(prev.get("discovered", {}))  # link -> {title,link}
    if not ok:
        new_state["discovered"] = discovered  # 取得失敗は前回維持
        print("  RSS発見器: 判定不能（前回状態を維持）")
        return

    first_run = "discovered" not in prev
    fresh = [lk for lk in found if lk not in discovered]
    # 発見済みに統合（上限を超えたら古いものから落とす）
    for lk, info in found.items():
        discovered[lk] = info
    if len(discovered) > config.MAX_DISCOVERED_ITEMS:
        # dictは挿入順。古い順に削る
        for lk in list(discovered.keys())[: len(discovered) - config.MAX_DISCOVERED_ITEMS]:
            del discovered[lk]
    new_state["discovered"] = discovered

    if first_run:
        print(f"  RSS発見器: 初回・{len(found)}件を記録（通知なし）")
    elif fresh:
        names = "、".join(found[lk]["title"][:30] for lk in fresh[:5])
        print(f"  RSS発見器: 新規{len(fresh)}件発見🔔 ← 通知（{names}）")
        lines = [f"{found[lk]['title']} {found[lk]['link']}" for lk in fresh[:8]]
        # 発見通知用の疑似item
        disco_item = {"name": "新弾・再販を発見（RSS）", "url": config.FEED_URLS[0], "retail_price": 0}
        alerts.append((disco_item, "新規発見: " + " / ".join(lines)))
    else:
        print(f"  RSS発見器: 新規なし（既知{len(discovered)}件）")


def run_once():
    """在庫チェックを1パス実行し、在庫復活/告知更新があれば通知する。状態はファイルで永続化。"""
    prev = load_state()
    new_state = {}
    alerts = []  # [(item, detail)] 通知すべき変化

    # Phase2: RSS発見器で新弾・再販を自動キャッチ（固定リストを動的に補完）
    run_discovery(prev, new_state, alerts)

    for item in config.WATCH_ITEMS:
        key = item["key"]

        if item.get("method") == "pokecard_official_list":
            # ポケカ公式API: (title,releaseDate)セット差分で新商品を検知（初回は基準記録）
            products, ok = fetch_pokecard_new_products()
            if not ok:
                new_state[key] = prev.get(key, [])
                print(f"  {item['name']}: 判定不能（前回状態を維持）")
                continue
            cur_keys = sorted(products.keys())
            new_state[key] = cur_keys
            prev_keys = prev.get(key, None)
            if prev_keys is None:
                print(f"  {item['name']}: 初回・{len(cur_keys)}商品を記録（通知なし）")
            else:
                fresh = [k for k in cur_keys if k not in set(prev_keys)]
                if fresh:
                    names = "、".join(products[k]["title"] for k in fresh[:5])
                    print(f"  {item['name']}: 新商品{len(fresh)}件検知🔔 ← 通知（{names}）")
                    detail_lines = []
                    for k in fresh:
                        p = products[k]
                        detail_lines.append(f"{p['title']}（{p['releaseDate']} {p['price']}）{p['link']}")
                    alerts.append((item, "ポケカ新商品: " + " / ".join(detail_lines[:5])))
                else:
                    print(f"  {item['name']}: 新商品なし（{len(cur_keys)}商品）")
            continue

        if item.get("method") == "page_update":
            # 告知ページ: 前回ハッシュと変化したら通知（初回は基準値を保存のみ）
            sig, ok = compute_page_signature(item)
            time.sleep(config.REQUEST_INTERVAL)
            if not ok:
                new_state[key] = prev.get(key, "")  # 取得失敗は前回維持
                print(f"  {item['name']}: 判定不能（前回状態を維持）")
                continue
            new_state[key] = sig
            prev_sig = prev.get(key, "")
            if prev_sig == "":
                print(f"  {item['name']}: 初回・基準を記録（通知なし）")
            elif sig != prev_sig:
                print(f"  {item['name']}: 告知更新を検知🔔 ← 通知")
                alerts.append((item, "再販告知が更新されました（受付/予約/再販情報を確認）"))
            else:
                print(f"  {item['name']}: 更新なし")
            continue

        # 在庫系（gdb_soldout / toei_stock_status）
        in_stock, ok, detail = check_item(item)
        time.sleep(config.REQUEST_INTERVAL)
        if not ok:
            new_state[key] = prev.get(key, False)  # 取得失敗は前回維持（誤通知防止）
            print(f"  {item['name']}: 判定不能（前回状態を維持）")
            continue

        new_state[key] = in_stock
        first_seen = key not in prev  # 初回は通知抑制（基準記録のみ）
        was = prev.get(key, False)
        was = bool(was) if isinstance(was, bool) else False
        status = "在庫あり🟢" if in_stock else "在庫なし🔴"
        change = ""
        if first_seen:
            change = "  （初回・基準を記録）"
        elif in_stock and not was:
            change = f"  ← 復活！（{detail}）"
            alerts.append((item, detail))
        detail_note = f" [{detail}]" if detail else ""
        print(f"  {item['name']}: {status}{detail_note}{change}")

    save_state(new_state)

    if alerts:
        print(f"  🎉 通知すべき変化 {len(alerts)}件 → 通知")
        notify(alerts)
    return len(alerts)


def main():
    """ジョブ内ループ対応。
    GitHub Actions の cron は混雑時に間引かれる（10分→実測2時間）ため、
    1起動の中で LOOP_COUNT 回・LOOP_INTERVAL 秒おきにチェックして粘り、
    1起動あたりの監視カバー時間を広げる（争奪戦の見逃しを減らす）。
    """
    loop_count = int(os.environ.get("LOOP_COUNT", "1"))
    loop_interval = int(os.environ.get("LOOP_INTERVAL", "180"))  # 秒

    print(f"=== 転売検証 在庫チェック開始（{loop_count}回ループ・{loop_interval}秒間隔）===")
    total_restocked = 0
    for i in range(loop_count):
        if i > 0:
            time.sleep(loop_interval)
        print(f"--- パス {i + 1}/{loop_count} ---")
        total_restocked += run_once()
    if total_restocked == 0:
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
