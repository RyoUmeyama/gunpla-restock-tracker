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
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

import config
from email_utils import send_email_with_retry
from webhook_utils import send_webhook


def _this_year():
    """現在の年（西暦）。ポケカ新弾の発売日フィルタを年経過で自動追従させるため。"""
    return datetime.now().year


# 同一パス内で接続不能（connectタイムアウト等）だったホスト。
# 例: nyuka-now.com は GitHub Actions のクラウドIPを遮断しており、20秒タイムアウト×11URL×4パス
# ＝1起動で約15分を浪費していた（Actions課金枠の主因）。初回失敗でホスト単位でスキップする。
_UNREACHABLE_HOSTS = set()


def http_get(url, **kwargs):
    """requests.get のラッパ。接続不能ホストはパス内で再試行せず即座に諦める。
    タイムアウト・UAは未指定なら既定値を補う。raise_for_status 済みの Response を返す。"""
    host = urlsplit(url).netloc
    if host in _UNREACHABLE_HOSTS:
        raise ConnectionError(f"{host} は接続不能（このパスではスキップ）")
    kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
    headers = kwargs.pop("headers", None) or {"User-Agent": config.USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, **kwargs)
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError):
        _UNREACHABLE_HOSTS.add(host)
        raise
    resp.raise_for_status()
    return resp


def fetch(url, encoding):
    resp = http_get(url)
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
    if method == "rakuten_books":
        return _check_rakuten_books(item)
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


def _check_rakuten_books(item):
    """楽天市場API(IchibaItem/Search)で楽天ブックス(shopCode=book)の在庫を判定する。
    楽天ブックスは定価販売の主要正規ルート。availability=1(在庫あり)で検索し、
    定価近傍(定価×1.05以下)のヒットがあれば「定価で買える在庫あり」とする。
    ※転売価格の出品はshopCode=bookに存在しないが、セット品/カートン誤ヒットは価格上限で弾く。
    環境変数 RAKUTEN_APP_ID 未設定時は判定不能(スキップ)扱い。"""
    app_id = os.environ.get("RAKUTEN_APP_ID")
    if not app_id:
        print(f"  （RAKUTEN_APP_ID未設定・スキップ {item['name']}）")
        return False, False, ""
    try:
        resp = http_get(
            config.RAKUTEN_ICHIBA_API,
            params={
                "applicationId": app_id,
                "keyword": item["keyword"],
                "shopCode": "book",   # 楽天ブックス（定価販売の直営店舗）
                "availability": "1",  # 在庫ありのみ
                "hits": "10",
                "format": "json",
            },
        )
        data = resp.json()
    except Exception as e:
        print(f"  ⚠ 楽天API取得失敗 {item['name']}: {e}")
        return False, False, ""

    retail = item.get("retail_price", 0)
    limit = int(retail * 1.05) if retail else None
    hits = []
    for wrap in data.get("Items", []):
        it = wrap.get("Item", wrap) if isinstance(wrap, dict) else {}
        price = it.get("itemPrice")
        if not isinstance(price, int):
            continue
        if limit and price > limit:
            continue  # セット品・カートン等の誤ヒットを価格上限で除外
        hits.append((price, (it.get("itemName") or "")[:40], it.get("itemUrl") or ""))
    if not hits:
        return False, True, "楽天ブックス在庫なし(定価近傍)"
    hits.sort()
    price, name, url = hits[0]
    return True, True, f"楽天ブックス {price:,}円 {name} {url}"


def fetch_altema_box_prices():
    """altemaのポケカBOX買取価格表から {BOX名: 買取価格(int)} を取得する。
    相場選別(passes_profit)の保守的な現金化下限指標として使う。
    返り値: (prices: dict[str->int], ok: bool)。"""
    try:
        resp = http_get(config.ALTEMA_BOX_URL)
        html = resp.content.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ altema相場取得失敗: {e}")
        return {}, False

    prices = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if not cells:
            continue
        name = cells[0]
        txt = " ".join(cells)
        pm = re.search(r"([0-9,]+)\s*円", txt)
        if name and pm:
            try:
                prices[name] = int(pm.group(1).replace(",", ""))
            except ValueError:
                pass
    return prices, True


def fetch_pricebase_box_price(url):
    """price-base の個別BOX相場記事から代表価格(中央値的な最頻値)を取得する。
    他TCG(ワンピ/遊戯王/DBFW)の相場源。altemaがポケカ専門のため。
    返り値: (price:int|None, ok:bool)。"""
    try:
        resp = http_get(url, allow_redirects=True)
        html = resp.content.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ price-base取得失敗: {e}")
        return None, False

    # BOX価格帯(3000〜200000円)の数値を集め、中央値を代表価格とする。
    # 中央値は外れ値(極端な広告・セット品価格)に強く、最頻値の同数ブレ問題も避けられる。
    nums = [int(p.replace(",", "")) for p in re.findall(r"([0-9,]{4,})\s*円", html)]
    box_nums = sorted(n for n in nums if 3000 <= n <= 200000)
    if not box_nums:
        return None, False
    mid = len(box_nums) // 2
    if len(box_nums) % 2:
        median = box_nums[mid]
    else:
        median = (box_nums[mid - 1] + box_nums[mid]) // 2
    return median, True


