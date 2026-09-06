# -*- coding: utf-8 -*-
"""前回の取得結果（state/<siteId>.json）の読み書き。

■なぜ state を持つのか
依頼の要件「更新がある記事のみ取得」を満たすには、
前回何を持っていたか（URL・本文ハッシュ・ETag）を覚えておく必要がある。
state を Git にコミットしておけば、GitHub Actions の実行環境が
毎回新品でも前回の記憶を引き継げる（Actionsのキャッシュに頼らない）。

■なぜ本文まで state に持つか
配信JSONは「全記事の一覧」なので、更新が1件だけでもファイル全体を作り直す。
本文を state に持っていれば、更新の無い記事は再取得せずに書き出せる。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List


class SiteState:
    def __init__(self, state_dir: str, site_id: str):
        self.path = os.path.join(state_dir, "%s.json" % site_id)
        self.site_id = site_id
        self.data: Dict[str, Any] = {
            "siteId": site_id,
            "updatedAt": 0,
            "listing": {},
            "articles": {},
        }
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except Exception:  # noqa: BLE001 壊れていたら作り直す（次回実行で復旧する）
                pass

    # ---------- 一覧ページの条件付きGET用メタ ----------
    def listing_meta(self, url: str) -> Dict[str, str]:
        return (self.data.get("listing") or {}).get(url, {})

    def set_listing_meta(self, url: str, etag: str, last_modified: str) -> None:
        self.data.setdefault("listing", {})[url] = {
            "etag": etag or "",
            "lastModified": last_modified or "",
            "checkedAt": int(time.time() * 1000),
        }

    # ---------- 記事 ----------
    def article(self, key: str) -> Dict[str, Any] | None:
        return (self.data.get("articles") or {}).get(key)

    def put_article(self, key: str, record: Dict[str, Any]) -> None:
        self.data.setdefault("articles", {})[key] = record

    def articles(self) -> List[Dict[str, Any]]:
        return list((self.data.get("articles") or {}).values())

    def prune(self, keep_days: int, max_items: int) -> int:
        """古い記事と上限超過分を落とす。戻り値は消した件数。"""
        items = self.articles()
        now = int(time.time() * 1000)
        limit_ms = keep_days * 86400 * 1000
        alive = [
            a for a in items
            if not keep_days or (now - int(a.get("publishedAt") or a.get("fetchedAt") or now)) <= limit_ms
        ]
        alive.sort(key=lambda a: int(a.get("publishedAt") or 0), reverse=True)
        alive = alive[:max_items]
        removed = len(items) - len(alive)
        self.data["articles"] = {a["key"]: a for a in alive}
        return removed

    def save(self) -> None:
        self.data["updatedAt"] = int(time.time() * 1000)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
