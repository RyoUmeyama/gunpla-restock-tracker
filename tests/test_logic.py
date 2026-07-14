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
        self.assertTrue(cs._title_matches("ワンピースカードゲーム 新時代の主役 BOX【再販】"))
        self.assertTrue(cs._title_matches("遊戯王 RARITY COLLECTION BOX【再販】"))

    def test_non_card_onepiece_rejected(self):
        # カード商品限定: 素の「ワンピース」（フィギュア等）は拾わない（2026-07-10仕様）
        self.assertFalse(cs._title_matches("ワンピース ルフィ フィギュア ワーコレ"))
        self.assertFalse(cs._title_matches("ドラゴンボール 超サイヤ人フィギュア"))

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


class TestUpcomingDates(unittest.TestCase):
    """応募チャンス抽出の日付解決: 年なし日付の年跨ぎと各形式。"""

    def test_same_year(self):
        from datetime import date
        today = date(2026, 7, 7)
        self.assertIn(date(2026, 7, 15), cs._upcoming_dates("7月15日 抽選受付", today))

    def test_year_rollover(self):
        # 12月に見た「1月10日」は来年と解釈する
        from datetime import date
        today = date(2026, 12, 20)
        self.assertIn(date(2027, 1, 10), cs._upcoming_dates("1月10日まで受付", today))

    def test_recent_past_stays_this_year(self):
        from datetime import date
        today = date(2026, 7, 7)
        self.assertIn(date(2026, 7, 1), cs._upcoming_dates("7月1日から", today))

    def test_slash_format(self):
        from datetime import date
        today = date(2026, 7, 7)
        self.assertIn(date(2026, 8, 1), cs._upcoming_dates("2026/8/1 10:00〜", today))

    def test_invalid_date_ignored(self):
        from datetime import date
        self.assertEqual(cs._upcoming_dates("13月40日", date(2026, 7, 7)), [])