def _normalize_box_name(s):
    """相場照合用に銘柄名を正規化する。装飾語・記号・空白を落として比較精度を上げる。
    全角/半角スペース・中黒・括弧類を除去し、監視名固有の装飾(ポケカ/BOX/再販集約等)も削る。"""
    s = s or ""
    for w in ("ポケカ ", "ポケモンカード ", " BOX", "BOX", " 再販集約", "再販集約",
              "（横断）", "(横断)", "（在庫）", "(在庫)"):
        s = s.replace(w, "")
    # 空白・中黒・括弧などの照合ノイズを除去
    s = re.sub(r"[\s　・,，()（）\[\]【】「」]", "", s)
    return s


def match_altema_price(name, prices):
    """altema相場辞書から監視名 name に対応する買取価格を選ぶ。
    正規化後、(1)完全一致を最優先。(2)無ければ『監視名コアが altema銘柄名に含まれる』
    候補のうち最短(=余計な装飾やセット品でない単品)を選ぶ。
    altemaは単品BOX名が正解で、長い名前はセット/同梱品など別物の罠のため最短を採る。
    返り値: price(int) | None。"""
    core = _normalize_box_name(name)
    if len(core) < 3:  # 短すぎるコアは誤マッチしやすいので照合しない
        return None
    candidates = []  # (正規化altema名長, price)
    for k, v in prices.items():
        nk = _normalize_box_name(k)
        if core == nk:
            return v  # 完全一致が最優先
        if core in nk:
            candidates.append((len(nk), v))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])  # 最短=最も単品に近い
    return candidates[0][1]


def passes_profit(retail, market, is_pokeca):
    """相場選別: 市場価格と定価から、監視ON/除外を判定する。
    返り値: 'active'(監視ON) / 'dropped'(除外) / 'unknown'(判定不能=安全側で監視継続)。
    ポケカは別格で閾値を下げる(定価以上で売れれば監視)。"""
    if not retail or not market or retail <= 0 or market <= 0:
        return "unknown"
    spread = market / retail
    net = market * (1 - config.FEE_RATE) - retail
    spread_in = config.POKECA_SPREAD_IN if is_pokeca else config.PROFIT_SPREAD_IN
    if spread >= spread_in and net > 0:
        return "active"
    if spread < config.PROFIT_SPREAD_OUT:
        return "dropped"
    return "unknown"  # 中間帯は監視継続（安全側）


def discover_toei_new_boxes():
    """Phase2.5: 東映ストアのLightningSearch APIから、ワンピ/DBFWのBOX系新商品を発見する。
    新弾のgoodsコードが出たら通知＋その商品ページを在庫監視候補として記録する。
    返り値: (boxes: dict[goods->{name,price,url,stockMsg}], ok: bool)。"""
    headers = {"User-Agent": config.USER_AGENT, "Referer": "https://store.toei-anim.co.jp/"}
    boxes = {}
    all_ok = True  # 全ジャンル成功時のみTrue。一部失敗で全置換すると消えたジャンルが
                   # 次回「新弾」と誤検知されるため(H1)、all成功時だけ差分判定に使う。
    for dcode in config.TOEI_GENRE_CODES:
        try:
            resp = http_get(
                config.TOEI_SEARCH_API,
                params={"DType": "Genre", "DCode": dcode, "ItemPerPage": "200"},
                headers=headers,
            )
            data = resp.json()
            for it in data.get("searchResults", []):
                name = (it.get("name") or "").strip()
                goods = (it.get("goods") or "").strip()
                # BOX系だけ拾う（「BOX」を名前に含む。スタートデッキ等は除外）
                if not goods or "BOX" not in name:
                    continue
                try:
                    price = int(it.get("price") or 0)
                except (ValueError, TypeError):
                    price = 0
                boxes[goods] = {
                    "name": name,
                    "price": price,
                    "url": f"https://store.toei-anim.co.jp/shop/g/g{goods}/",
                    "stockMsg": it.get("stockMsg", ""),
                }
            time.sleep(config.REQUEST_INTERVAL)
        except Exception as e:
            print(f"  ⚠ 東映API取得失敗(DCode={dcode}): {e}")
            all_ok = False  # 1ジャンルでも失敗したら判定不能扱い
    return boxes, all_ok


def _title_matches(title):
    """RSS発見器のタイトル選別。監視キーワード合致が前提。
    ポケカ関連は別格（方針: 関連全部を定価なら狙う）で広く拾う。
    それ以外はサプライ用品（スリーブ/デッキケース等）を除外し、BOX/パック本体に絞る。
    従来は「ワンピース」等の部分一致だけだったため、デッキセットやサプライまで
    発見通知に混ざっていた（通知精度低下の一因）。"""
    if not any(kw in title for kw in config.WATCH_KEYWORDS):
        return False
    if any(kw in title for kw in config.POKECA_TITLE_KEYWORDS):
        return True
    return not any(kw in title for kw in config.EXCLUDE_TITLE_KEYWORDS)


