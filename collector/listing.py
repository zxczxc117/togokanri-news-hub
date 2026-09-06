# -*- coding: utf-8 -*-
"""記事一覧の取得（RSS/Atom/RDF と HTML一覧の両対応）。

■なぜHTML一覧にも対応するか
依頼の前提「RSSでは情報がほとんど取得できない」。
RSSが抜粋しか返さない、あるいはRSS自体が無いサイトでは
一覧ページのHTMLからリンクを拾う必要がある。
どちらの場合も「記事URLの候補一覧」という同じ形に揃えて
以降の処理（差分判定・本文取得）を共通化している。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .normalize import clean_text


@dataclass
class Candidate:
    url: str
    title: str = ""
    summary: str = ""
    image_url: str = ""
    published_at: int = 0          # epoch millis（0=不明）
    feed_updated: str = ""         # フィードが示す更新時刻の生文字列
    feed_content: str = ""         # RSSに本文が入っていた場合の生HTML
    extra: dict = field(default_factory=dict)


_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
)


def parse_date_ms(text: str) -> int:
    """日付表記のブレを吸収して epoch millis にする。読めなければ0。"""
    import datetime as dt

    t = clean_text(text)
    if not t:
        return 0
    t = t.replace("Z", "+0000")
    t = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", t)
    for fmt in _DATE_FORMATS:
        try:
            d = dt.datetime.strptime(t, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    return 0


def _tag(name: str) -> str:
    """名前空間付きタグ名を素のローカル名に落とす。"""
    return name.rsplit("}", 1)[-1].lower()


def from_feed(xml_text: str, limit: int) -> List[Candidate]:
    """RSS2.0 / RDF(RSS1.0) / Atom を1つの関数で読む（アプリ側と同じ考え方）。"""
    out: List[Candidate] = []
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8", "ignore"))
    except ElementTree.ParseError:
        return out

    for node in root.iter():
        if _tag(node.tag) not in ("item", "entry"):
            continue
        title = link = summary = content = date = image = ""
        for child in node:
            name = _tag(child.tag)
            text = (child.text or "").strip()
            if name == "title" and not title:
                title = text
            elif name == "link":
                href = child.attrib.get("href", "")
                rel = child.attrib.get("rel", "")
                if href and rel in ("", "alternate"):
                    link = href
                elif text.startswith("http") and not link:
                    link = text
            elif name == "guid" and not link and text.startswith("http"):
                link = text
            elif name in ("description", "summary") and not summary:
                summary = text
            elif name in ("encoded", "content") and not content:
                content = text or ""
                if not content:
                    url = child.attrib.get("url", "")
                    if url and not image:
                        image = url
            elif name in ("pubdate", "date", "updated", "published") and not date:
                date = text
            elif name in ("enclosure", "thumbnail") and not image:
                image = child.attrib.get("url", "")
        if not title or not link:
            continue
        if not image:
            m = re.search(r"""<img[^>]+src=["']([^"']+)["']""", content or summary or "", re.I)
            if m:
                image = m.group(1)
        out.append(Candidate(
            url=link.strip(),
            title=clean_text(re.sub(r"<[^>]*>", "", title)),
            summary=clean_text(re.sub(r"<[^>]*>", "", summary))[:400],
            image_url=image.strip(),
            published_at=parse_date_ms(date),
            feed_updated=date,
            feed_content=content or "",
        ))
        if len(out) >= limit:
            break
    return out


def from_html(html: str, base_url: str, listing: dict, limit: int) -> List[Candidate]:
    """一覧ページのHTMLから記事リンクを拾う。"""
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "lxml")
    selector = listing.get("linkSelector") or "a"
    attr = listing.get("linkAttr") or "href"
    include = listing.get("urlIncludePatterns") or []
    exclude = listing.get("urlExcludePatterns") or []

    out: List[Candidate] = []
    seen = set()
    for a in soup.select(selector):
        href = a.get(attr) or ""
        if not href:
            continue
        url = urljoin(base_url, href.strip())
        if not url.startswith(("http://", "https://")):
            continue
        if include and not any(p in url for p in include):
            continue
        if exclude and any(p in url for p in exclude):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(Candidate(url=url, title=clean_text(a.get_text(" ", strip=True))[:200]))
        if len(out) >= limit:
            break
    return out


def collect(fetcher, site, limit: int, state) -> tuple[List[Candidate], List[str]]:
    """設定の listing.urls をすべて読み、記事URLの候補を返す。"""
    errors: List[str] = []
    listing = site.listing
    ltype = listing.get("type", "rss")
    result: List[Candidate] = []
    for url in listing.get("urls", []):
        meta = state.listing_meta(url)
        res = fetcher.get(url, meta.get("etag", ""), meta.get("lastModified", ""))
        if res.not_modified:
            # 一覧が更新されていない＝新着なし。記事の再取得もしない。
            continue
        if not res.ok:
            errors.append("%s 一覧取得失敗(%s): %s" % (site.id, res.status, res.error or url))
            continue
        state.set_listing_meta(url, res.etag, res.last_modified)
        if ltype == "rss":
            result += from_feed(res.text, limit)
        else:
            result += from_html(res.text, url, listing, limit)
    # 同じURLが複数の一覧に出ることがあるので先着順で一意化する
    uniq: List[Candidate] = []
    seen = set()
    for c in result:
        if c.url in seen:
            continue
        seen.add(c.url)
        uniq.append(c)
    return uniq[:limit], errors