class TestExtractOpportunities(unittest.TestCase):
    """応募/予約チャンスダイジェスト: 未来日付つきの抽選行のみ拾う。"""

    def _state(self, lines):
        item = next(it for it in cs.config.WATCH_ITEMS
                    if it["method"] == "page_update" and "anime-matsuri" in it["url"])
        return item, {item["key"]: {"sig": "x", "lines": lines}}

    def test_future_lottery_line_included(self):
        from datetime import date
        today = date(2026, 7, 7)
        item, st = self._state(["【ヨドバシ】7月14日まで抽選受付中", "ただの本文"])
        opps = cs.extract_opportunities({}, st, today)
        self.assertEqual(len(opps), 1)
        self.assertIn("ヨドバシ", opps[0])

    def test_past_lottery_excluded(self):
        from datetime import date
        today = date(2026, 7, 7)
        item, st = self._state(["【ビックカメラ】5月10日まで抽選受付", "6月1日 応募終了"])
        self.assertEqual(cs.extract_opportunities({}, st, today), [])

    def test_no_keyword_excluded(self):
        from datetime import date
        today = date(2026, 7, 7)
        item, st = self._state(["7月20日 発売のカードリスト"])
        self.assertEqual(cs.extract_opportunities({}, st, today), [])

    def test_open_now_marker_without_date(self):
        # Amazon招待リクエスト等は日付なしでも「今応募できる」ので拾う
        from datetime import date
        today = date(2026, 7, 7)
        item, st = self._state(["2026年6月中旬頃から抽選予約開始Amazonで招待リクエスト(抽選)予約受付開始"])
        opps = cs.extract_opportunities({}, st, today)
        self.assertEqual(len(opps), 1)

    def test_stale_year_open_marker_excluded(self):
        # 過去の年に言及する「受付開始」履歴行は古いので拾わない
        from datetime import date
        today = date(2026, 7, 7)
        item, st = self._state(["2025年1月初旬頃から抽選予約開始Amazonで招待リクエスト(抽選)予約受付開始"])
        self.assertEqual(cs.extract_opportunities({}, st, today), [])

    def test_boilerplate_excluded(self):
        from datetime import date
        today = date(2026, 7, 7)
        item, st = self._state(["抽選応募や予約受付中・受付予定のストア一覧や応募条件等（7月20日）"])
        self.assertEqual(cs.extract_opportunities({}, st, today), [])

    def test_date_in_next_line(self):
        # 期間がテーブルの隣セル（次の行）にある構造でも拾う
        from datetime import date
        today = date(2026, 7, 7)
        item, st = self._state(["【ヨドバシ】抽選販売応募", "7月8日〜7月14日"])
        opps = cs.extract_opportunities({}, st, today)
        self.assertEqual(len(opps), 1)
        self.assertIn("7月8日", opps[0])

    def test_non_card_tag_excluded(self):
        # 【雑誌】等のカード以外商品タグは通知しない（週刊ジャンプ毎週通知の回帰テスト）
        from datetime import date
        today = date(2026, 7, 14)
        line = "アニメイトにて【雑誌】週刊少年ジャンプ 2026年7月27日号が販売継続中です"
        self.assertFalse(cs._is_actionable_line(line, today))
        self.assertFalse(cs._title_matches("【雑誌】週刊少年ジャンプ ワンピース特集号"))

    def test_magazine_with_card_appendix_notified(self):
        # 例外: カード付録つき雑誌（Vジャンプのプロモカード等）は購入対象（ユーザー指示）
        from datetime import date
        today = date(2026, 7, 14)
        self.assertTrue(cs._is_actionable_line(
            "【雑誌】Vジャンプ9月号 ワンピースカード プロモカード付録 予約受付", today))
        self.assertTrue(cs._title_matches("【雑誌】Vジャンプ 遊戯王プロモカード付録つき"))

    def test_status_quo_not_actionable(self):
        # 「販売継続中」は状態の継続でありチャンスではない
        from datetime import date
        self.assertFalse(cs._is_actionable_line(
            "ワンピースカード OP-17 BOXが販売継続中です", date(2026, 7, 14)))

    def test_card_goods_tag_still_passes(self):
        # カード商品タグ（【グッズ-カードゲーム】）は引き続き通知対象
        from datetime import date
        self.assertTrue(cs._is_actionable_line(
            "【グッズ-カードゲーム】ワンピースカード OP-17 BOXの抽選受付", date(2026, 7, 14)))