def discover_from_rss():
    """nyuka-now のRSSフィードから、監視キーワードに合致する商品(入荷/再販)を発見する。
    返り値: (found: dict[link->{title,link}], ok: bool)。ok=False は全フィード取得失敗。
    ※規約配慮: 低頻度・キャッシュTTLで運用（FEED_URLSは最小限に絞る）。"""
    found = {}
    all_ok = True  # 全フィード成功時のみTrue（一部失敗での誤発見を防ぐ・H2）
    for feed_url in config.FEED_URLS:
        try:
            resp = http_get(feed_url, allow_redirects=True)
            xml = resp.content.decode("utf-8", errors="replace")
            for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
                tm = re.search(r"<title>(.*?)</title>", block, re.S)
                lm = re.search(r"<link>(.*?)</link>", block, re.S)
                if not (tm and lm):
                    continue
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", tm.group(1)).strip()
                link = lm.group(1).strip()
                # 監視キーワードに合致するものだけ拾う（ポケカ別格＝広く／他はサプライ除外）
                if _title_matches(title):
                    found[link] = {"title": title, "link": link}
            time.sleep(config.REQUEST_INTERVAL)
        except Exception as e:
            print(f"  ⚠ RSS取得失敗({feed_url[:40]}): {e}")
            all_ok = False
    return found, all_ok


