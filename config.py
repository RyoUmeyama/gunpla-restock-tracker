#!/usr/bin/env python3
"""
監視設定 — 転売検証用 在庫トラッカー（小額モデル検証 Q1 用）

目的: docs/07_minimal_validation.md / docs/08_broad_screening.md の Q1「入手再現性」を
      実測するための入手監視。狙いの商品が正規店で定価・在庫ありになった瞬間を検知して即通知し、
      争奪戦に参加できる状態を作る。

複数サイト・複数判定方式に対応:
  各監視品(WATCH_ITEMS)は "method" で在庫判定方式を指定する。
    - "gdb_soldout": GunplaDatabase。shop_status_container ブロックの soldout/「売切」で判定（ガンプラ用）
    - "toei_stock_status": 東映アニメ公式。埋め込みJSONの stock_status の値で判定（OP-16用）
                          stock_status が "0" 以外なら在庫あり。
  新サイトを足す場合は check_stock.py に判定関数を追加し、method を増やす。
"""

# 監視対象。各品に method（判定方式）・url・表示名・定価を持たせる。
WATCH_ITEMS = [
    {
        "name": "HG 1/144 ナイチンゲール",
        "method": "gdb_soldout",
        "url": "https://gunpla-database.doc-sin.life/?no=2294",
        "retail_price": 7700,
        "key": "gunpla_nightingale",
    },
    {
        # 網羅スクリーニング(docs/08)の本命。唯一 入手容易さ=medium。
        # 東映アニメ公式の正規・定価¥5,280ルートを監視。stock_status で在庫判定。
        "name": "ワンピカード OP-16 決戦の刻 BOX",
        "method": "toei_stock_status",
        "url": "https://store.toei-anim.co.jp/shop/g/gONP03841O1/",
        "retail_price": 5280,
        "key": "op16_kessen",
    },
    {
        # 【C: 再販告知の監視】OP-16の再販入荷情報まとめ（40社以上集約）。
        # 在庫の「瞬間」ではなく再販告知の「更新」を検知する。間引きに強い保険。
        "name": "OP-16 再販告知まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/onepiececard-kessennokoku-op16-reservation-lottery/",
        "retail_price": 5280,
        "key": "op16_restock_news",
    },
    # ===== 2026-06-22 多数監視に拡張（docs/10_multi_watch_sources.md で実地検証）=====
    # 設計判断: 個別在庫判定(駿河屋検索/カードラッシュ/コトブキヤ)は実地で不安定=誤検知リスク
    # と判明したため、確実な個別在庫判定(東映公式・GunplaDatabase)＋集約ページ更新検知
    # (nyuka-now の page_update)に一本化。誤検知ゼロで在庫変動・再販告知を広く拾う。

    # --- 個別在庫判定（実地検証で安定・確実なものだけ）---
    {
        "name": "DBFW MANGA BOOSTER 02 [SB02] BOX（在庫）",
        "method": "toei_stock_status",
        "url": "https://store.toei-anim.co.jp/shop/g/gDBS00124O1/",
        "retail_price": 7920,
        "key": "dbfw_sb02_stock",
    },
    # --- 集約ページ更新検知（page_update。誤検知なし・在庫変動/再販告知を拾う）---
    {
        "name": "DBFW CROSS FORCE FB10 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/140408",
        "retail_price": 5280,
        # 他TCGの相場源(price-base個別記事)。altemaはポケカ専門のため。
        "price_url": "https://price-base.com/useful/crossforce-box-market",
        "key": "dbfw_fb10_news",
    },
    {
        "name": "DBFW MANGA BOOSTER SB01 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/151955",
        "retail_price": 7920,
        "key": "dbfw_sb01_news",
    },
    {
        "name": "ポケカ テラスタルフェスex 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/144710",
        "retail_price": 5500,
        "key": "pokeca_terastal_news",
    },
    {
        "name": "ポケカ ホワイトフレア 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/145377",
        "retail_price": 5800,
        "key": "pokeca_whiteflare_news",
    },
    {
        # 検索URL(?s=)は「○年○月○日現在」の揮発日付＋無関係記事の混入で毎回ハッシュ変化
        # していたため、ホワイトフレア(145377)と対の安定した個別まとめ記事に差し替え。
        "name": "ポケカ ブラックボルト 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/145378",
        "retail_price": 5800,
        "key": "pokeca_blackbolt_news",
    },
    {
        "name": "ワンピTCG 在庫・再販集約（横断）",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/97073",
        "retail_price": 5280,
        "key": "onepiece_cross_news",
    },
    {
        "name": "遊戯王 WPP7 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/24047",
        "retail_price": 2970,
        "key": "yugioh_wpp7_news",
    },
    {
        "name": "遊戯王 RARITY COLLECTION RC04 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/134935",
        "retail_price": 5280,
        "key": "yugioh_rc04_news",
    },
    {
        "name": "名探偵コナンTCG 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/143009",
        "retail_price": 7392,
        "key": "conan_tcg_news",
    },
    {
        "name": "ガンプラ 在庫・再販集約（プレバン/人気品横断）",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/6830",
        "retail_price": 0,
        "key": "gunpla_news",
    },
    # ===== Phase1（2026-06-22 動的追従 docs/11）=====
    # --- ポケカ別格: 公式API 新弾検知（新商品が出たら通知＋将来は監視候補化）---
    {
        "name": "ポケカ公式 新商品検知（全カテゴリ）",
        "method": "pokecard_official_list",
        "url": "https://www.pokemon-card.com/products/resultAPI.php",
        "retail_price": 0,
        "key": "pokecard_official",
    },
    # --- ポケセン実店舗 販売方法ページ（都内店頭チャネル。matoca抽選/整理券の事前確定）---
    {
        "name": "ポケセン トウキョーDX 店頭ニュース",
        "method": "page_update",
        "url": "https://shop.pokemon.co.jp/ja/shop/pokemoncenter-tokyodx/news/",
        "retail_price": 0,
        "key": "pokecenter_tokyodx_news",
    },
    {
        "name": "ポケセン メガトウキョー 店頭ニュース",
        "method": "page_update",
        "url": "https://shop.pokemon.co.jp/ja/shop/pokemoncenter-megatokyo/news/",
        "retail_price": 0,
        "key": "pokecenter_megatokyo_news",
    },
    # --- ポケカ コラボ・プロモ・グッズ 公式info一覧（ポケカ全方位）---
    {
        "name": "ポケカ公式 info一覧（コラボ/限定/プロモ）",
        "method": "page_update",
        "url": "https://www.pokemon-card.com/info/",
        "retail_price": 0,
        "key": "pokecard_info",
    },
    # 追加の監視品はここに足す（方針順守: 非酒類・正規新品・未開封のまま売れる）
]