class TestStoreLinkResolution(unittest.TestCase):
    """遷移先ストアURLの解決とアフィリエイト剥がし。"""

    def test_unwrap_rakuten_affiliate(self):
        url = "https://hb.afl.rakuten.co.jp/hgc/xxx/?pc=https%3A%2F%2Fbooks.rakuten.co.jp%2Frb%2F123%2F&m=http%3A%2F%2Fexample"
        self.assertEqual(cs._unwrap_affiliate(url), "https://books.rakuten.co.jp/rb/123/")

    def test_unwrap_passthrough(self):
        self.assertEqual(cs._unwrap_affiliate("https://www.amazon.co.jp/dp/B0X"), "https://www.amazon.co.jp/dp/B0X")

    def test_resolve_prefers_store_domain(self):
        html = ('<tr><td>【ヨドバシ】7月10日 抽選受付開始</td>'
                '<td><a href="https://twitter.com/share">share</a>'
                '<a href="https://www.yodobashi.com/product/100000/">商品</a></td></tr>')
        link = cs.resolve_store_link(html, "【ヨドバシ】7月10日 抽選受付開始")
        self.assertEqual(link, "https://www.yodobashi.com/product/100000/")


    def test_unwrap_with_html_entity_escaped_params(self):
        # HTML内のhrefは&が&amp;になっている。resolve_store_link側で復元してから剥がす
        html = ('<td>【楽天ブックス】7月20日 再販予約</td>'
                '<a href="https://af.moshimo.com/af/c/click?a_id=1&amp;p_id=2&amp;'
                'url=https%3A%2F%2Fbooks.rakuten.co.jp%2Frb%2F999%2F">リンク</a>')
        link = cs.resolve_store_link(html, "【楽天ブックス】7月20日 再販予約")
        self.assertEqual(link, "https://books.rakuten.co.jp/rb/999/")


    def test_anchor_text_exact_match(self):
        # 行がリンクのアンカーテキストそのもの → そのhrefを確実に対応付ける
        html = ('<a href="https://www.amazon.co.jp/dp/AAA111/">'
                'ROBOT魂 ストライクガンダム ver. A.N.I.M.E. (再販版）</a>'
                '<a href="https://www.amazon.co.jp/dp/BBB222/">別商品 ガンダムリバティ</a>')
        link = cs.resolve_store_link(html, "ROBOT魂 ストライクガンダム ver. A.N.I.M.E. (再販版）")
        self.assertEqual(link, "https://www.amazon.co.jp/dp/AAA111/")

    def test_no_guess_from_neighbor_link(self):
        # 店舗名もアンカー一致もない行は、近くに他商品のリンクがあってもURLを付けない
        # （「実際のページでは関係ないものが表示される」誤リンク事故の回帰テスト）
        html = ('<p>HGUC 新商品 7月20日 再販予定</p>'
                '<a href="https://www.amazon.co.jp/dp/CCC333/">全く別の商品名リンク</a>')
        self.assertIsNone(cs.resolve_store_link(html, "HGUC 新商品 7月20日 再販予定"))

    def test_resolve_none_when_not_found(self):
        self.assertIsNone(cs.resolve_store_link("<p>無関係</p>", "【ヨドバシ】7月10日 抽選受付"))

    def test_article_store_link_by_hint(self):
        # RSS記事: タイトルの【Amazon】タグとドメイン一致するリンクだけ採用
        html = ('<a href="https://twitter.com/share">tw</a>'
                '<a href="https://af.moshimo.com/af/c/click?a_id=1&amp;url=https%3A%2F%2Fwww.amazon.co.jp%2Fdp%2FB0FS14%2F">Amazonで購入</a>')
        url = cs.resolve_store_link_from_article(html, "【Amazon】DBFW スタートデッキ FS14")
        self.assertEqual(url, "https://www.amazon.co.jp/dp/B0FS14/")

    def test_article_no_hint_returns_none(self):
        html = '<a href="https://www.amazon.co.jp/dp/X/">buy</a>'
        self.assertIsNone(cs.resolve_store_link_from_article(html, "店舗タグなしの商品"))


