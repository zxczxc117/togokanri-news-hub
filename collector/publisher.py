# -*- coding: utf-8 -*-
"""配信JSON（public/index.json と public/sites/<id>.json）の書き出し。

■rev（版）を持たせる理由
アプリは1日3回の取得のたびに全サイトのJSONを落とす必要はない。
index.json にサイトごとの rev（本文ハッシュの集約値）を載せておけば、
アプリは「rev が前回と同じサイトは本体JSONを取りに行かない」と判断できる。
これで通信量は index.json（数KB）だけになる回が大半になる。

■ファイルを rev が変わった時だけ書く理由
内容が同じでも書き直すと Git が差分ありと判断してコミットが増える。
rev が変わった時だけ書けば、コミット履歴が「実際に更新があった回」だけになる。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .normalize import sha

SCHEMA_VERSION = 1


def _iso(ms: int) -> str:
    if not ms:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S+09:00", time.localtime(ms / 1000))


def site_payload(site, records: List[Dict[str, Any]], include_body: bool) -> Dict[str, Any]:
    records = sorted(records, key=lambda a: int(a.get("publishedAt") or 0), reverse=True)
    articles = []
    for r in records:
        item = {
            "id": r.get("id", ""),
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "summary": r.get("summary", ""),
            "imageUrl": r.get("imageUrl", ""),
            "publishedAt": int(r.get("publishedAt") or 0),
            "updatedAt": int(r.get("updatedAt") or 0),
            "hash": r.get("hash", ""),
        }
        if include_body and site.allow_full_text:
            item["bodyBlocks"] = r.get("bodyBlocks") or []
        else:
            item["bodyBlocks"] = []
        articles.append(item)

    rev = sha("|".join("%s:%s" % (a["url"], a["hash"]) for a in articles), length=12)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "siteId": site.id,
        "name": site.name,
        "category": site.category,
        "allowFullText": site.allow_full_text,
        "rev": rev,
        "generatedAt": _iso(int(time.time() * 1000)),
        "count": len(articles),
        "articles": articles,
    }


def write_site(publish_dir: str, payload: Dict[str, Any]) -> tuple[bool, int]:
    """rev が変わっていれば書き出す。戻り値 (書いたか, バイト数)。"""
    path = os.path.join(publish_dir, "sites", "%s.json" % payload["siteId"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
            if old.get("rev") == payload["rev"]:
                return False, os.path.getsize(path)
        except Exception:  # noqa: BLE001 壊れていれば上書きする
            pass
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True, len(text.encode("utf-8"))


def write_index(publish_dir: str, entries: List[Dict[str, Any]]) -> str:
    entries = sorted(entries, key=lambda e: e["id"])
    index = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _iso(int(time.time() * 1000)),
        "sites": entries,
    }
    index["rev"] = sha("|".join("%s:%s" % (e["id"], e["rev"]) for e in entries), length=12)
    path = os.path.join(publish_dir, "index.json")
    os.makedirs(publish_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return index["rev"]
