#!/usr/bin/env python3
"""判定規則: 日付解決・実質情報判定・商品ライフサイクル・相場選別（check_stock.pyから分割）
規則の仕様は NOTIFICATION_RULES.md を参照。"""

import re
from datetime import datetime

import config


def _this_year():
    """現在の年（西暦）。ポケカ新弾の発売日フィルタを年経過で自動追従させるため。"""
    return datetime.now().year


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


def _deck_supply_rule(line, is_pokeca, today):
    """商品ライフサイクル規則（サプライ/デッキ系）を適用する。
    返り値: (excluded, forced)。excluded=True なら通知対象外。
    forced=True はポケカのデッキ系初回販売（明示的に通知対象）。
    差分通知と朝のダイジェストの両方で共通に使う。"""
    if any(kw in line for kw in config.SUPPLY_NOISE_KEYWORDS):
        return True, False
    # スターターセット/構築デッキ系（「スタートデッキ」は別扱いで常に許可）
    if any(kw in line for kw in config.DECK_PRODUCT_KEYWORDS) and "スタートデッキ" not in line:
        if not is_pokeca:
            return True, False
        if any(w in line for w in ("再販", "再入荷", "再販売")):
            return True, False  # ポケカのデッキ系は初回販売のみ（再販は転売不可）
        dates = _upcoming_dates(line, today) if today else []
        if not any(abs((d - today).days) <= config.INITIAL_SALE_WINDOW_DAYS for d in dates):
            return True, False  # 発売前後の初回販売期でなければ通知しない
        return False, True
    return False, False


def _is_actionable_line(line, today=None, strict=False, is_pokeca=False):
    """追加行が「通知する価値のある実質情報」か判定する。
    (1)再販/入荷/抽選/予約/コラボ/発売等の行動語を含む、または
    (2)近い将来の日付＋日付以外の中身を含む（strict=Falseの場合のみ）
    かつ、定型文・ナビ断片でなく、商品ライフサイクル規則に適合すること:
      - サプライ類: 常に通知しない
      - スターターセット/構築デッキ系: ポケカのみ初回販売（発売前後60日の告知）だけ通知。
        再販は通知しない。ポケカ以外は通知しない
      - スタートデッキ: 再販でも人気のため常に通知対象（例外）"""
    if not (config.DIGEST_LINE_MINLEN <= len(line) <= 120):
        return False
    if any(mk in line for mk in config.DIGEST_EXCLUDE_MARKERS):
        return False
    # カード以外の商品（雑誌/書籍/フィギュア等のカテゴリタグ）は対象外。
    # ただしカード付録（Vジャンプのプロモカード等）を含むものは購入対象になり得るため通知する。
    if any(tag in line for tag in config.NON_CARD_CATEGORY_TAGS):
        if not (("カード" in line) and any(mk in line for mk in config.MAGAZINE_CARD_MARKERS)):
            return False
    # 「販売継続中」等は状態の継続であって新しいチャンスではない
    if any(mk in line for mk in config.STATUS_QUO_MARKERS):
        return False
    excluded, forced = _deck_supply_rule(line, is_pokeca, today)
    if excluded:
        return False
    if forced:
        return True  # ポケカのデッキ系・初回販売期（規則で明示的に許可）
    if any(kw in line for kw in config.NOTIFY_ACTION_KEYWORDS):
        return True
    if strict:
        return False
    if today is not None:
        dates = _upcoming_dates(line, today)
        if any(0 <= (d - today).days <= config.OPPORTUNITY_WINDOW_DAYS for d in dates):
            # 日付以外の中身があること（「2026.7.10」のような日付セル単独の行は
            # 情報ゼロなので通知しない。中身は隣の題名行が別途拾われる）
            residue = re.sub(
                r"20\d\d[年./]\s*\d{1,2}[月./]\s*\d{1,2}日?|\d{1,2}月\s*\d{1,2}日"
                r"|[〜~（）()（）\s、。・!！?？-]",
                "", line)
            return len(residue) >= 8
    return False


def _expired_pokeca_titles(prev, new_state, today):
    """ポケカ公式APIの商品リストから「発売から1年半超」の商品タイトル（正規化済み）を返す。
    これらの商品は再販されないため、言及する行を通知から除外する。"""
    officials = new_state.get("pokecard_official") or prev.get("pokecard_official") or []
    out = []
    for key in officials:
        parts = key.split("|")
        if len(parts) != 2:
            continue
        title, rdate = parts
        m = re.search(r"(20\d\d)年\s*(\d{1,2})月\s*(\d{1,2})日", rdate)
        if not m:
            continue
        from datetime import date
        try:
            released = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if (today - released).days > config.MAX_PRODUCT_AGE_DAYS:
            nt = _normalize_box_name(title)
            # 「拡張パック」等のカテゴリ接頭辞を剥がしてコア名で照合できるようにする
            # （通知行は「超電ブレイカーBOX再販」のようにコア名だけで言及されるため）
            for pre in ("拡張パックデラックス", "強化拡張パック", "拡張パック",
                        "ハイクラスパック", "スペシャルカードセット", "スペシャルBOX",
                        "デッキビルドBOX", "プレミアムトレーナーボックス"):
                if nt.startswith(pre):
                    nt = nt[len(pre):]
                    break
            if len(nt) >= 5:
                out.append(nt)
    return out


def _mentions_expired(line, expired_titles):
    """行が「1年半超で再販の来ない商品」に言及しているか。"""
    nl = _normalize_box_name(line)
    return any(t in nl for t in expired_titles)


def _item_short_name(item):
    """監視名から商品コア名を取り出す（検索クエリ・ダイジェスト表示用）。"""
    short = item.get("name", "")
    for w in (" 抽選/再販まとめ（anime-matsuri）", " 抽選/予約まとめ（anime-matsuri）",
              " 再販告知まとめ（anime-matsuri）", " 再販集約", "（横断）", "（在庫）",
              "（楽天ブックス）", "（東映ストア）"):
        short = short.replace(w, "")
    return short.strip()


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

def extract_lottery_candidate(line, item, today, store_hints):
    """検知行から「応募台帳に登録できる構造化された抽選候補」を抽出する。
    条件（すべて必須・精度優先）:
      - 行に店舗名（store_hints のキー）がある
      - 抽選/応募/予約/先着 のいずれかを含む
      - 今日以降の日付が1つ以上ある（締切=最も遅い日付、開始=最も早い日付）
    返り値: dict（channel/product/apply_start/apply_end） | None。
    商品名は監視アイテム名から取る（行の断片より確実）。"""
    channel = next((name for name in store_hints if name in line), None)
    if not channel:
        return None
    if not any(kw in line for kw in ("抽選", "応募", "予約", "先着")):
        return None
    dates = [d for d in _upcoming_dates(line, today) if 0 <= (d - today).days <= 60]
    if not dates:
        return None
    return {
        "channel": channel,
        "product": _item_short_name(item),
        "apply_start": min(dates).isoformat() if len(dates) > 1 else None,
        "apply_end": max(dates).isoformat(),
    }