# --- gdb_soldout 方式（GunplaDatabase）の設定 ---
GDB_SOLDOUT_MARKER = "soldout"
GDB_SHOP_BLOCK_CLASS = "shop_status_container"

# --- toei_stock_status 方式（東映アニメ公式）の設定 ---
# 商品ページ埋め込みJSONの stock_status を読む。"0" は在庫なし、それ以外は在庫あり。
TOEI_ENCODING = "shift_jis"
TOEI_INSTOCK_MEANS_NOT = "0"  # stock_status がこの値なら在庫なし

# --- page_update 方式（再販告知ページの更新検知）の設定 ---
# ページから再販関連の本文だけを抽出し、正規化してハッシュ化。前回ハッシュと変われば
# 「告知が更新された」と判定して通知する。広告等のノイズを除くため抽出範囲を絞る。
# 「受付中/予約/抽選/先着/再販/入荷」と日付の周辺テキストを抽出対象にする。
# ※揮発日付（○月○日更新・○時○分時点）は compute_page_signature で除外して誤検知を防ぐ。
PAGE_UPDATE_KEYWORDS = ["受付中", "予約", "抽選", "先着", "再販", "入荷", "整理券", "販売方法", "コラボ", "限定", "プロモ", "受注"]

# --- pokecard_official_list 方式（ポケカ公式API 新弾検知）の設定 ---
# resultAPI.php の4カテゴリ。新弾は (productTitle, releaseDate) のセット差分で検知する。
# productType の正しい値は下記4つのみ（deck/other等は無効値で全件返すサイレント故障の罠）。
POKECARD_PRODUCT_TYPES = ["expansion", "construction", "others", "peripheral"]

# --- Phase2: RSS発見器（nyuka-now instockフィードで新弾・再販を自動発見）---
# 規約配慮: nyuka-nowは転売目的利用を規約で禁止→低頻度・最小フィードに留める。
# instock限定フィードはホビーTCG限定でクリーン（メインfeedは全ジャンル混在）。
FEED_URLS = [
    "https://nyuka-now.com/archives/category/instock/feed",
]
# 発見対象キーワード。ポケカは別格＝関連語を広く。
WATCH_KEYWORDS = [
    "ポケモンカード", "ポケカ", "ワンピースカード", "ワンピース",
    "遊戯王", "ドラゴンボール", "DBFW", "フュージョンワールド", "名探偵コナン",
]
# 動的監視候補の上限（net利益降順で上位のみ。暴走防止）
MAX_DISCOVERED_ITEMS = 30

# --- Phase3: 相場選別（価値が落ちた旧銘柄を自動除外）---
# 相場ソース: altema BOX買取価格表（静的HTML・堅牢。現金化下限の保守指標）。
# pokeca-chartはBOX相場APIが先方バグで故障のため使わない（docs/11）。
ALTEMA_BOX_URL = "https://altema.jp/pokemoncard/mikaihubox"
FEE_RATE = 0.10            # 売却手数料（メルカリ等）
PROFIT_SPREAD_IN = 1.25    # 市場/定価 がこれ以上で監視ON（手数料・送料控除後も利益）
PROFIT_SPREAD_OUT = 1.05   # これ未満（定価割れ近傍）で監視から自動除外
POKECA_SPREAD_IN = 1.0     # ポケカは別格: 定価以上で売れれば監視ON（ほぼ全弾）
# 相場選別の安全ガード: 取得失敗時は判定不能=監視継続（優良銘柄の取りこぼし防止）。
# dropped化はヒステリシス（連続観測）で行い、一時的な相場ブレで外さない。
DROP_CONFIRM_COUNT = 2     # 連続でこの回数 dropped 判定が続いたら実際に除外
# Phase3は「ログ通知のみ」で開始（自動除外はまだしない）。数日様子を見て誤除外がないことを
# 確認してから AUTO_DROP_ENABLED を True にして自動除外を有効化する。
PRICE_SCREEN_ENABLED = True   # 相場選別の判定をログ表示する
AUTO_DROP_ENABLED = False     # True にすると dropped 銘柄を実際に監視から除外（まだ無効）

# 既定の文字コード（明示しないサイト用）
DEFAULT_ENCODING = "utf-8"

# HTTP設定
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
REQUEST_INTERVAL = 1.5  # 商品ごとのリクエスト間隔（サイト負荷配慮）

# 前回在庫状態の保存ファイル
STATE_FILE = "stock_state.json"

# 通知本文の心得（争奪戦は数分で完売。価格は要確認）
NOTE = (
    "再販/在庫を検知。数分で完売の可能性大。"
    "⚠️必ず価格を確認（定価で買えるか。転売価格の場合あり）。"
    "定価なら即購入→即売りで売却検証（寝かせ厳禁）。"
)
