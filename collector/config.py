# -*- coding: utf-8 -*-
"""設定ファイルの読み込みと検証。

■なぜ設定をJSONに外出しするか
サイトごとの調整（セレクタ変更・件数変更・一時停止）が
Pythonコードを触らずに済む形にしておくと、
「HTML構造が変わった1サイトだけ直す」作業が config/sites/<id>.json の
1ファイル差し替えで完了する。生成AIへ渡す資産も1ファイルで足りる。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
SITES_DIR = os.path.join(CONFIG_DIR, "sites")

VALID_CATEGORIES = ("MATOME", "GAME", "STOCK", "TECH", "GENERAL")


@dataclass
class GlobalConfig:
    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, key, default=None):
        return self.raw.get(key, default)

    @property
    def publish_dir(self) -> str:
        return os.path.join(ROOT, self.raw.get("publishDir", "public"))

    @property
    def state_dir(self) -> str:
        return os.path.join(ROOT, self.raw.get("stateDir", "state"))


@dataclass
class SiteConfig:
    raw: Dict[str, Any]
    path: str

    @property
    def id(self) -> str:
        return str(self.raw.get("id", "")).strip()

    @property
    def name(self) -> str:
        return str(self.raw.get("name", self.id)).strip()

    @property
    def category(self) -> str:
        c = str(self.raw.get("category", "GENERAL")).upper()
        return c if c in VALID_CATEGORIES else "GENERAL"

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def allow_full_text(self) -> bool:
        return bool(self.raw.get("allowFullText", True))

    @property
    def max_items(self) -> int:
        return int(self.raw.get("maxItems", 80))

    @property
    def listing(self) -> Dict[str, Any]:
        return self.raw.get("listing", {}) or {}

    @property
    def article(self) -> Dict[str, Any]:
        return self.raw.get("article", {}) or {}


def load_global() -> GlobalConfig:
    path = os.path.join(CONFIG_DIR, "global.json")
    with open(path, encoding="utf-8") as f:
        return GlobalConfig(json.load(f))


def load_sites(only: List[str] | None = None) -> List[SiteConfig]:
    """config/sites/*.json を読む。先頭が _ のファイルはひな型として読み飛ばす。"""
    out: List[SiteConfig] = []
    for name in sorted(os.listdir(SITES_DIR)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        path = os.path.join(SITES_DIR, name)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        site = SiteConfig(raw, path)
        if not site.id:
            raise ValueError("id が空です: %s" % path)
        if only and site.id not in only:
            continue
        out.append(site)
    return out


def validate(sites: List[SiteConfig]) -> List[str]:
    """設定の明らかな誤りを列挙する（通信は行わない）。"""
    errors: List[str] = []
    seen = set()
    for s in sites:
        if s.id in seen:
            errors.append("id が重複: %s" % s.id)
        seen.add(s.id)
        urls = s.listing.get("urls") or []
        if not urls:
            errors.append("%s: listing.urls が空" % s.id)
        for u in urls:
            if not str(u).startswith(("http://", "https://")):
                errors.append("%s: URLが不正 %s" % (s.id, u))
        ltype = s.listing.get("type", "rss")
        if ltype not in ("rss", "html"):
            errors.append("%s: listing.type は rss か html" % s.id)
        if ltype == "html" and not s.listing.get("linkSelector"):
            errors.append("%s: listing.type=html では linkSelector が必須" % s.id)
        if s.max_items < 1 or s.max_items > 300:
            errors.append("%s: maxItems は 1〜300" % s.id)
    return errors