class TestActionableLine(unittest.TestCase):
    """通知価値の判定: 実質情報のみ通知。"""

    def test_restock_line_actionable(self):
        self.assertTrue(cs._is_actionable_line("【楽天ブックス】7月10日10時から再販予定"))

    def test_boilerplate_not_actionable(self):
        self.assertFalse(cs._is_actionable_line("抽選応募や予約受付中・受付予定のストア一覧まとめ"))

    def test_no_action_keyword_not_actionable(self):
        self.assertFalse(cs._is_actionable_line("新カードのイラストが公開されました"))


    def test_bare_date_line_not_actionable(self):
        # 日付だけで中身のない行（日付セル・期間セル単独）は通知しない
        # （「2026.7.10 →検索:amazon...k=2026.7.10」という無意味通知の回帰テスト）
        from datetime import date
        self.assertFalse(cs._is_actionable_line("2026年7月15日〜7月22日", date(2026, 7, 8)))
        self.assertFalse(cs._is_actionable_line("2026.7.10", date(2026, 7, 8)))

    def test_date_with_substance_actionable(self):
        # 日付＋商品名など中身のある行は行動語が無くても実質情報
        from datetime import date
        self.assertTrue(cs._is_actionable_line(
            "拡張パック ストームエメラルダ BOXが 7月31日（金）に登場", date(2026, 7, 8)))

    def test_deck_products_lifecycle_rules(self):
        # 商品ライフサイクル規則（2026-07-10 ユーザードメイン知識）:
        # スターターセット/構築デッキはポケカのみ初回販売だけ通知。再販は通知しない。
        from datetime import date
        today = date(2026, 7, 8)
        line_initial = "構築デッキ「スターターセットex」3種が、7月31日（金）に発売！"
        # ポケカ×初回販売（発売前後60日の日付あり）→ 通知する
        self.assertTrue(cs._is_actionable_line(line_initial, today, is_pokeca=True))
        # ポケカ以外 → 通知しない
        self.assertFalse(cs._is_actionable_line(line_initial, today, is_pokeca=False))
        # ポケカでも再販 → 通知しない
        self.assertFalse(cs._is_actionable_line(
            "スターターセットexの再販が7月31日に決定", today, is_pokeca=True))
        # ポケカでも初回販売期の日付がない → 通知しない
        self.assertFalse(cs._is_actionable_line(
            "構築デッキ「スターターセットex」好評発売中", today, is_pokeca=True))

    def test_start_deck_always_notified(self):
        # 例外: スタートデッキは再販でも人気 → 再販も通知する
        from datetime import date
        self.assertTrue(cs._is_actionable_line(
            "スタートデッキ100の再販が決定", date(2026, 7, 8), is_pokeca=True))

    def test_supply_always_excluded(self):
        from datetime import date
        self.assertFalse(cs._is_actionable_line(
            "デッキシールド ピカチュウが7月31日に発売", date(2026, 7, 8), is_pokeca=True))

    def test_expired_product_mention_excluded(self):
        # 発売から1年半超のポケカ商品への言及は除外（再販が来ないため）
        from datetime import date
        today = date(2026, 7, 10)
        officials = ["拡張パック「超電ブレイカー」|2024年10月11日（金）",
                     "拡張パック「アビスアイ」|2026年 5月22日（金）"]
        expired = cs._expired_pokeca_titles({}, {"pokecard_official": officials}, today)
        self.assertTrue(cs._mentions_expired("超電ブレイカーBOXの再販情報", expired))
        self.assertFalse(cs._mentions_expired("アビスアイBOXの再販情報", expired))

    def test_stale_date_line_not_actionable(self):
        from datetime import date
        self.assertFalse(cs._is_actionable_line("2025年1月10日〜1月17日", date(2026, 7, 8)))

    def test_too_short_not_actionable(self):
        self.assertFalse(cs._is_actionable_line("再販"))


class TestFallbackSearchUrl(unittest.TestCase):
    """検索URLフォールバック: 集約ページ頼みにしない通知の要。"""

    ITEM = {"name": "ポケカ アビスアイ 抽選/再販まとめ（anime-matsuri）"}

    def test_product_line_to_amazon_search(self):
        url = cs.fallback_search_url(
            "ROBOT魂 ＜SIDE MS＞ 機動戦士ガンダムSEED ストライクガンダム (再販版）", self.ITEM)
        self.assertIn("amazon.co.jp/s?k=", url)
        self.assertIn("ROBOT", url)
        self.assertNotIn("%E5%86%8D%E8%B2%A9", url)  # 「再販」はクエリから除去

    def test_store_hint_selects_store_search(self):
        url = cs.fallback_search_url("ヨドバシで7月20日から抽選受付", self.ITEM)
        self.assertIn("yodobashi.com", url)

    def test_short_line_uses_item_name(self):
        url = cs.fallback_search_url("抽選販売応募受け付け期間", self.ITEM)
        self.assertIn("amazon.co.jp/s?k=", url)
        from urllib.parse import unquote
        self.assertIn("アビスアイ", unquote(url))

    def test_query_strips_dot_dates_and_brackets(self):
        # 「2026.7.10」検索や「」・句読点・助詞のゴミが残らないこと（回帰テスト）
        from urllib.parse import unquote
        url = cs.fallback_search_url(
            "構築デッキ「スターターセットex」3種が、7月31日（金）に発売！", self.ITEM)
        q = unquote(url.split("k=")[1])
        self.assertNotIn("2026", q)
        self.assertNotIn("「", q)
        self.assertNotIn("、", q)
        self.assertNotIn("発売", q)
        self.assertIn("スターターセットex", q)

    def test_bare_dot_date_uses_item_name(self):
        from urllib.parse import unquote
        url = cs.fallback_search_url("2026.7.10", self.ITEM)
        self.assertIn("アビスアイ", unquote(url))


