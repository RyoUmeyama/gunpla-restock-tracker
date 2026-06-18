#!/usr/bin/env python3
"""
監視設定 — ガンプラ再販在庫トラッカー（小額モデル検証 Q1 用）

目的: docs/07_minimal_validation.md の Q1「入手再現性」を実測するための入手監視。
      HG 1/144 ナイチンゲール（および追加で監視したい品）が、正規店で
      定価以下・在庫ありになった瞬間を検知して即通知し、争奪戦に参加できる状態を作る。

データ源の選定理由（事前検証で確定）:
  - ヨドバシ.com 直接監視 → Bot対策で自動取得不可（接続/WebFetchともに失敗）
  - GunplaDatabase（複数店舗集約サイト）→ 取得可能で、店舗別の在庫状態が
    `shop_status_container ... soldout` のCSSクラス有無で判定できる。
  → GunplaDatabase の商品個別ページを監視し、soldout でない店舗が現れたら在庫復活と判定。

在庫判定ロジック:
  ページ内の `shop_status_container` ブロックごとに class に `soldout` を含むか判定。
  soldout を含まない（=在庫あり/予約受付）店舗が1つでもあれば「在庫あり」。
  前回「在庫なし」→今回「在庫あり」に変化したら通知。
"""

# 監視対象（GunplaDatabaseの商品個別ページ）。複数登録可。
# no= は GunplaDatabase の商品ID。name は通知に出す表示名。
WATCH_ITEMS = [
    {
        "no": "2294",
        "name": "HG 1/144 ナイチンゲール",
        "retail_price": 7700,
        "url": "https://gunpla-database.doc-sin.life/?no=2294",
    },
    # 追加の監視品があればここに足す（同条件: 非酒類・非TCG・正規新品・未開封のまま売れる）
]

# GunplaDatabase 商品ページURLの組み立て用
GDB_ITEM_URL = "https://gunpla-database.doc-sin.life/?no={no}"

# 在庫切れを示すCSSクラスのマーカー。
# shop_status_container ブロックの属性にこの語があれば、その店舗は売り切れ。
SOLDOUT_MARKER = "soldout"

# 店舗ステータスブロックを切り出す正規表現の起点クラス
SHOP_BLOCK_CLASS = "shop_status_container"

# このサイトの文字コード
SITE_ENCODING = "utf-8"

# HTTP設定
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
REQUEST_INTERVAL = 1.5  # 商品ごとのリクエスト間隔（サイト負荷配慮）

# 前回在庫状態の保存ファイル（在庫なし→在庫ありの変化時のみ通知するため）
STATE_FILE = "stock_state.json"

# 通知本文に出す、検証の心得（争奪戦は数分で完売）
# ※Amazon等は転売価格の場合あり。通知後に「定価で買えるか」を必ず人間が確認する前提。
NOTE = (
    "再販/在庫を検知。数分で完売の可能性大。"
    "⚠️必ず価格を確認（定価7,700円か。Amazon等は転売価格の場合あり）。"
    "定価なら即購入→実勢12,000〜13,000円で売却検証。"
)
