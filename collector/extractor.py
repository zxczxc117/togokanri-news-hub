# -*- coding: utf-8 -*-
"""記事ページから 見出し・本文ブロック・画像・日付 を抜き出す。

■2段構えにしている理由
サイト個別のCSSセレクタ（config/sites/<id>.json の article.bodySelectors）が
当たればそれを使い、当たらなければ「文字が濃い塊を本文とみなす」汎用推定に落ちる。
こうしておくと、新しいサイトを足すときはセレクタ未指定でもだいたい動き、
精度が要るサイトだけ後からセレクタを書けばよい。

■出力を「ブロックの配列」にする理由
アプリ側（HtmlText.Block）が kind(H/P/LI/QUOTE) + text の並びで本文を持っている。
同じ形で配信すれば、アプリは整形処理なしにそのまま表示できる。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .normalize import clean_text, trim_blocks

BLOCK_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "blockquote")
NOISE_WORDS = ("関連記事", "おすすめ記事", "広告", "スポンサー", "この記事のURL", "share on", "follow us")


@dataclass
class Extracted:
    title: str = ""
    blocks: List[dict] = field(default_factory=list)
    image_url: str = ""
    published_at: int = 0
    dropped: List[str] = field(default_factory=list)

    @property
    def body_chars(self) -> int:
        return sum(len(b["text"]) for b in self.blocks)


def _kind_of(tag_name: str) -> str:
    if tag_name.startswith("h"):
        return "H"
    if tag_name == "li":
        return "LI"
    if tag_name == "blockquote":
        return "QUOTE"
    return "P"


def _blocks_from(node, min_chars: int, stop_texts: List[str], dropped: List[str]) -> List[dict]:
    out: List[dict] = []
    for el in node.find_all(BLOCK_TAGS):
        text = clean_text(el.get_text(" ", strip=True))
        if not text:
            continue
        low = text.lower()
        if any(s and s.lower() in low for s in stop_texts):
            # 記事末尾の目印に達したらそこで打ち切る（以降は関連記事などの塊）
            dropped.append("stopAtText: " + text[:40])
            break
        if any(w.lower() in low for w in NOISE_WORDS):
            dropped.append("noise: " + text[:40])
            continue
        kind = _kind_of(el.name.lower())
        if kind == "P" and len(text) < max(min_chars, 1):
            dropped.append("short: " + text[:40])
            continue
        out.append({"kind": kind, "text": text})
    return out


def _drop_nodes(soup: BeautifulSoup, selectors: List[str], dropped: List[str]) -> None:
    for sel in selectors:
        try:
            for el in soup.select(sel):
                dropped.append("dropSelector %s" % sel)
                el.decompose()
        except Exception:  # noqa: BLE001 セレクタの書き間違いで全体を止めない
            dropped.append("dropSelector（無効なセレクタ）: %s" % sel)


def _densest(soup: BeautifulSoup, min_chars: int, stop_texts: List[str], dropped: List[str]) -> List[dict]:
    """汎用推定。段落の合計文字数が最大の入れ物を本文とみなす。"""
    best, best_len = None, 0
    for node in soup.find_all(["article", "main", "div", "section"]):
        length = sum(len(clean_text(p.get_text(" ", strip=True))) for p in node.find_all(("p", "li")))
        # リンクだらけの塊（＝一覧・ナビ）は本文ではないので除く
        link_chars = sum(len(clean_text(a.get_text(" ", strip=True))) for a in node.find_all("a"))
        if length > best_len and (length == 0 or link_chars / max(length, 1) < 0.5):
            best, best_len = node, length
    target = best or soup
    return _blocks_from(target, min_chars, stop_texts, dropped)


def extract(html: str, url: str, article_cfg: dict, max_chars: int) -> Extracted:
    soup = BeautifulSoup(html, "lxml")
    dropped: List[str] = []

    _drop_nodes(soup, ["script", "style", "noscript", "iframe", "form"], [])
    _drop_nodes(soup, article_cfg.get("dropSelectors") or [], dropped)

    title = ""
    for sel in (article_cfg.get("titleSelectors") or ["h1"]) + ["meta[property='og:title']", "title"]:
        try:
            el = soup.select_one(sel)
        except Exception:  # noqa: BLE001
            continue
        if el is None:
            continue
        title = clean_text(el.get("content") if el.name == "meta" else el.get_text(" ", strip=True))
        if title:
            break

    min_chars = int(article_cfg.get("minParagraphChars", 0) or 0)
    stop_texts = article_cfg.get("stopAtTexts") or []

    blocks: List[dict] = []
    for sel in article_cfg.get("bodySelectors") or []:
        try:
            node = soup.select_one(sel)
        except Exception:  # noqa: BLE001
            dropped.append("bodySelector（無効なセレクタ）: %s" % sel)
            continue
        if node is None:
            continue
        blocks = _blocks_from(node, min_chars, stop_texts, dropped)
        if blocks:
            break
    if not blocks:
        blocks = _densest(soup, min_chars, stop_texts, dropped)

    image = ""
    for sel in article_cfg.get("imageSelectors") or []:
        try:
            el = soup.select_one(sel)
        except Exception:  # noqa: BLE001
            continue
        if el is None:
            continue
        image = (el.get("content") or el.get("src") or "").strip()
        if image:
            image = urljoin(url, image)
            break

    published = 0
    from .listing import parse_date_ms
    for sel in article_cfg.get("dateSelectors") or []:
        try:
            el = soup.select_one(sel)
        except Exception:  # noqa: BLE001
            continue
        if el is None:
            continue
        raw = el.get("content") or el.get("datetime") or el.get_text(" ", strip=True)
        published = parse_date_ms(raw or "")
        if published:
            break
    if not published:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
        if m:
            published = parse_date_ms(m.group(1))

    return Extracted(
        title=title,
        blocks=trim_blocks(blocks, max_chars),
        image_url=image if image.startswith("https://") else "",
        published_at=published,
        dropped=dropped[:30],
    )
