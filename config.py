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
        # 在庫監視(toei)が見逃しても、告知更新で数時間〜数日前に構えられる。
        "name": "OP-16 再販告知まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/onepiececard-kessennokoku-op16-reservation-lottery/",
        "retail_price": 5280,
        "key": "op16_restock_news",
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
PAGE_UPDATE_KEYWORDS = ["受付中", "予約", "抽選", "先着", "再販", "入荷"]

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