class TestConfigGuards(unittest.TestCase):
    """設定の整合性ガード: 追加時の付け忘れをCIで検出する。"""

    def test_single_product_items_have_release_date(self):
        # 単一商品の監視には必ず release_date（1年半自動失効・全商品共通規則）
        missing = []
        for it in cs.config.WATCH_ITEMS:
            m = it.get("method")
            single = (m in ("toei_stock_status", "rakuten_books")) or \
                     (m == "page_update" and "reservation-lottery" in it.get("url", ""))
            if single and not it.get("release_date"):
                missing.append(it["key"])
        self.assertEqual(missing, [], f"release_date未設定: {missing}")


class TestToeiSweepAgeRule(unittest.TestCase):
    """東映在庫スイープの1年半ルール。"""

    def test_old_box_not_notified(self):
        from datetime import date
        today = date(2026, 7, 13)
        boxes = {
            "OLD": {"name": "OP-06 双璧の覇者", "stockMsg": "○", "releaseDt": "2023/11/25"},
            "NEW": {"name": "OP-16 決戦の刻", "stockMsg": "○", "releaseDt": "2026/05/30"},
            "END": {"name": "OP-05", "stockMsg": "販売終了", "releaseDt": "2026/05/30"},
        }
        known = {g: {"stockMsg": "×"} for g in boxes}
        notify, old_only = cs._sweep_restocked(boxes, known, today)
        self.assertEqual([b["name"] for b in notify], ["OP-16 決戦の刻"])
        self.assertEqual([b["name"] for b in old_only], ["OP-06 双璧の覇者"])


class TestWeeklySummary(unittest.TestCase):
    """週次運用サマリ: 月曜のヘルスレポートで報告しリセット、他の曜日は蓄積のみ。"""

    def _run(self, weekday_date, stats):
        import datetime as _dt
        class FakeDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                d = weekday_date
                return cls(d.year, d.month, d.day, 10, 0, tzinfo=tz)
        orig = cs.datetime
        cs.datetime = FakeDT
        try:
            alerts, ns = [], {"weekly_stats": dict(stats)}
            cs.append_heartbeat({}, ns, alerts, {"ok": ["a"], "fail": [], "suppressed": 0})
            hb = [a for a in alerts if "ヘルス" in a[0]["name"]][0]
            return hb[1], ns["weekly_stats"]
        finally:
            cs.datetime = orig

    def test_monday_reports_and_resets(self):
        from datetime import date
        detail, stats = self._run(date(2026, 7, 20), {"notified": 7, "suppressed": 3, "chances": 2, "since": "2026-07-13"})
        self.assertIn("週次サマリ", detail)
        self.assertIn("通知7件", detail)
        self.assertIn("ノイズ抑制3件", detail)
        self.assertEqual(stats["notified"], 0)  # リセット

    def test_other_days_no_report(self):
        from datetime import date
        detail, stats = self._run(date(2026, 7, 21), {"notified": 7, "suppressed": 3, "chances": 2, "since": "2026-07-20"})
        self.assertNotIn("週次サマリ", detail)
        self.assertEqual(stats["notified"], 7)  # 蓄積維持


