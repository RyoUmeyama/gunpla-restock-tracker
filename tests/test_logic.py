#!/usr/bin/env python3
"""純粋ロジックの単体テスト（ネットワークアクセスなし・CIで実行）。

サイトへの実フェッチを伴う部分は対象外（実挙動はローカル/本番の実行ログで確認する）。
実行: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_stock as cs
import webhook_utils as wu


class TestTitleMatches(unittest.TestCase):
    """RSS発見器のタイトル選別: ポケカは別格で広く、他はサプライ除外。"""

    def test_supply_excluded_for_non_pokeca(self):
        self.assertFalse(cs._title_matches("ワンピースカードゲーム スリーブ ルフィ"))
        self.assertFalse(cs._title_matches("遊戯王 デュエリストカードプロテクター(スリーブ)"))
        self.assertFalse(cs._title_matches("ドラゴンボール フュージョンワールド プレイマット"))

    def test_box_products_pass(self):
        self.assertTrue(cs._title_matches("ワンピース 一緒に学べるデッキセット(LT-01)"))
        self.assertTrue(cs._title_matches("遊戯王 RARITY COLLECTION BOX【再販】"))

    def test_pokeca_is_exception(self):
        # ポケカ関連はサプライでも拾う（方針: 関連全部を定価なら狙う）
        self.assertTrue(cs._title_matches("ポケモンカード デッキシールド ピカチュウ"))

    def test_unwatched_keyword_rejected(self):
        self.assertFalse(cs._title_matches("ガンダムベース限定 HG ガンプラ"))


class TestPassesProfit(unittest.TestCase):
    """相場選別: spread閾値とポケカ別格。"""

    def test_active_when_spread_high(self):
        self.assertEqual(cs.passes_profit(5000, 10000, False), "active")

    def test_dropped_when_below_retail(self):
        self.assertEqual(cs.passes_profit(5000, 5000, False), "dropped")

    def test_unknown_in_middle_band(self):
        # spread 1.1 は IN(1.25) 未満・OUT(1.05) 以上 → 監視継続(unknown)
        self.assertEqual(cs.passes_profit(5000, 5500, False), "unknown")

    def test_pokeca_active_at_retail_plus(self):
        # ポケカは閾値1.0: 定価超で手数料後も黒字なら active
        self.assertEqual(cs.passes_profit(5000, 6000, True), "active")

    def test_invalid_inputs_unknown(self):
        self.assertEqual(cs.passes_profit(0, 10000, False), "unknown")
        self.assertEqual(cs.passes_profit(5000, None, False), "unknown")


class TestAltemaMatch(unittest.TestCase):
    """altema相場辞書との銘柄名照合。"""

    def test_exact_match_wins(self):
        prices = {"テラスタルフェスex": 19000, "テラスタルフェスex 2BOXセット": 40000}
        self.assertEqual(cs.match_altema_price("ポケカ テラスタルフェスex 再販集約", prices), 19000)

    def test_shortest_candidate_for_partial(self):
        prices = {"ホワイトフレア＋おまけ付き限定セット": 30000, "ホワイトフレアBOX": 17000}
        self.assertEqual(cs.match_altema_price("ポケカ ホワイトフレア", prices), 17000)

    def test_short_core_not_matched(self):
        self.assertIsNone(cs.match_altema_price("BOX", {"何か": 1000}))


class TestRakutenParse(unittest.TestCase):
    """楽天ブックス在庫判定: 定価近傍のみ在庫あり。"""

    def setUp(self):
        os.environ["RAKUTEN_APP_ID"] = "dummy"
        self._orig = cs.http_get
        self.item = {"name": "t", "keyword": "kw", "retail_price": 5500}

    def tearDown(self):
        cs.http_get = self._orig
        del os.environ["RAKUTEN_APP_ID"]

    def _stub(self, data):
        class R:
            def json(self_inner):
                return data
        cs.http_get = lambda url, **kw: R()

    def test_retail_price_hit(self):
        self._stub({"Items": [{"Item": {"itemName": "BOX", "itemPrice": 5500, "itemUrl": "u"}}]})
        in_stock, ok, detail = cs._check_rakuten_books(self.item)
        self.assertTrue(ok)
        self.assertTrue(in_stock)

    def test_scalper_price_rejected(self):
        self._stub({"Items": [{"Item": {"itemName": "BOX", "itemPrice": 14800, "itemUrl": "u"}}]})
        in_stock, ok, _ = cs._check_rakuten_books(self.item)
        self.assertTrue(ok)
        self.assertFalse(in_stock)

    def test_skip_without_app_id(self):
        del os.environ["RAKUTEN_APP_ID"]
        os.environ["RAKUTEN_APP_ID"] = ""
        in_stock, ok, _ = cs._check_rakuten_books(self.item)
        self.assertFalse(ok)  # 判定不能=前回維持


class TestBuildMessages(unittest.TestCase):
    """通知文面: 件名に商品名、在庫系が先頭。"""

    def test_subject_contains_item_name(self):
        item = {"name": "テスト商品", "url": "https://example.com", "retail_price": 5280}
        subject, text, html, wt, wl = cs.build_messages([(item, "詳細", "stock")])
        self.assertIn("テスト商品", subject)
        self.assertIn("在庫検知", subject)

    def test_stock_sorted_first(self):
        info = ({"name": "お知らせ品", "url": "u1", "retail_price": 0}, "d1", "info")
        stock = ({"name": "在庫品", "url": "u2", "retail_price": 0}, "d2", "stock")
        subject, *_ = cs.build_messages([info, stock])
        self.assertIn("在庫品", subject)


class TestSplitChunks(unittest.TestCase):
    """Discord 2000字制限対策の行境界分割。"""

    def test_short_body_single_chunk(self):
        self.assertEqual(wu._split_chunks("a\nb", 1900), ["a\nb"])

    def test_split_on_line_boundary(self):
        body = "\n".join(["x" * 100] * 30)  # 3029字
        chunks = wu._split_chunks(body, 1900)
        self.assertEqual(len(chunks), 2)
        for c in chunks:
            self.assertLessEqual(len(c), 1900)
            self.assertFalse(c.startswith("\n"))

    def test_max_chunks_truncated(self):
        body = "\n".join(["y" * 100] * 100)
        chunks = wu._split_chunks(body, 1900, max_chunks=3)
        self.assertEqual(len(chunks), 3)
        self.assertIn("省略", chunks[-1])

    def test_overlong_single_line(self):
        chunks = wu._split_chunks("z" * 4000, 1900)
        self.assertTrue(all(len(c) <= 1900 for c in chunks))


if __name__ == "__main__":
    unittest.main()
