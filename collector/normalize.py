# -*- coding: utf-8 -*-
"""URL正規化・ハッシュ・本文ブロック整形。

■アプリ側と同じ規則で正規化する理由
アプリ（NewsRepository.normalizeUrl）は追跡クエリを落として小文字化した
URLで重複を判定している。配信側が別の規則で正規化すると、
同じ記事が「RSSルート」と「配信JSONルート」で二重に並ぶ。
そのため、ここはアプリの実装と1対1で同じ規則にしてある。
変更する場合は必ず両方を直すこと。
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, List

TRACKING_KEYS = ("utm_", "fbclid", "gclid", "yclid", "cmpid", "ref_src", "ref=", "spm=")

_WS = re.compile(r"[ \t\u3000]+")
_NL = re.compile(r"\n{2,}")


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("#", 1)[0]
    if "?" in u:
        base, query = u.split("?", 1)
        kept = [
            p for p in query.split("&")
            if p and not any(p.lower().startswith(k) for k in TRACKING_KEYS)
        ]
        u = base if not kept else base + "?" + "&".join(sorted(kept))
    return u.rstrip("/").lower()


def sha(*parts: Iterable[str], length: int = 16) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()[:length]


def clean_text(text: str) -> str:
    t = (text or "").replace("\r", "\n")
    t = _WS.sub(" ", t)
    t = _NL.sub("\n", t)
    return t.strip()


def title_key(title: str) -> str:
    """全角半角と記号の違いを吸収した見出しの比較キー。"""
    t = clean_text(title)
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[!-/:-@\[-`{-~！-／：-＠［-｀｛-～、。「」『』・…—―\-]", "", t)
    return t.lower()


def blocks_hash(blocks: List[dict]) -> str:
    return sha("".join(b.get("kind", "") + b.get("text", "") for b in blocks))


def trim_blocks(blocks: List[dict], max_chars: int) -> List[dict]:
    out: List[dict] = []
    total = 0
    for b in blocks:
        text = clean_text(b.get("text", ""))
        if not text:
            continue
        if total + len(text) > max_chars:
            break
        new_block = {
            "kind": b.get("kind", "P"),
            "text": text,
        }

        if b.get("imageUrls"):
            new_block["imageUrls"] = b["imageUrls"]

        if b.get("imageIds"):
            new_block["imageIds"] = b["imageIds"]

        out.append(new_block)
        total += len(text)
    return out
