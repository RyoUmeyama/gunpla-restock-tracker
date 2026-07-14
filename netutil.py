#!/usr/bin/env python3
"""通信ユーティリティ: requestsラッパとサーキットブレーカー（check_stock.pyから分割）"""

from urllib.parse import urlsplit

import requests

import config


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
