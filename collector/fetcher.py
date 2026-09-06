# -*- coding: utf-8 -*-
"""HTTP取得（条件付きGET・リトライ・待ち時間）。

■なぜ条件付きGETを使うか
依頼の要件「更新がある記事のみ取得」を通信段階で満たすため。
前回のETag / Last-Modified を送り、サーバが 304 を返したら
本文の解析も差分計算も行わずに終わる。相手サイトへの負荷も減る。

■なぜ待ち時間を入れるか
1サイト50〜100件を毎回全部取ると相手に負荷をかける。
既定1秒の間隔と1回あたりの新規取得上限（maxNewArticlesPerRun）で
「ゆっくり集めて数回の実行で揃える」形にしている。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    bytes: int
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.error


class Fetcher:
    def __init__(self, gcfg):
        # requests は実行時にだけ必要。--validate や selftest（通信しない用途）で
        # 未インストール環境でも動かせるよう、ここで読み込む。
        import requests

        self.timeout = float(gcfg.get("requestTimeoutSec", 20))
        self.sleep = float(gcfg.get("sleepBetweenRequestsSec", 1.0))
        self.retry = int(gcfg.get("retryCount", 2))
        self.backoff = float(gcfg.get("retryBackoffSec", 3))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": gcfg.get("userAgent", "TogoKanriNewsHub/1.0"),
            "Accept-Language": "ja,en;q=0.8",
        })
        self.total_bytes = 0
        self.requests_made = 0

    def get(self, url: str, etag: str = "", last_modified: str = "") -> FetchResult:
        headers: Dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        last_error = ""
        for attempt in range(self.retry + 1):
            try:
                res = self.session.get(url, headers=headers, timeout=self.timeout)
                self.requests_made += 1
                size = len(res.content or b"")
                self.total_bytes += size
                time.sleep(self.sleep)
                if res.status_code == 304:
                    return FetchResult(url, 304, "", size, etag, last_modified, True)
                if res.status_code >= 500 and attempt < self.retry:
                    last_error = "HTTP %d" % res.status_code
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                # requests の推定文字コードは日本語サイトで外れることがあるため、
                # apparent_encoding（実データからの推定）を優先する。
                if not res.encoding or res.encoding.lower() in ("iso-8859-1", "ascii"):
                    res.encoding = res.apparent_encoding or "utf-8"
                return FetchResult(
                    url, res.status_code, res.text, size,
                    res.headers.get("ETag", ""), res.headers.get("Last-Modified", ""),
                )
            except Exception as e:  # noqa: BLE001  通信は落ちて当然なので必ず握る
                last_error = str(e)[:200]
                if attempt < self.retry:
                    time.sleep(self.backoff * (attempt + 1))
        return FetchResult(url, 0, "", 0, error=last_error or "取得失敗")