def fetch_pokecard_new_products():
    """ポケカ公式の商品APIから現在の商品リストを取得する。
    resultAPI.php の4カテゴリ(expansion/construction/others/peripheral)を叩き、
    各商品を (productTitle, releaseDate) のキーで返す。
    返り値: (products: dict[key->info], ok: bool)。ok=False は全カテゴリ取得失敗。"""
    base = "https://www.pokemon-card.com/products/resultAPI.php"
    products = {}
    any_ok = False
    for ptype in config.POKECARD_PRODUCT_TYPES:
        try:
            resp = http_get(base, params={"productType": ptype, "page": "1"})
            data = resp.json()
            any_ok = True
            for p in data.get("products", []):
                title = (p.get("productTitle") or "").strip()
                rdate = (p.get("releaseDate") or "").strip()
                if not title:
                    continue
                # 発売日フィルタ: 古い弾を「新商品」と誤検知しないよう、発売年が
                # 「今年-1年」以降のものだけ新弾候補にする（年が変わっても自動追従・M5）。
                ym = re.search(r"(20\d\d)", rdate)
                if ym and int(ym.group(1)) < (_this_year() - 1):
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
    返り値: (signature: str|None, lines: list[str]|None, ok: bool)。ok=False は取得失敗。
    lines は抽出行（ページ内の出現順・重複除去済み）。前回との差分を通知本文に使う。"""
    try:
        html = fetch(item["url"], config.DEFAULT_ENCODING)
    except Exception as e:
        print(f"  ⚠ 取得失敗 {item['name']}: {e}")
        return None, None, False

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
    # 「現在）」（全角カッコ）も揮発行として除外（nyuka-nowの「○年○月○日現在）」対策）。
    volatile_line_re = re.compile(r"更新】|更新\)|時点|現在[、,）)]|最終更新|本日|今日")
    # 揮発トークン: 日付・時刻の数値そのものをハッシュから除去（行は残しつつ数値だけ消す）
    volatile_token_re = re.compile(
        r"【?\d{4}年\d{1,2}月\d{1,2}日.*?更新】?"
        r"|（\d{4}年\d{1,2}月\d{1,2}日現在）"
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

    # 抽出が0行=本文を拾えていない（JS描画/構造変化等）。空ハッシュを基準化すると
    # 監視が事実上死に、将来1行拾えた瞬間に誤検知するため、判定不能(前回維持)にする(H3)。
    if not picked:
        print(f"  ⚠ 抽出0行 {item['name']}（本文を拾えず・判定不能）")
        return None, None, False

    # 正規化（重複除去・ソート）してハッシュ化。順序揺れに強くする。
    normalized = "\n".join(sorted(set(picked)))
    sig = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    # 差分表示用の行リスト（出現順・重複除去・肥大防止の上限あり）
    lines = list(dict.fromkeys(picked))[: config.PAGE_LINES_KEEP]
    return sig, lines, True


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
    else:
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
            alerts.append((disco_item, "新規発見: " + " / ".join(lines), "info"))
        else:
            print(f"  RSS発見器: 新規なし（既知{len(discovered)}件）")

    # Phase2.5: 東映APIでワンピ/DBFWの新弾BOXを発見（goodsコード差分）。
    # RSS取得の成否とは独立に必ず実行する（以前はRSS失敗時に return でスキップされ、
    # toei_boxes が state から消える＋新弾検知が止まる不具合があった。nyuka-nowが
    # GitHub ActionsのIPを遮断してRSSが常時失敗する環境では致命的だった）。
    boxes, ok2 = discover_toei_new_boxes()
    toei_known = dict(prev.get("toei_boxes", {}))
    if not ok2:
        new_state["toei_boxes"] = toei_known
        print("  東映新弾発見: 判定不能（前回状態を維持）")
        return
    first_toei = "toei_boxes" not in prev
    fresh_boxes = [g for g in boxes if g not in toei_known]
    new_state["toei_boxes"] = boxes  # 現在の全BOXコードを保存
    if first_toei:
        print(f"  東映新弾発見: 初回・{len(boxes)}BOXを記録（通知なし）")
    elif fresh_boxes:
        names = "、".join(boxes[g]["name"][:30] for g in fresh_boxes[:5])
        print(f"  東映新弾発見: 新弾{len(fresh_boxes)}件🔔 ← 通知（{names}）")
        lines = [
            f"{boxes[g]['name'][:40]}（定価{boxes[g]['price']}円）{boxes[g]['url']}"
            for g in fresh_boxes[:5]
        ]
        toei_item = {"name": "東映 新弾BOX検知（在庫監視に追加候補）", "url": "https://store.toei-anim.co.jp/", "retail_price": 0}
        alerts.append((toei_item, "東映新弾: " + " / ".join(lines), "info"))
    else:
        print(f"  東映新弾発見: 新弾なし（{len(boxes)}BOX）")

    # 【在庫スイープ】新弾発見用に取得済みの全BOXの stockMsg を前回と比較し、
    # 「×(在庫なし)」から変化したBOXを在庫復活として通知する。追加リクエストゼロで
    # 東映ストアの全カタログ(約31BOX)をカバーできる。個別ページ監視(toei_stock_status)は
    # 高優先5商品の鮮度用に併存（検索APIはCDNキャッシュで数分遅れる可能性があるため）。
    if not first_toei:
        restocked = [
            b for g, b in boxes.items()
            if (toei_known.get(g) or {}).get("stockMsg") == "×"
            and (b.get("stockMsg") or "×") != "×"
        ]
        if restocked:
            names = "、".join(b["name"][:30] for b in restocked[:5])
            print(f"  東映在庫スイープ: {len(restocked)}件が×から変化🔔 ← 通知（{names}）")
            for b in restocked[:5]:
                sweep_item = {
                    "name": f"{b['name'][:44]}（東映ストア）",
                    "url": b["url"],
                    "retail_price": b.get("price", 0),
                }
                alerts.append((sweep_item, f"在庫表示が「×」→「{b.get('stockMsg', '')}」に変化", "stock"))


def run_price_screen(prev, new_state):
    """Phase3: altema/price-base相場で監視itemの利益判定を行い、dropped連続回数をstateに蓄積する。
    AUTO_DROP_ENABLED時はDROP_CONFIRM_COUNT回連続droppedの銘柄が監視スキップされる(run_once)。
    除外中も本関数の相場評価は毎回走るので、相場回復でカウントが0に戻れば自動復帰する。"""
    prices, ok = fetch_altema_box_prices()
    time.sleep(config.REQUEST_INTERVAL)
    drop_counts = dict(prev.get("drop_counts", {}))
    if not ok:
        new_state["drop_counts"] = drop_counts
        print("  相場選別: 判定不能（altema取得失敗・前回維持）")
        return

    mode = "自動除外あり" if config.AUTO_DROP_ENABLED else "ログのみ"
    print(f"  --- 相場選別（{mode}）---")
    for item in config.WATCH_ITEMS:
        retail = item.get("retail_price", 0)
        if not retail:
            continue
        name = item["name"]
        market = None
        # 相場源: item に price_url(price-base個別記事)があれば他TCGもそこから取る。
        # なければポケカは altema辞書から銘柄名で部分一致（altemaはポケカ専門）。
        if item.get("price_url"):
            market, pok = fetch_pricebase_box_price(item["price_url"])
            time.sleep(config.REQUEST_INTERVAL)
            if not pok:
                market = None
        else:
            market = match_altema_price(name, prices)
        if market is None:
            # 相場取れず＝判定不能。他TCGで相場源未設定の場合はここに来る（安全側=監視継続）。
            continue
        is_pokeca = ("ポケカ" in name) or ("ポケモンカード" in name)
        verdict = passes_profit(retail, market, is_pokeca)
        key = item["key"]
        if verdict == "dropped":
            drop_counts[key] = drop_counts.get(key, 0) + 1
        else:
            drop_counts[key] = 0
        net = market * (1 - config.FEE_RATE) - retail
        flag = ""
        if verdict == "dropped" and drop_counts[key] >= config.DROP_CONFIRM_COUNT:
            flag = f"  ⚠除外候補(連続{drop_counts[key]}回・自動除外は未有効)"
        print(f"    {name[:24]}: 定価{retail} 相場{market} 手残り{net:+.0f}円 → {verdict}{flag}")
    # 監視対象から外した銘柄の drop_counts は残さない（stateの肥大・幽霊キー防止）。
    valid_keys = {it["key"] for it in config.WATCH_ITEMS}
    drop_counts = {k: c for k, c in drop_counts.items() if k in valid_keys}
    new_state["drop_counts"] = drop_counts


def _upcoming_dates(text, today):
    """テキスト中の日付(202X年M月D日 / M月D日 / 202X/M/D / 202X.M.D)を解決して返す。
    年つき日付はその年で確定。年なしのM月D日は「今日から180日以上過去なら来年」と
    解釈する（年跨ぎ対応）。年つき部分を除外してから年なしを探す
    （「2024年7月19日」の「7月19日」を今年と誤解釈しないため）。"""
    from datetime import date
    found = []
    for y, m, d in re.findall(r"(20\d\d)年\s*(\d{1,2})月\s*(\d{1,2})日", text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    stripped = re.sub(r"20\d\d年\s*\d{1,2}月\s*\d{1,2}日", "", text)
    for m, d in re.findall(r"(\d{1,2})月\s*(\d{1,2})日", stripped):
        try:
            dt = date(today.year, int(m), int(d))
        except ValueError:
            continue
        if (today - dt).days > 180:
            dt = date(today.year + 1, int(m), int(d))
        found.append(dt)
    for y, m, d in re.findall(r"(202\d)[/.](\d{1,2})[/.](\d{1,2})", text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return found


def suggest_watch_candidates(prices, official_titles):
    """altema相場辞書から「買取が高いのに未監視」のポケカ銘柄を監視追加候補として返す。
    (1)価格帯フィルタ (2)ポケカ公式APIの現行商品リストとの照合（絶版＝正規入手ルートが
    無い銘柄を除外） (3)既存WATCH_ITEMSとの双方向部分一致で未監視のみ、を通過したものを
    高値順に返す。official_titles はポケカ公式APIの商品タイトル一覧。"""
    watched = [_normalize_box_name(it["name"]) for it in config.WATCH_ITEMS]
    current = [_normalize_box_name(t) for t in official_titles]
    cands = []
    for name, price in prices.items():
        if not (config.SUGGEST_MIN_PRICE <= price <= config.SUGGEST_MAX_PRICE):
            continue
        nk = _normalize_box_name(name)
        if len(nk) < 3:
            continue
        # 公式の現行商品リスト（直近1〜2年の発売分）に無い＝絶版でチャンスが湧かない
        if not any(nk in c for c in current):
            continue
        if any(w and (w in nk or nk in w) for w in watched):
            continue
        cands.append((price, name))
    cands.sort(reverse=True)
    return [f"{n}（買取{p:,}円）" for p, n in cands]


def extract_opportunities(prev, new_state, today):
    """監視中のanime-matsuriまとめページの抽出行から「応募/予約チャンス」を集める。
    条件: 抽選/予約/受付/応募/先着 のいずれかを含み、かつ今日〜OPPORTUNITY_WINDOW_DAYS日
    以内の日付を含む行（過去の抽選履歴の行を日付で除外する）。
    返り値: ["[商品名] 行テキスト", ...]（重複除去・上限あり）。"""
    out, seen = [], set()
    for item in config.WATCH_ITEMS:
        if item.get("method") != "page_update" or "anime-matsuri" not in item.get("url", ""):
            continue
        val = new_state.get(item["key"]) or prev.get(item["key"])
        lines = val.get("lines") if isinstance(val, dict) else None
        if not lines:
            continue
        # 商品名の要約（「ポケカ 」「 抽選/再販まとめ（anime-matsuri）」等の定型を削る）
        short = item["name"]
        for w in (" 抽選/再販まとめ（anime-matsuri）", " 抽選/予約まとめ（anime-matsuri）",
                  " 再販告知まとめ（anime-matsuri）", "ポケカ ", "ワンピ "):
            short = short.replace(w, "")
        for i, line in enumerate(lines):
            if not any(kw in line for kw in config.OPPORTUNITY_KEYWORDS):
                continue
            # 短すぎる断片（テーブルセル由来）・長文や関連記事紹介文はチャンスでない
            if not (config.DIGEST_LINE_MINLEN <= len(line) <= config.DIGEST_LINE_MAXLEN):
                continue
            if any(mk in line for mk in config.DIGEST_EXCLUDE_MARKERS):
                continue
            # 判定①: 近い将来の日付つき。日付が隣の行（テーブルの期間セル）にある構造に
            # 対応するため、次の行まで結合して判定する。
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            ctx_dates = _upcoming_dates(line + "　" + nxt, today)
            dated = any(0 <= (d - today).days <= config.OPPORTUNITY_WINDOW_DAYS for d in ctx_dates)
            # 判定②: 日付がなくても「今応募できる」マーカー（Amazon招待リクエスト等）。
            # ただし過去の年（例: 2025年1月〜）に言及する行は古い履歴なので除外する。
            years = [int(y) for y in re.findall(r"(20\d\d)年", line)]
            stale = bool(years) and min(years) < today.year
            open_now = (not stale) and any(mk in line for mk in config.DIGEST_OPEN_MARKERS)
            if not (dated or open_now):
                continue
            # 表示: 日付が隣の行にしかない場合はその行も添える
            text = line
            if dated and not _upcoming_dates(line, today) and nxt:
                text = f"{line} ／ {nxt[:40]}"
            # 重複判定は行テキストのみで行う（同じ告知が複数商品ページに載る場合の重複排除）
            if text in seen:
                continue
            seen.add(text)
            out.append(f"[{short}] {text[:90]}")
            if len(out) >= config.DIGEST_MAX_LINES:
                return out
    return out


def append_heartbeat(prev, new_state, alerts, health):
    """日次ヘルスレポート: JST9時以降の最初のパスで、Bot生存＋監視状態サマリを1通送る。
    「沈黙が『検知なし』なのか『Bot停止』なのか分からない」問題への対策
    （過去にActions枠切れで3週間気づかず停止していた教訓）。"""
    now_jst = datetime.now(ZoneInfo("Asia/Tokyo"))
    today = now_jst.strftime("%Y-%m-%d")
    new_state["last_heartbeat"] = prev.get("last_heartbeat")
    new_state["digest_seen"] = prev.get("digest_seen")  # 非発火パスでも既知チャンスを維持する
    new_state["suggested_seen"] = prev.get("suggested_seen")
    if now_jst.hour < 9 or prev.get("last_heartbeat") == today:
        return
    new_state["last_heartbeat"] = today
    ok_n = len(health["ok"])
    # 想定内の取得不能: nyuka-now系（クラウドIP遮断）と、RAKUTEN_APP_ID未設定時の楽天監視。
    # それ以外の取得不能は故障の可能性があるので「要確認」として名前を出す。
    no_rakuten_id = not os.environ.get("RAKUTEN_APP_ID")
    def _is_expected(u):
        return ("nyuka-now" in u) or (no_rakuten_id and "rakuten" in u)
    expected = [n for n, u in health["fail"] if _is_expected(u)]
    unexpected = [n for n, u in health["fail"] if not _is_expected(u)]
    lines = [f"監視{ok_n + len(health['fail'])}件: 正常{ok_n}件"]
    if expected:
        lines.append(f"想定内の取得不能{len(expected)}件（nyuka-now系/楽天API未設定）")
    if unexpected:
        lines.append(f"⚠要確認の取得不能{len(unexpected)}件: " + "、".join(unexpected[:6]))
    lines.append("このレポートが毎朝届いていればBotは正常稼働しています。")
    hb_item = {
        "name": "📊 日次ヘルスレポート（Bot生存確認）",
        "url": "https://github.com/RyoUmeyama/gunpla-restock-tracker/actions",
        "retail_price": 0,
    }
    print("  📊 日次ヘルスレポートを送信")
    alerts.append((hb_item, " ／ ".join(lines), "info"))

    # 応募/予約チャンス・ダイジェスト（ヘルスレポートと同じ朝1回に同梱）。
    # 「再販の瞬間を待つ」だけでは通知は稀にしか来ない。締切が先にある抽選・予約は
    # 速度勝負でない確実な入手ルートなので、朝に能動的に提示する。
    # ※通知するのは「新規に現れたチャンスだけ」。Amazon招待リクエスト等は一度登録すれば
    #   再登録不要のため、既知のチャンスを毎朝繰り返し見せない（既知はstateに保持）。
    opps = extract_opportunities(prev, new_state, now_jst.date())
    seen_prev = prev.get("digest_seen")
    first_digest = not isinstance(seen_prev, list)
    seen = list(seen_prev or [])
    fresh = [o for o in opps if o not in set(seen)]
    seen.extend(fresh)
    new_state["digest_seen"] = seen[-config.DIGEST_SEEN_KEEP:]
    if first_digest:
        # 導入初回: 現在見えているチャンスは対応済みとみなし、記録のみ（通知なし）
        print(f"  📅 チャンスダイジェスト: 初回・既知{len(fresh)}件を記録（通知なし・対応済み扱い）")
    elif fresh:
        digest_item = {
            "name": f"📅 新規の応募/予約チャンス {len(fresh)}件",
            "url": "https://anime-matsuri.com/",
            "retail_price": 0,
        }
        print(f"  📅 新規チャンス {len(fresh)}件を送信")
        alerts.append((digest_item, "\n" + "\n".join("・" + o for o in fresh), "info"))
    else:
        print("  📅 新規の応募/予約チャンスなし")

    # 監視追加候補の自動提案（altema相場ベース・提案済みは再提案しない）。
    # 監視リストが市場の移り変わりで古びるのを防ぐ（アビスアイ等の見落とし再発防止）。
    try:
        prices, ok_p = fetch_altema_box_prices()
        if ok_p:
            sugg_prev = prev.get("suggested_seen")
            first_sugg = not isinstance(sugg_prev, list)
            sugg_seen = list(sugg_prev or [])
            officials = new_state.get("pokecard_official") or prev.get("pokecard_official") or []
            titles = [k.split("|")[0] for k in officials]
            cands = [c for c in suggest_watch_candidates(prices, titles) if c not in set(sugg_seen)]
            cands = cands[: config.SUGGEST_MAX]
            sugg_seen.extend(cands)
            new_state["suggested_seen"] = sugg_seen[-config.DIGEST_SEEN_KEEP:]
            if first_sugg:
                print(f"  🧭 監視追加候補: 初回・{len(cands)}件を記録（通知なし）")
            elif cands:
                sug_item = {
                    "name": f"🧭 監視追加候補 {len(cands)}件（相場が高いのに未監視）",
                    "url": config.ALTEMA_BOX_URL,
                    "retail_price": 0,
                }
                print(f"  🧭 監視追加候補 {len(cands)}件を提案")
                alerts.append((sug_item,
                               "\n" + "\n".join("・" + c for c in cands) +
                               "\n（監視に追加したい銘柄があればClaude Codeに伝えてください）", "info"))
            else:
                print("  🧭 新規の監視追加候補なし")
    except Exception as e:
        print(f"  ⚠ 監視追加候補の算出でエラー（スキップ）: {e}")


def _process_item(item, prev, new_state, alerts, health):
    """1監視項目の判定・状態更新・通知起票。run_once から項目ごとに例外隔離されて呼ばれる。"""
    key = item["key"]

    if item.get("method") == "pokecard_official_list":
        # ポケカ公式API: (title,releaseDate)セット差分で新商品を検知（初回は基準記録）
        products, ok = fetch_pokecard_new_products()
        if not ok:
            new_state[key] = prev.get(key, [])
            health["fail"].append((item["name"], item.get("url", "")))
            print(f"  {item['name']}: 判定不能（前回状態を維持）")
            return
        health["ok"].append(item["name"])
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
                alerts.append((item, "ポケカ新商品: " + " / ".join(detail_lines[:5]), "info"))
            else:
                print(f"  {item['name']}: 新商品なし（{len(cur_keys)}商品）")
        return

    if item.get("method") == "page_update":
        # 告知ページ: 前回ハッシュと変化したら通知（初回は基準値を保存のみ）
        sig, lines, ok = compute_page_signature(item)
        time.sleep(config.REQUEST_INTERVAL)
        first_seen = key not in prev  # 初回判定はキー存在で統一(H4)
        if not ok:
            # 取得失敗: 前回値があれば維持、無ければキー未設定のまま(次回も初回扱い)
            if key in prev:
                new_state[key] = prev[key]
            health["fail"].append((item["name"], item.get("url", "")))
            print(f"  {item['name']}: 判定不能（前回状態を維持）")
            return
        health["ok"].append(item["name"])
        # 状態は {"sig", "lines"}。旧形式（ハッシュ文字列のみ）からも読めるようにする。
        prev_val = prev.get(key)
        prev_sig = prev_val.get("sig") if isinstance(prev_val, dict) else prev_val
        prev_lines = prev_val.get("lines") if isinstance(prev_val, dict) else None
        new_state[key] = {"sig": sig, "lines": lines}
        if first_seen:
            print(f"  {item['name']}: 初回・基準を記録（通知なし）")
        elif sig != prev_sig:
            # 「何が変わったか」を通知に載せる（新規に現れた行＝新しい入荷/受付情報）。
            # 従来は「更新されました」だけでページを開いて探す必要があり、精度が低かった。
            added = []
            if isinstance(prev_lines, list):
                prev_set = set(prev_lines)
                added = [l for l in lines if l not in prev_set]
            if added:
                shown = [l[: config.DIFF_LINE_MAXLEN] for l in added[: config.DIFF_LINES_SHOWN]]
                more = f" …ほか{len(added) - len(shown)}行" if len(added) > len(shown) else ""
                detail = f"更新検知・新規{len(added)}行: " + " ／ ".join(shown) + more
            else:
                detail = "再販告知が更新されました（既存行の削除/変更。リンク先で確認を）"
            print(f"  {item['name']}: 告知更新を検知🔔 ← 通知（新規{len(added)}行）")
            alerts.append((item, detail, "info"))
        else:
            print(f"  {item['name']}: 更新なし")
        return

    # 在庫系（gdb_soldout / toei_stock_status / rakuten_books）
    in_stock, ok, detail = check_item(item)
    time.sleep(config.REQUEST_INTERVAL)
    if not ok:
        new_state[key] = prev.get(key, False)  # 取得失敗は前回維持（誤通知防止）
        health["fail"].append((item["name"], item.get("url", "")))
        print(f"  {item['name']}: 判定不能（前回状態を維持）")
        return

    health["ok"].append(item["name"])

    if item.get("method") == "gdb_soldout":
        # 店舗単位の遷移検知: 「新たに在庫ありになった店」が現れたら通知する。
        # 全体boolだけの判定では、Amazon(転売価格の場合あり)が在庫あり続けると状態が
        # trueに張り付き、ヨドバシ等の定価店の復活を見逃す盲点があった。
        # detail は _check_gdb_soldout が生成する在庫あり店名の ", " 連結。
        # 大文字小文字の表記ゆれで「新規店舗」と誤判定しないよう小文字に正規化。
        shops = [s.lower() for s in detail.split(", ") if s] if detail else []
        prev_val = prev.get(key)
        prev_shops = prev_val.get("shops") if isinstance(prev_val, dict) else None
        new_state[key] = {"in_stock": in_stock, "shops": shops}
        status = "在庫あり🟢" if in_stock else "在庫なし🔴"
        if key not in prev:
            print(f"  {item['name']}: {status} [{detail}]  （初回・基準を記録）")
        elif prev_shops is None:
            # 旧形式(bool)からの移行: 前回Falseなら従来どおり復活通知、Trueなら基準更新のみ
            if in_stock and not bool(prev_val):
                print(f"  {item['name']}: {status} [{detail}]  ← 復活！")
                alerts.append((item, detail, "stock"))
            else:
                print(f"  {item['name']}: {status} [{detail}]  （店舗別基準に移行）")
        else:
            new_shops = [s for s in shops if s not in set(prev_shops)]
            if new_shops:
                d = f"新たに在庫あり: {', '.join(new_shops)}（在庫あり全店: {detail}）"
                print(f"  {item['name']}: {status}  ← 新規店舗で復活！（{', '.join(new_shops)}）")
                alerts.append((item, d, "stock"))
            else:
                print(f"  {item['name']}: {status} [{detail}]")
        return

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
        alerts.append((item, detail, "stock"))  # 在庫検知=緊急(買える)
    detail_note = f" [{detail}]" if detail else ""
    print(f"  {item['name']}: {status}{detail_note}{change}")


def run_once():
    """在庫チェックを1パス実行し、在庫復活/告知更新があれば通知する。状態はファイルで永続化。"""
    _UNREACHABLE_HOSTS.clear()  # 接続不能ホストの記録はパスごとにリセット（次パスで再挑戦）
    prev = load_state()
    new_state = {}
    alerts = []  # [(item, detail)] 通知すべき変化
    health = {"ok": [], "fail": []}  # 日次ヘルスレポート用。fail は (name, url) のリスト

    # Phase2: RSS発見器で新弾・再販を自動キャッチ（固定リストを動的に補完）。
    # 想定外の例外でもパス全体を壊さない（関連stateを前回維持して継続）。
    try:
        run_discovery(prev, new_state, alerts)
    except Exception as e:
        for k in ("discovered", "toei_boxes"):
            if k in prev:
                new_state.setdefault(k, prev[k])
        print(f"  ⚠ 発見層で想定外エラー（前回状態を維持）: {e}")

    # Phase3: 相場選別。定価割れが連続確定した銘柄は AUTO_DROP_ENABLED 時に監視スキップする。
    if config.PRICE_SCREEN_ENABLED:
        try:
            run_price_screen(prev, new_state)
        except Exception as e:
            if "drop_counts" in prev:
                new_state.setdefault("drop_counts", prev["drop_counts"])
            print(f"  ⚠ 相場選別で想定外エラー（前回状態を維持）: {e}")
    drop_counts = prev.get("drop_counts", {})

    for item in config.WATCH_ITEMS:
        key = item["key"]

        # Phase3自動除外: 相場選別で「定価割れ」が連続確定した銘柄はチェック自体をスキップ。
        # 状態は維持する（相場が回復して除外解除されたとき、誤った復活通知を出さないため）。
        if config.AUTO_DROP_ENABLED and drop_counts.get(key, 0) >= config.DROP_CONFIRM_COUNT:
            if key in prev:
                new_state[key] = prev[key]
            print(f"  {item['name']}: 相場選別により除外中（定価割れ連続{drop_counts[key]}回）")
            continue

        # 1商品の判定で想定外の例外が起きてもパス全体を壊さない（バグ・サイト構造の
        # 急変・不正なレスポンス等）。その商品だけ前回状態を維持して次へ進む。
        try:
            _process_item(item, prev, new_state, alerts, health)
        except Exception as e:
            if key in prev:
                new_state[key] = prev[key]
            health["fail"].append((item["name"], item.get("url", "")))
            print(f"  ⚠ {item['name']}: 想定外エラーで判定不能（前回状態を維持）: {e}")

    # 日次ヘルスレポート（JST9時以降の最初のパスで1通・生存確認）
    append_heartbeat(prev, new_state, alerts, health)

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
    # TEST_NOTIFY=1 のとき: 在庫検知を待たずにテスト通知を1本送り、通知経路(Secrets)を点検する。
    # 通知は「在庫復活時」しか走らないため、Secrets再登録の確認手段としてこれを使う。
    if os.environ.get("TEST_NOTIFY") == "1":
        print("=== TEST_NOTIFY: 通知経路テスト ===")
        test_item = {
            "name": "【通知経路テスト】これはテスト送信です",
            "url": "https://github.com/RyoUmeyama/gunpla-restock-tracker",
            "retail_price": 0,
        }
        notify([(test_item, "Secrets再登録後の疎通確認。届けばメール/Discordとも正常。", "stock")])
        print("=== テスト送信 完了 ===")
        return

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


def build_messages(alerts):
    """alerts: [(item, detail, kind)] → (subject, text, html, webhook_title, webhook_lines)
    kind="stock"(在庫検知=緊急・買える) と "info"(新弾/告知/発見=お知らせ) で文面を分ける。
    後方互換: 2要素タプルは kind="stock" 扱い。"""
    norm = []
    for a in alerts:
        if len(a) == 3:
            norm.append(a)
        else:
            norm.append((a[0], a[1], "stock"))

    has_stock = any(k == "stock" for _, _, k in norm)
    n = len(norm)
    # 件名に先頭商品の名前を入れる（「N件」だけでは開くまで中身が分からない）。
    # 在庫検知が混在する場合は在庫系を先頭に出す。
    norm.sort(key=lambda a: 0 if a[2] == "stock" else 1)
    first_name = norm[0][0]["name"][:24]
    suffix = f" ほか{n - 1}件" if n > 1 else ""

    if has_stock:
        # 在庫検知が含まれる=緊急。買える可能性があるので煽り文。
        subject = f"🤖【在庫検知】{first_name}{suffix}（要・定価確認）"
        headline = "🤖 転売検証 在庫検知！"
        note = config.NOTE  # 「数分で完売の可能性大。即購入→即売り」
        color = "#c00"
    else:
        # お知らせ系のみ(新弾・告知更新・発見)。緊急でないので穏やかに。
        subject = f"📣【お知らせ】{first_name}{suffix}"
        headline = "📣 転売検証 新弾・再販のお知らせ"
        note = "新弾・再販・告知の更新を検知しました（在庫が買える状態とは限りません。リンク先で確認を）。"
        color = "#36c"

    text_lines = [headline, note, ""]
    web_lines = [note, ""]
    html_rows = []
    for item, detail, kind in norm:
        tag = "【在庫】" if kind == "stock" else "【お知らせ】"
        price = f"（定価{item['retail_price']:,}円）" if item.get("retail_price") else ""
        line = f"{tag}{item['name']}{price} {detail}"
        text_lines.append("・" + line)
        text_lines.append(f"  {item['url']}")
        text_lines.append("")
        web_lines.append("・" + line)
        web_lines.append(item["url"])
        html_rows.append(
            f'<li style="margin-bottom:10px;"><strong>{tag}{item["name"]}</strong>'
            f'{price} {detail}<br><a href="{item["url"]}">{item["url"]}</a></li>'
        )
    text = "\n".join(text_lines)
    html = (
        '<html><body style="font-family:sans-serif;">'
        f'<h2 style="color:{color};">{headline}</h2>'
        f"<p>{note}</p><ul>{''.join(html_rows)}</ul>"
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
    # SMTP_PORTが非数値（GitHub Actionsのマスク '***' 等）でも落ちないようフォールバック
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except (ValueError, TypeError):
        port = 587
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
