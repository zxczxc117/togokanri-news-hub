# -*- coding: utf-8 -*-
"""通信なしの机上テスト。設定の検証と、同梱サンプルHTML/RSSでの抽出確認を行う。

    python -m collector.selftest

GitHub へ上げる前、またはサイト個別の調整をした後にこれを流せば、
「セレクタの書き間違いで本文が0件になる」事故をGit push前に見つけられる。
"""
from __future__ import annotations

import os
import sys

from . import config as cfgmod
from .extractor import extract
from .listing import from_feed
from .normalize import normalize_url

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


class _StubFetcher:
    """通信の代わりに samples/ の中身を返す。差分判定と書き出しの机上確認用。"""

    def __init__(self):
        self.requests_made = 0
        self.total_bytes = 0
        with open(os.path.join(SAMPLES, "sample_feed.xml"), encoding="utf-8") as f:
            self.feed = f.read()
        with open(os.path.join(SAMPLES, "sample_article.html"), encoding="utf-8") as f:
            self.article = f.read()

    def get(self, url, etag="", last_modified=""):
        from .fetcher import FetchResult

        self.requests_made += 1
        body = self.feed if url.endswith("feed") else self.article
        self.total_bytes += len(body)
        return FetchResult(url, 200, body, len(body))


def pipeline_test() -> int:
    """収集→state→配信JSONを、通信せずに一巡させる。

    1回目で2件が新規になり、2回目は「更新なし」で据え置きになること
    （＝更新がある記事のみ取得する仕組みが効いていること）を確認する。
    """
    import argparse
    import json
    import shutil

    from .main import run_site
    from .state import SiteState

    ng = 0
    gcfg = cfgmod.load_global()
    gcfg.raw["publishDir"] = "samples/_tmp_out"
    gcfg.raw["stateDir"] = "samples/_tmp_state"
    gcfg.raw["sleepBetweenRequestsSec"] = 0
    gcfg.raw["bodyMinChars"] = 100
    gcfg.raw["recheckHours"] = 24
    for d in (gcfg.publish_dir, gcfg.state_dir):
        shutil.rmtree(d, ignore_errors=True)

    site = cfgmod.SiteConfig({
        "id": "selftest_site", "name": "机上検証サイト", "category": "GENERAL",
        "enabled": True, "allowFullText": True, "maxItems": 10,
        "listing": {"type": "rss", "urls": ["https://example.com/feed"]},
        "article": {
            "titleSelectors": ["h1"], "bodySelectors": ["div.entry-body"],
            "dropSelectors": [".ad", ".related"],
            "imageSelectors": ["meta[property='og:image']"],
            "dateSelectors": ["meta[property='article:published_time']"],
            "stopAtTexts": ["関連記事"], "minParagraphChars": 20,
        },
    }, "(selftest)")
    args = argparse.Namespace(dry_run=False, limit=0)

    first = run_site(site, gcfg, _StubFetcher(), args)
    second = run_site(site, gcfg, _StubFetcher(), args)
    print("パイプライン: 1回目 新規%d 更新%d / 2回目 新規%d 更新%d 取得%d"
          % (first["added"], first["updated"], second["added"], second["updated"], second["fetched"]))
    if first["added"] != 2:
        ng += 1
        print("  NG 1回目は2件が新規になるべき")
    if second["added"] or second["updated"]:
        ng += 1
        print("  NG 2回目は新規・更新0件になるべき（更新分だけ取る仕組みの確認）")
    if first["rev"] != second["rev"]:
        ng += 1
        print("  NG 内容が同じなら rev は変わらないべき")

    path = os.path.join(gcfg.publish_dir, "sites", "selftest_site.json")
    if not os.path.exists(path):
        ng += 1
        print("  NG 配信JSONが書き出されていない")
    else:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        need = ("schemaVersion", "siteId", "rev", "articles")
        missing = [k for k in need if k not in payload]
        first_article = (payload.get("articles") or [{}])[0]
        keys = ("id", "url", "title", "summary", "imageUrl", "publishedAt", "hash", "bodyBlocks")
        missing += [k for k in keys if k not in first_article]
        if missing:
            ng += 1
            print("  NG 配信JSONに項目が足りない: %s" % missing)
        else:
            print("配信JSON: %d件 / 先頭記事のブロック数=%d"
                  % (payload["count"], len(first_article["bodyBlocks"])))
    # 検証用の一時出力は残さない
    shutil.rmtree(gcfg.publish_dir, ignore_errors=True)
    shutil.rmtree(gcfg.state_dir, ignore_errors=True)
    _ = SiteState  # import が未使用にならないよう明示的に触る
    return ng


def main() -> int:
    ng = 0

    sites = cfgmod.load_sites()
    problems = cfgmod.validate(sites)
    print("設定: %d サイト / 問題 %d件" % (len(sites), len(problems)))
    for p in problems:
        print("  NG %s" % p)
    ng += len(problems)

    # URL正規化（アプリ側 NewsRepository.normalizeUrl と同じ結果になること）
    cases = [
        ("https://Example.com/a/?utm_source=x&id=3#top", "https://example.com/a/?id=3"),
        ("https://example.com/b/", "https://example.com/b"),
    ]
    for src, want in cases:
        got = normalize_url(src)
        mark = "OK" if got == want else "NG"
        if mark == "NG":
            ng += 1
        print("URL正規化 %s: %s -> %s" % (mark, src, got))

    with open(os.path.join(SAMPLES, "sample_feed.xml"), encoding="utf-8") as f:
        items = from_feed(f.read(), 10)
    print("RSS解析: %d件 %s" % (len(items), [i.title for i in items]))
    if len(items) != 2:
        ng += 1
        print("  NG RSSサンプルは2件取れるべき")

    with open(os.path.join(SAMPLES, "sample_article.html"), encoding="utf-8") as f:
        html = f.read()
    cfg = {
        "titleSelectors": ["h1"],
        "bodySelectors": ["div.entry-body"],
        "dropSelectors": [".ad", ".related"],
        "imageSelectors": ["meta[property='og:image']"],
        "dateSelectors": ["meta[property='article:published_time']"],
        "stopAtTexts": ["関連記事"],
        "minParagraphChars": 20,
    }
    ex = extract(html, "https://example.com/news/1", cfg, 20000)
    print("本文抽出: 見出し=%s / ブロック=%d / 文字数=%d / 画像=%s"
          % (ex.title, len(ex.blocks), ex.body_chars, bool(ex.image_url)))
    if ex.body_chars < 100 or not ex.title:
        ng += 1
        print("  NG サンプル記事から本文が抜けていない")
    if any("広告" in b["text"] for b in ex.blocks):
        ng += 1
        print("  NG 広告ブロックが残っている")

    # セレクタを外した場合でも汎用推定で本文が取れること
    ex2 = extract(html, "https://example.com/news/1", dict(cfg, bodySelectors=[]), 20000)
    print("汎用推定: ブロック=%d / 文字数=%d" % (len(ex2.blocks), ex2.body_chars))
    if ex2.body_chars < 100:
        ng += 1
        print("  NG セレクタ無しでも本文が取れるべき")

    ng += pipeline_test()

    print("=" * 40)
    print("SELFTEST %s (NG=%d)" % ("PASS" if ng == 0 else "FAIL", ng))
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
