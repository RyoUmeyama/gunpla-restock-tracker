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
    # ※ガンプラ監視（HGナイチンゲール/ガンプラ横断集約）は2026-07-10にユーザー指示で
    #   通知対象から除外。戦略の主軸はTCG（ポケカ別格）のため。
    {
        # 網羅スクリーニング(docs/08)の本命。唯一 入手容易さ=medium。
        # 東映アニメ公式の正規・定価¥5,280ルートを監視。stock_status で在庫判定。
        "name": "ワンピカード OP-16 決戦の刻 BOX",
        "method": "toei_stock_status",
        "url": "https://store.toei-anim.co.jp/shop/g/gONP03841O1/",
        "retail_price": 5280,
        "release_date": "2026-05-30",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "op16_kessen",
    },
    {
        # 【C: 再販告知の監視】OP-16の再販入荷情報まとめ（40社以上集約）。
        # 在庫の「瞬間」ではなく再販告知の「更新」を検知する。間引きに強い保険。
        "name": "OP-16 再販告知まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/onepiececard-kessennokoku-op16-reservation-lottery/",
        "retail_price": 5280,
        "release_date": "2026-05-30",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "op16_restock_news",
    },
    # ===== 2026-06-22 多数監視に拡張（docs/10_multi_watch_sources.md で実地検証）=====
    # 設計判断: 個別在庫判定(駿河屋検索/カードラッシュ/コトブキヤ)は実地で不安定=誤検知リスク
    # と判明したため、確実な個別在庫判定(東映公式・GunplaDatabase)＋集約ページ更新検知
    # (nyuka-now の page_update)に一本化。誤検知ゼロで在庫変動・再販告知を広く拾う。

    # --- 個別在庫判定（東映stock_status。実地検証で安定・確実）---
    # 商品コードは東映ストアのLightningSearch API(DType=Genre)で全件取得・確定済み(2026-06-23)。
    {
        "name": "DBFW MANGA BOOSTER 02 [SB02] BOX（在庫）",
        "method": "toei_stock_status",
        "url": "https://store.toei-anim.co.jp/shop/g/gDBS00124O1/",
        "retail_price": 7920,
        "release_date": "2025-11-08",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "dbfw_sb02_stock",
    },
    {
        "name": "DBFW MANGA BOOSTER 01 [SB01] BOX（在庫）",
        "method": "toei_stock_status",
        "url": "https://store.toei-anim.co.jp/shop/g/gDBS00120O1/",
        "retail_price": 7920,
        "release_date": "2025-06-28",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "dbfw_sb01_stock",
    },
    {
        # OP-10「王族の血統」=プレミア化(price-base買取9,500円)。RSS発見器が再販記事を検知した本体。
        "name": "ワンピ OP-10 王族の血統 BOX（在庫）",
        "method": "toei_stock_status",
        "url": "https://store.toei-anim.co.jp/shop/g/gONP01938O1/",
        "retail_price": 5280,
        "price_url": "https://price-base.com/useful/ouzokunokettou-box-market",
        "release_date": "2024-11-30",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "onepiece_op10_stock",
    },
    {
        "name": "ワンピ OP-15 神の島の冒険 BOX（在庫）",
        "method": "toei_stock_status",
        "url": "https://store.toei-anim.co.jp/shop/g/gONP03128O1/",
        "retail_price": 5280,
        "release_date": "2026-02-28",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "onepiece_op15_stock",
    },
    # --- 楽天ブックス在庫（公式API・要RAKUTEN_APP_ID）---
    # 楽天ブックスは定価販売の主要正規ルート。公式APIなのでブロックの心配がなく、
    # 「定価×1.05以下の在庫あり」だけを検知する＝通知が来た時点で定価で買える可能性が高い。
    # RAKUTEN_APP_ID（GitHub Secrets / .env）を設定すると自動で有効になる。未設定ならスキップ。
    {
        "name": "ポケカ MEGAドリームex BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード ハイクラスパック MEGAドリームex BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5500,
        "release_date": "2025-11-28",  # 発売日（1年半で自動失効）
        "key": "rakuten_megadream",
    },
    {
        "name": "ポケカ テラスタルフェスex BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード ハイクラスパック テラスタルフェスex BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5500,
        "release_date": "2024-12-06",  # 発売日（1年半で自動失効）
        "key": "rakuten_terastal",
    },
    {
        "name": "ポケカ ホワイトフレア BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 拡張パック ホワイトフレア BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5800,
        "release_date": "2025-06-06",  # 発売日（1年半で自動失効）
        "key": "rakuten_whiteflare",
    },
    {
        "name": "ポケカ ブラックボルト BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 拡張パック ブラックボルト BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5800,
        "release_date": "2025-06-06",  # 発売日（1年半で自動失効）
        "key": "rakuten_blackbolt",
    },
    {
        "name": "ワンピ OP-17 世界最強の戦士 BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ワンピースカードゲーム 世界最強の戦士 BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5280,
        "release_date": "2026-08-22",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "rakuten_op17",
    },
    {
        "name": "ポケカ ストームエメラルダ BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 拡張パック ストームエメラルダ BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 6000,
        "release_date": "2026-07-31",  # 発売日（1年半で自動失効）
        "key": "rakuten_stormemerald",
    },
    {
        "name": "ポケカ アビスアイ BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 拡張パック アビスアイ BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 6000,
        "release_date": "2026-05-22",  # 発売日（1年半で自動失効）
        "key": "rakuten_abysseye",
    },
    {
        "name": "ポケカ ロケット団の栄光 BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 拡張パック ロケット団の栄光 BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5400,
        "release_date": "2025-04-18",  # 発売日（1年半で自動失効）
        "key": "rakuten_rocket",
    },
    {
        "name": "ポケカ 超電ブレイカー BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 拡張パック 超電ブレイカー BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5400,
        "release_date": "2024-10-11",  # 発売日（1年半で自動失効）
        "key": "rakuten_tyoden",
    },
    {
        "name": "ポケカ インフェルノX BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 拡張パック インフェルノX BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5400,
        "release_date": "2025-09-26",  # 発売日（1年半で自動失効）
        "key": "rakuten_inferno",
    },
    {
        "name": "ポケカ 熱風のアリーナ BOX（楽天ブックス）",
        "method": "rakuten_books",
        "keyword": "ポケモンカード 強化拡張パック 熱風のアリーナ BOX",
        "url": "https://books.rakuten.co.jp/",
        "retail_price": 5400,
        "release_date": "2025-03-14",  # 発売日（1年半で自動失効）
        "key": "rakuten_neppu",
    },
    {
        # ワンピ公式ニュース（新商品/コラボ/イベント/抽選）。JSON APIの新着記事差分で検知。
        # ユーザー要望(2026-07-10): ポケカ・ワンピはコラボや新発売のニュースだけでも取得したい。
        "name": "ワンピ公式 ニュース（新商品/コラボ/イベント）",
        "method": "onepiece_news",
        "url": "https://onepiece-cardgame.com/news/",
        "retail_price": 0,
        "key": "onepiece_news",
    },
    # --- 集約ページ更新検知（page_update・anime-matsuri）---
    # 【2026-07-03 クラウド主戦化】nyuka-nowはクラウドIPを遮断しておりGitHub Actionsから
    # 取得不能。anime-matsuriの「抽選予約・先着販売・再販入荷まとめ」ページは同種の集約情報で
    # クラウド到達可（OP-16ページで実績済み）のため、主要銘柄はこちらを主監視にする。
    {
        "name": "ポケカ テラスタルフェスex 抽選/再販まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-terracetal-fes-ex-reservation-lottery/",
        "retail_price": 5500,
        "release_date": "2024-12-06",  # 発売日（1年半で自動失効）
        "key": "pokeca_terastal_am",
    },
    {
        "name": "ポケカ ホワイトフレア 抽選/再販まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-whiteflare-reservation-lottery-sv11w/",
        "retail_price": 5800,
        "release_date": "2025-06-06",  # 発売日（1年半で自動失効）
        "key": "pokeca_whiteflare_am",
    },
    {
        "name": "ポケカ ブラックボルト 抽選/再販まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-blackbolt-reservation-lottery-sv11b/",
        "retail_price": 5800,
        "release_date": "2025-06-06",  # 発売日（1年半で自動失効）
        "key": "pokeca_blackbolt_am",
    },
    {
        # ハイクラスパック（2025-11発売・プレミア持続中）。ポケカ別格方針で追加。
        "name": "ポケカ MEGAドリームex 抽選/再販まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-mega-dream-ex-reservation-lottery/",
        "retail_price": 5500,
        "release_date": "2025-11-28",  # 発売日（1年半で自動失効）
        "key": "pokeca_megadream_am",
    },
    {
        # ワンピ最新弾（第17弾）。東映ストア掲載時は新弾発見機能が自動通知→在庫監視を追加する。
        "name": "ワンピ OP-17 世界最強の戦士 抽選/再販まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/onepiececard-sekaisaikyonosenshi-op17-reservation-lottery/",
        "retail_price": 5280,
        "release_date": "2026-08-22",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "onepiece_op17_am",
    },
    # --- 2026-07-07 相場データ駆動の監視拡大 ---
    # altema買取相場で「買取が定価の1.3〜5.2倍」の現行弾が9銘柄未監視だったため一括追加。
    # いずれもanime-matsuriの抽選/再販まとめページ（クラウド到達可・差分つき通知）。
    # 定価はポケカ公式APIのパック単価×BOX入数から算出。
    {
        "name": "ポケカ アビスアイ 抽選/再販まとめ（anime-matsuri）",  # 買取14,000/定価6,000。docs/16の現在の本命
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-abyss-eye-reservation-lottery/",
        "retail_price": 6000,
        "release_date": "2026-05-22",  # 発売日（1年半で自動失効）
        "key": "pokeca_abysseye_am",
    },
    {
        "name": "ポケカ 超電ブレイカー 抽選/再販まとめ（anime-matsuri）",  # 買取28,000/定価5,400=5.2倍
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-tyodenbraker-reservation-lottery/",
        "retail_price": 5400,
        "release_date": "2024-10-11",  # 発売日（1年半で自動失効）
        "key": "pokeca_tyoden_am",
    },
    {
        "name": "ポケカ ロケット団の栄光 抽選/再販まとめ（anime-matsuri）",  # 買取22,000/定価5,400
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-rokettodan-no-eiko-reservation-lottery/",
        "retail_price": 5400,
        "release_date": "2025-04-18",  # 発売日（1年半で自動失効）
        "key": "pokeca_rocket_am",
    },
    {
        "name": "ポケカ 熱風のアリーナ 抽選/再販まとめ（anime-matsuri）",  # 買取18,000/定価5,400
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-neppunoarina-enhancement-reservation-lottery/",
        "retail_price": 5400,
        "release_date": "2025-03-14",  # 発売日（1年半で自動失効）
        "key": "pokeca_neppu_am",
    },
    {
        "name": "ポケカ インフェルノX 抽選/再販まとめ（anime-matsuri）",  # 買取17,000/定価5,400
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-infernox-enhancement-reservation-lottery/",
        "retail_price": 5400,
        "release_date": "2025-09-26",  # 発売日（1年半で自動失効）
        "key": "pokeca_inferno_am",
    },
    {
        "name": "ポケカ ニンジャスピナー 抽選/再販まとめ（anime-matsuri）",  # 買取9,500/定価5,400
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-ninja-spinner-m4-reservation-lottery/",
        "retail_price": 5400,
        "release_date": "2026-03-13",  # 発売日（1年半で自動失効）
        "key": "pokeca_ninja_am",
    },
    {
        "name": "ポケカ ムニキスゼロ 抽選/再販まとめ（anime-matsuri）",  # 買取7,000/定価5,400
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-munikisuzero-reservation-lottery/",
        "retail_price": 5400,
        "release_date": "2026-01-23",  # 発売日（1年半で自動失効）
        "key": "pokeca_munikisu_am",
    },
    {
        "name": "ポケカ バトルパートナーズ 抽選/再販まとめ（anime-matsuri）",  # 買取9,000/定価5,400
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-battle-partners-reservation-lottery/",
        "retail_price": 5400,
        "release_date": "2025-01-24",  # 発売日（1年半で自動失効）
        "key": "pokeca_batpart_am",
    },
    {
        # 【最優先】2026-07-31発売の最新拡張パック。予約・抽選戦線が今まさに進行中で、
        # 「入手機会が今ある」度が最も高い。発売前後の抽選/先着/再販を全部拾う。
        "name": "ポケカ ストームエメラルダ 抽選/予約まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-mega-storm-emerald-reservation-lottery/",
        "retail_price": 6000,
        "release_date": "2026-07-31",  # 発売日（1年半で自動失効）
        "key": "pokeca_stormemerald_am",
    },
    {
        # 30周年記念（2026-09/10発売）。予約・抽選戦線が進行中の最重要新弾。
        # FUTURISTIC BOX(定価27,500)等の高額セットも同ページで拾う。
        "name": "ポケカ 30th CELEBRATION 抽選/予約まとめ（anime-matsuri）",
        "method": "page_update",
        "url": "https://anime-matsuri.com/pokemoncard-mega-30th-celebration-card-set-reservation-lottery/",
        "retail_price": 10800,
        "release_date": "2026-09-16",  # 発売日（1年半で自動失効）
        "key": "pokeca_30th_am",
    },
    # --- 集約ページ更新検知（page_update・nyuka-now）---
    # ⚠ nyuka-nowはクラウドIP遮断のためGitHub Actionsからは常時「判定不能」になる。
    #   平日にローカルMacで実行したときだけ機能するボーナス層（サーキットブレーカーで
    #   クラウド実行時のコストはほぼゼロ）。anime-matsuriに専用ページが無い銘柄
    #   （DBFW SB01/FB10・遊戯王WPP7/RC04・コナンCT-P09）のカバーだけをここに残す。
    #   ※ポケカ3ページ(テラスタル/ホワイトフレア/ブラックボルト)はanime-matsuri版と
    #     完全重複のため2026-07-08に削除（クラウドでは常時取得不能だった）。
    {
        "name": "DBFW 在庫・再販集約（横断）",
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
        "price_url": "https://price-base.com/useful/mangabooster01-box-market",
        "release_date": "2025-06-28",  # 発売日（1年半で自動失効・全商品共通規則）
        "key": "dbfw_sb01_news",
    },
    {
        "name": "ワンピTCG 在庫・再販集約（横断）",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/97073",
        "retail_price": 5280,
        # OP-16「決戦の刻」相場。slugはkessen(kessennotokiでない)。
        "price_url": "https://price-base.com/useful/kessen-box-market",
        "key": "onepiece_cross_news",
    },
    {
        "name": "遊戯王 在庫・再販集約（横断）",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/24047",
        "retail_price": 2970,
        "key": "yugioh_wpp7_news",
    },
    {
        "name": "遊戯王 レアコレシリーズ 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/134935",
        "retail_price": 5280,
        "key": "yugioh_rc04_news",
    },
    {
        # 現行弾 CT-P09「疾風の煌めき」(定価5,544円)。price-base相場6,100円。
        "name": "名探偵コナンTCG 再販集約",
        "method": "page_update",
        "url": "https://nyuka-now.com/archives/143009",
        "retail_price": 5544,
        "price_url": "https://price-base.com/useful/shippunokirameki-box-market",
        "key": "conan_tcg_news",
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
    # ※ポケセン トウキョーDX 店頭ニュースは2026-07-08に監視除外。
    #   実査の結果、静的HTMLから取れるのは5/22付の古い見出し2行のみで6週間以上無変化＝
    #   実質何も監視できていないゾンビだった（本体はJSレンダリング）。
    #   都内店頭・30th抽選の告知はポケカ公式info＋anime-matsuri 30thページでカバーする。
    # ※メガトウキョー店頭ニュース(pokemoncenter-megatokyo/news/)はJSレンダリングで
    #   本文が取得できず常時「抽出0行・判定不能」だったため監視から除外(2026-07-02)。
    #   トウキョーDX側は静的HTMLに見出しが出るため監視継続（販売方法告知は全店共通が多い）。
    # --- ポケカ コラボ・プロモ・グッズ 公式info一覧（ポケカ全方位）---
    {
        # 発売告知も通知対象（ポケカの初回販売は転売機会。日付単独行・サプライ・
        # 再販不能な旧商品は _is_actionable_line 側の規則で除外される）。
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
# page_update の差分通知: stateに保持する抽出行の上限（肥大防止）と、通知に載せる新規行数
# 通知する価値がある「実質的な情報」の判定語。追加行がこれを含む場合だけ通知する
# （ボイラープレートの変化・行の削除だけの更新は通知しない＝正確な情報のみ通知）。
NOTIFY_ACTION_KEYWORDS = ["再販", "入荷", "抽選", "予約", "受付", "先着", "販売", "在庫", "整理券", "受注",
                          # ポケカ/ワンピはコラボ・新発売などのニュースも取得したい（ユーザー要望 2026-07-10）
                          "コラボ", "限定", "プロモ", "発売"]
# --- 商品ライフサイクル規則（2026-07-10 ユーザーのドメイン知識を反映）---
# サプライ類: 常に通知しない（転売妙味なし）。
# ※「カードセット」は30th CELEBRATIONカードセット等のプレミア商品があるため除外しない。
SUPPLY_NOISE_KEYWORDS = [
    "デッキケース", "デッキシールド", "プレイマット", "ラバーマット",
    "カードファイル", "バインダー", "スリーブ", "ダメカン", "コインセット",
]
# スターターセット/構築デッキ系: ポケカに限り「初回販売のみ」転売可能性あり。
# 再販は通知しない。ポケカ以外は常に通知しない。
DECK_PRODUCT_KEYWORDS = ["スターターセット", "構築デッキ", "デッキビルド"]
# 例外: スタートデッキ（スタートデッキ100等）は再販でも人気のため常に通知対象。
# → どのリストにも入れない。
# 初回販売とみなす発売日ウィンドウ（発売のこの日数前後の告知を初回販売期とみなす）
INITIAL_SALE_WINDOW_DAYS = 60
# ポケカ商品は発売から約1年半経過後は再販されないため、以降の情報は無視する。
# 監視アイテムの自動失効（release_date指定）・通知行の除外・監視候補提案の除外に共通適用。
MAX_PRODUCT_AGE_DAYS = 548  # ≒1年半

# 遷移先ストアURLとして認めるドメイン（追加行の近傍リンクから抽出。これ以外は載せない＝
# Googleフォーム・SNS等のノイズURL混入防止）
STORE_DOMAINS = [
    "amazon.co.jp", "amzn.to", "rakuten.co.jp", "yodobashi.com", "animate", "amiami",
    "biccamera", "yamada", "7net", "omni7", "hmv", "tsutaya", "store.toei-anim",
    "pokemon", "hobby", "joshin", "edion", "lawson", "aeon", "toysrus", "surugaya",
]
# 確実な直リンクが取れない行に付ける「ストア検索URL」のテンプレート。
# 集約ページのURLだけでは行動につながらないため、商品名での検索結果に直接飛ばす。
# 行に店舗名があればそのストアの検索、なければAmazon検索を既定にする。
SEARCH_URL_TEMPLATES = {
    "amazon": "https://www.amazon.co.jp/s?k={q}",
    "rakuten": "https://search.rakuten.co.jp/search/mall/{q}/",
    "yodobashi": "https://www.yodobashi.com/?word={q}",
    "pokemoncenter": "https://www.pokemoncenter-online.com/search/?q={q}",
    "amiami": "https://www.amiami.jp/top/search/list?s_keywords={q}",
    "animate": "https://www.animate-onlineshop.jp/products/list.php?mode=search&smt={q}",
    "surugaya": "https://www.suruga-ya.jp/search?search_word={q}",
}
# 店舗名ヒント→検索テンプレートのキー
STORE_SEARCH_KEY = {
    "Amazon": "amazon", "アマゾン": "amazon", "楽天": "rakuten", "ヨドバシ": "yodobashi",
    "ポケモンセンター": "pokemoncenter", "ポケセン": "pokemoncenter",
    "あみあみ": "amiami", "アニメイト": "animate", "駿河屋": "surugaya",
}

# 東映在庫スイープで「×からの変化」でも在庫復活とみなさない値（誤報ガード）。
# 例: ×→販売終了 は入荷ではない。
TOEI_SWEEP_IGNORE = ["終了", "未定", "取扱なし", "取り扱いなし"]

# 行内の店舗名→URLドメインの対応。行に店舗名があるときはドメインが一致するリンクだけを
# 採用する（隣の行のリンクを誤って拾う「ズレ」の防止）。
STORE_NAME_HINTS = {
    "Amazon": ["amazon", "amzn"], "アマゾン": ["amazon", "amzn"],
    "楽天": ["rakuten"], "ヨドバシ": ["yodobashi"],
    "ポケモンセンター": ["pokemoncenter"], "ポケセン": ["pokemoncenter"],
    "あみあみ": ["amiami"], "アニメイト": ["animate"], "駿河屋": ["surugaya"],
    "ビックカメラ": ["biccamera"], "ヤマダ": ["yamada"], "セブン": ["7net", "omni7"],
    "HMV": ["hmv"], "TSUTAYA": ["tsutaya"], "東映": ["toei-anim"],
    "ローソン": ["lawson"], "トイザらス": ["toysrus"], "ジョーシン": ["joshin"],
    "エディオン": ["edion"], "ホビーサーチ": ["hobbysearch"],
}
PAGE_LINES_KEEP = 400    # stateに保存する行数上限
DIFF_LINES_SHOWN = 6     # 通知本文に載せる新規行の最大数
DIFF_LINE_MAXLEN = 90    # 1行の最大表示文字数（単語の途中で切れにくいよう拡大）

# --- pokecard_official_list 方式（ポケカ公式API 新弾検知）の設定 ---
# resultAPI.php の4カテゴリ。新弾は (productTitle, releaseDate) のセット差分で検知する。
# productType の正しい値は下記4つのみ（deck/other等は無効値で全件返すサイレント故障の罠）。
POKECARD_PRODUCT_TYPES = ["expansion", "construction", "others", "peripheral"]
# 新弾候補とする発売年の下限は check_stock.py の _this_year()-1 で動的に決まる
# （年が変わっても自動追従するため固定年定数は持たない）。

# --- Phase2: RSS発見器（nyuka-now instockフィードで新弾・再販を自動発見）---
# 規約配慮: nyuka-nowは転売目的利用を規約で禁止→低頻度・最小フィードに留める。
# instock限定フィードはホビーTCG限定でクリーン（メインfeedは全ジャンル混在）。
FEED_URLS = [
    "https://nyuka-now.com/archives/category/instock/feed",
]
# 発見対象キーワード。ポケカは別格＝関連語を広く。
# カードゲーム商品限定（素の「ワンピース」「ドラゴンボール」「名探偵コナン」は
# フィギュア・マンガ等のカード以外も拾ってしまうため使わない。2026-07-10）
WATCH_KEYWORDS = [
    "ポケモンカード", "ポケカ", "ワンピースカード", "ワンピカード",
    "遊戯王", "ドラゴンボールスーパーカード", "DBFW", "フュージョンワールド",
    "名探偵コナンTCG", "コナンTCG", "コナンカード",
]
# ポケカ判定語（これを含むタイトルは別格＝除外フィルタをかけず広く拾う）
POKECA_TITLE_KEYWORDS = ["ポケモンカード", "ポケカ", "ポケモン"]
# ポケカ以外で発見対象から外すサプライ用品・周辺グッズ（BOX転売の対象外＝通知ノイズ）
EXCLUDE_TITLE_KEYWORDS = [
    "スリーブ", "デッキケース", "プレイマット", "ラバーマット", "デッキシールド",
    "カードローダー", "バインダー", "ストレイジ", "ストレージ", "サプライ",
]
# 動的監視候補の上限（net利益降順で上位のみ。暴走防止）
MAX_DISCOVERED_ITEMS = 30

# --- onepiece_news 方式（ワンピ公式ニュースAPI）---
# 公式サイトのニュース一覧が使うJSON API。新着記事(title,dspdate)の差分で検知する。
ONEPIECE_NEWS_API = "https://onepiece-cardgame.com/common/templates/api/article_list.php"

# --- rakuten_books 方式（楽天市場API・楽天ブックス在庫）---
# 公式API。1リク/秒制限（REQUEST_INTERVALで遵守）。applicationIdはSecretsのRAKUTEN_APP_ID。
RAKUTEN_ICHIBA_API = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"

# --- Phase2.5: 東映LightningSearch APIで新弾BOXを自動発見 ---
# ワンピ/DBFWの新弾BOXが出たら通知。商品ページは toei_stock_status で在庫監視可能。
TOEI_SEARCH_API = "https://d17aii3v2u8mk9.cloudfront.net/api/search"
TOEI_GENRE_CODES = ["104616", "102022"]  # 104616=ワンピカード, 102022=DBFW

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
# Phase3は「ログ通知のみ」で開始し、2週間（6/22〜7/6）の観測で誤除外ゼロを確認したため
# 自動除外を有効化（2026-07-06）。除外中も相場評価は毎回行われ、相場が回復すれば
# drop_counts がリセットされて監視に自動復帰する。
PRICE_SCREEN_ENABLED = True   # 相場選別の判定をログ表示する
AUTO_DROP_ENABLED = True      # dropped 確定銘柄（定価割れ連続DROP_CONFIRM_COUNT回）を監視スキップ

# --- 応募/予約チャンス・ダイジェスト（日次ヘルスレポートに同梱）---
# 監視中のanime-matsuriまとめページから「近い将来の日付を含む抽選/予約/受付の行」を抽出し、
# 毎朝のヘルスレポートと一緒に届ける。再販の瞬間を待つだけでなく、
# 「今日応募できる抽選」を毎日提示して購入機会を能動的に作る。
OPPORTUNITY_KEYWORDS = ["抽選", "予約", "受付", "応募", "先着"]
OPPORTUNITY_WINDOW_DAYS = 45   # 今日からこの日数以内の日付を含む行を「チャンス」とみなす
DIGEST_MAX_LINES = 12          # ダイジェストに載せる最大行数
# 記事紹介文（関連記事ブロック）をチャンスから除外するマーカー。
# anime-matsuriの全ページに載る「〜収録カードリストや当たりカードの…」系の定型文対策。
DIGEST_EXCLUDE_MARKERS = ["収録カードリスト", "当たりカード", "買取価格", "封入確率", "カードリスト",
                          "ストア一覧", "随時更新中", "情報まとめ", "速報をお届け", "コチラ"]
DIGEST_LINE_MAXLEN = 90        # これより長い行は記事紹介文の可能性が高いので除外
DIGEST_LINE_MINLEN = 8         # 短すぎる断片行（テーブルセル由来）は除外
# 日付がなくても「今応募できる」ことを示すマーカー（Amazon招待リクエスト抽選等）
DIGEST_OPEN_MARKERS = ["受付中", "招待リクエスト", "応募受付", "エントリー受付"]
# 一度通知したチャンスは再通知しない（Amazon招待リクエスト等は一度登録すれば済むため、
# 毎朝同じものを見せない）。既知チャンスはstateに保持し、新規に現れたものだけ通知する。
DIGEST_SEEN_KEEP = 300         # 既知チャンスとして保持する行数の上限（古いものからFIFOで破棄）

# --- 監視追加候補の自動提案（日次ヘルスレポートに同梱）---
# altema相場で「買取が高いのに未監視」のポケカ銘柄を毎朝提案する。
# 監視リストが市場の移り変わりで古びるのを防ぐ（アビスアイ等の見落とし再発防止）。
# 価格帯で絞る: 下限=定価超で鞘が出る水準、上限=ヴィンテージ（正規再販が事実上ない）除外。
SUGGEST_MIN_PRICE = 7000
SUGGEST_MAX_PRICE = 40000
SUGGEST_MAX = 5                # 1日に提案する最大件数（提案済みは再提案しない）

# 既定の文字コード（明示しないサイト用）
DEFAULT_ENCODING = "utf-8"

# HTTP設定
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
# (接続タイムアウト, 読み取りタイムアウト)。接続6秒で見切ることで、クラウドIPを遮断している
# ホスト（nyuka-now等）への無駄な待ち時間を最小化しサーキットブレーカーを早く発動させる。
REQUEST_TIMEOUT = (6, 20)
REQUEST_INTERVAL = 1.0  # 商品ごとのリクエスト間隔（サイト負荷配慮）

# 前回在庫状態の保存ファイル
STATE_FILE = "stock_state.json"

# 通知本文の心得（争奪戦は数分で完売。価格は要確認）
NOTE = (
    "再販/在庫を検知。数分で完売の可能性大。"
    "⚠️必ず価格を確認（定価で買えるか。転売価格の場合あり）。"
    "定価なら即購入→即売りで売却検証（寝かせ厳禁）。"
)
