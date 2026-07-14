#!/usr/bin/env python3
"""リンク解決: ストア直リンクの確実な対応付けと検索URLフォールバック（check_stock.pyから分割）"""

import re

import config
from rules import _upcoming_dates, _item_short_name


def _unwrap_affiliate(url):
    """楽天アフィリエイト/バリューコマース等のラッパーURLから実際の遷移先を取り出す。
    取り出せない形式はそのまま返す（アフィリエイト経由でも商品ページには着地する）。"""
    from urllib.parse import urlparse, parse_qs, unquote
    try:
        q = parse_qs(urlparse(url).query)
        for key in ("pc", "m", "vc_url", "url", "u"):
            vals = q.get(key)
            if vals and vals[0].startswith("http"):
                return unquote(vals[0])
    except Exception:
        pass
    return url


def _norm_link_text(t):
    """アンカーテキスト照合用の正規化（タグ・エンティティ・空白を除去）。"""
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"&[a-z]+;|&#\d+;", " ", t)
    return re.sub(r"\s+", "", t)


def _clean_store_url(href):
    """hrefのHTMLエンティティを復元→アフィリエイト剥がし→ストアドメイン確認。
    ストアドメインでなければ None。"""
    href = href.replace("&amp;", "&").replace("&#038;", "&")
    real = _unwrap_affiliate(href)
    if "anime-matsuri.com" in real or "nyuka-now.com" in real:
        return None
    if any(d in real for d in config.STORE_DOMAINS):
        return real
    return None


def extract_anchors(html):
    """ページ内の <a href>text</a> を (正規化テキスト, href) のリストで返す。
    行→リンクの確実な対応付け（アンカーテキスト一致）に使う。"""
    anchors = []
    if not html:
        return anchors
    for m in re.finditer(r'<a\s[^>]*?href="(https?://[^"]+?)"[^>]*?>(.*?)</a>', html, re.S | re.I):
        text = _norm_link_text(m.group(2))
        if len(text) >= 10:
            anchors.append((text, m.group(1)))
    return anchors


def resolve_store_link(html, line, anchors=None):
    """追加行 line に対応する遷移先ストアURLを返す。
    「実際のページでは関係ないものが表示される」誤リンク事故を防ぐため、
    **確実に対応関係が取れる場合だけ**URLを返す（推測で近傍リンクを拾わない）:
      ① 行がリンクのアンカーテキストそのもの（集約ページの商品名リンク）→ そのhref
      ② 行が店舗名を含む → 行の近傍でドメインが店舗名と一致するリンクのみ
    どちらでもなければ None（URLなしで通知。集約ページのURLは従来どおり載る）。"""
    if not html:
        return None
    # ① アンカーテキスト一致（最も確実: その行はリンクの文字列そのもの）
    if anchors is None:
        anchors = extract_anchors(html)
    nl = _norm_link_text(line)
    if len(nl) >= 15:
        for na, href in anchors:
            # 完全包含のみ採用（先頭一致では同一シリーズの別商品に交差マッチするため。
            # 例: METAL ROBOT魂＜SIDE MS＞... は先頭が全商品共通）。
            # 「行 ⊆ アンカー」はアンカーが商品名＋（価格：...）等の装飾付きのケース。
            if len(na) >= 15 and (nl in na or na in nl):
                real = _clean_store_url(href)
                if real:
                    return real
    # ② 店舗名ヒント一致（行が「Amazonで抽選受付」等の場合のみ・ドメイン一致必須）
    hint_domains = None
    for name, domains in config.STORE_NAME_HINTS.items():
        if name in line:
            hint_domains = domains
            break
    if not hint_domains:
        return None
    idx = -1
    for probe_len in (18, 10, 6):
        probe = line[:probe_len].strip()
        if len(probe) >= 4:
            idx = html.find(probe)
            if idx >= 0:
                break
    if idx < 0:
        return None
    win_start = max(0, idx - 1500)
    window = html[win_start: idx + 1500]
    cands = []  # (ウィンドウ内位置, 実URL)
    for m in re.finditer(r'href="(https?://[^"]+?)"', window):
        real = _clean_store_url(m.group(1))
        if real and any(d in real for d in hint_domains):
            cands.append((m.start(), real))
    if not cands:
        return None  # 店舗名は分かるのに一致リンクが無い→誤リンクを載せない
    # 行の位置(ウィンドウ内では1500付近)より後ろにあるリンクを優先（表は「行→リンク」の順）
    line_pos = idx - win_start
    after = [c for c in cands if c[0] >= line_pos]
    chosen = min(after, key=lambda c: c[0]) if after else max(cands, key=lambda c: c[0])
    return chosen[1]


def resolve_store_link_from_article(html, title):
    """nyuka-now等の記事ページ（単一商品）から遷移先ストアURLを解決する。
    タイトルの【Amazon】等の店舗タグとドメインが一致するリンクだけを採用し、
    一致が取れなければ None（誤リンクを載せない。呼び出し側が検索URLで代替）。"""
    if not html:
        return None
    hint = None
    for name, domains in config.STORE_NAME_HINTS.items():
        if name in title:
            hint = domains
            break
    if not hint:
        return None  # 店舗が特定できない記事は確実な対応が取れない
    for m in re.finditer(r'href="(https?://[^"]+?)"', html):
        real = _clean_store_url(m.group(1).replace("&amp;", "&").replace("&#038;", "&"))
        if real and any(d in real for d in hint):
            return real
    return None


def fallback_search_url(line, item):
    """確実な直リンクが無い行に付ける「ストア検索URL」を作る。
    集約ページのURLだけでは『結局どこで買えるのか』が分からないため、
    商品名でのストア検索結果へ直接飛ばす。行に店舗名があればそのストアの検索、
    なければAmazon検索。クエリは行から日付・記号・店舗タグを除いた商品名部分
    （短すぎる行は監視対象の商品名を使う）。"""
    from urllib.parse import quote
    tpl_key = "amazon"
    for name, key in config.STORE_SEARCH_KEY.items():
        if name in line:
            tpl_key = key
            break
    q = line
    q = re.sub(r"【[^】]*】", " ", q)                      # 店舗タグ等
    # 日付は全形式除去（2026年7月10日 / 2026.7.10 / 2026/7/10 / 7月10日）
    q = re.sub(r"20\d\d[年./]\s*\d{1,2}[月./]\s*\d{1,2}日?|\d{1,2}月\s*\d{1,2}日", " ", q)
    q = re.sub(r"[（(].*?[)）]", " ", q)                   # 括弧注記
    q = q.replace("「", " ").replace("」", " ").replace("『", " ").replace("』", " ")
    for w in ("再販", "入荷", "抽選", "予約", "受付中", "受付", "先着", "販売開始", "販売",
              "発売", "在庫", "応募", "開始", "期間", "情報", "まとめ", "〜", "～",
              "にて", "継続中", "継続", "です", "ます"):
        q = q.replace(w, " ")
    q = re.sub(r"[、。．！!？?・]", " ", q)                # 句読点・記号
    q = re.sub(r"\s(が|を|に|は|で|の|と)\s", " ", " " + q + " ")  # 浮いた助詞
    q = re.sub(r"(?<=[ぁ-んァ-ヶ一-龯A-Za-z0-9])(が|を|は|に|で)(?=\s|$)", "", q)  # 語末の助詞（「3種が」→「3種」）
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) < 8:  # 行から商品名が取れない（期間行など）→ 監視対象の商品名で検索
        q = _item_short_name(item)
    q = q[:40]
    return config.SEARCH_URL_TEMPLATES[tpl_key].format(q=quote(q))