class TestProcessItemPageUpdate(unittest.TestCase):
    """_process_item page_update の状態遷移（初回/変化あり/抑制/取得失敗）。"""

    ITEM = {"name": "ポケカ テスト 抽選/再販まとめ（anime-matsuri）", "method": "page_update",
            "url": "https://anime-matsuri.com/test-reservation-lottery/", "retail_price": 5400,
            "key": "test_pu"}

    def _run(self, sig_result, prev):
        orig = cs.compute_page_signature
        cs.compute_page_signature = lambda item: sig_result
        orig_sleep = cs.time.sleep
        cs.time.sleep = lambda s: None
        try:
            new_state, alerts, health = {}, [], {"ok": [], "fail": [], "suppressed": 0}
            cs._process_item(self.ITEM, prev, new_state, alerts, health)
            return new_state, alerts, health
        finally:
            cs.compute_page_signature = orig
            cs.time.sleep = orig_sleep

    def test_first_seen_records_baseline_no_alert(self):
        ns, alerts, h = self._run(("sig1", ["抽選受付 7月20日"], True, "<html>"), {})
        self.assertEqual(ns["test_pu"]["sig"], "sig1")
        self.assertEqual(alerts, [])
        self.assertIn(self.ITEM["name"], h["ok"])

    def test_actionable_change_alerts(self):
        prev = {"test_pu": {"sig": "old", "lines": [], "links": {}}}
        ns, alerts, h = self._run(("new", ["【ヨドバシ】抽選受付開始 7月20日まで"], True, "<html>"), prev)
        self.assertEqual(len(alerts), 1)
        self.assertIn("ヨドバシ", alerts[0][1])
        self.assertIn("→", alerts[0][1])  # リンク（検索フォールバック含む）付き

    def test_noise_change_suppressed(self):
        prev = {"test_pu": {"sig": "old", "lines": [], "links": {}}}
        ns, alerts, h = self._run(("new", ["ただの本文更新です"], True, "<html>"), prev)
        self.assertEqual(alerts, [])
        self.assertEqual(h["suppressed"], 1)

    def test_fetch_failure_keeps_prev_state(self):
        prev = {"test_pu": {"sig": "old", "lines": ["a"], "links": {}}}
        ns, alerts, h = self._run((None, None, False, None), prev)
        self.assertEqual(ns["test_pu"], prev["test_pu"])
        self.assertEqual([n for n, _ in h["fail"]], [self.ITEM["name"]])


class TestProcessItemStock(unittest.TestCase):
    """在庫系（toei_stock_status）の遷移: 復活で通知・継続では沈黙。"""

    ITEM = {"name": "テストBOX（在庫）", "method": "toei_stock_status",
            "url": "https://store.toei-anim.co.jp/shop/g/gTEST/", "retail_price": 5280, "key": "t_stock"}

    def _run(self, in_stock, prev):
        orig = cs.check_item
        cs.check_item = lambda item: (in_stock, True, "stub")
        orig_sleep = cs.time.sleep
        cs.time.sleep = lambda s: None
        try:
            new_state, alerts, health = {}, [], {"ok": [], "fail": [], "suppressed": 0}
            cs._process_item(self.ITEM, prev, new_state, alerts, health)
            return new_state, alerts
        finally:
            cs.check_item = orig
            cs.time.sleep = orig_sleep

    def test_restock_alerts(self):
        ns, alerts = self._run(True, {"t_stock": False})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][2], "stock")

    def test_still_in_stock_silent(self):
        ns, alerts = self._run(True, {"t_stock": True})
        self.assertEqual(alerts, [])

    def test_first_seen_silent(self):
        ns, alerts = self._run(True, {})
        self.assertEqual(alerts, [])


class TestAmLotteryPageDiscovery(unittest.TestCase):
    """anime-matsuri 新規抽選まとめページの自動発見。"""

    def test_filters_slug_and_title(self):
        posts = [
            {"slug": "pokemoncard-new-reservation-lottery", "link": "https://anime-matsuri.com/a/",
             "title": {"rendered": "ポケモンカード 新弾の抽選予約まとめ"}},
            {"slug": "pokemoncard-atari-list", "link": "https://anime-matsuri.com/b/",
             "title": {"rendered": "ポケモンカード 当たりカードまとめ"}},  # slug不一致
            {"slug": "unionarena-x-reservation-lottery", "link": "https://anime-matsuri.com/c/",
             "title": {"rendered": "ユニオンアリーナの抽選予約まとめ"}},  # 対象外タイトル
        ]
        class R:
            def json(self):
                return posts
        orig = cs.http_get
        cs.http_get = lambda url, **kw: R()
        try:
            pages, ok = cs.discover_am_lottery_pages()
        finally:
            cs.http_get = orig
        self.assertTrue(ok)
        self.assertEqual(list(pages), ["pokemoncard-new-reservation-lottery"])


if __name__ == "__main__":
    unittest.main()
