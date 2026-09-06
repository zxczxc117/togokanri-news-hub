# -*- coding: utf-8 -*-
"""収集の本体。GitHub Actions からも手元のWindowsからも同じ入口で動く。

使い方:
    python -m collector.main                 全サイト
    python -m collector.main --site 4gamer    1サイトだけ
    python -m collector.main --dry-run        取得はするが public/ と state/ を書かない
    python -m collector.main --limit 5        1サイトの新規取得を5件までに絞る（試験用）
    python -m collector.main --validate       設定の検証だけ（通信しない）

処理の流れ（1サイトあたり）:
  1) 一覧（RSS/HTML）を条件付きGET → 304 なら以降を全部飛ばす
  2) 候補URLを正規化し、state と突き合わせて「新規・更新あり」だけ選ぶ
  3) 選んだ記事だけ本文を取得（1回あたりの上限 maxNewArticlesPerRun）
  4) state を更新 → public/sites/<id>.json を rev が変わった時だけ書く
  5) 全サイト終了後に public/index.json を書く
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from . import config as cfgmod
from .extractor import extract
from .fetcher import Fetcher
from .listing import collect
from .normalize import blocks_hash, clean_text, normalize_url, sha, title_key
from .publisher import site_payload, write_images, write_index, write_site
from .state import SiteState
import base64
import hashlib
import mimetypes
import urllib.request
from urllib.request import Request, urlopen

def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _summary_from_blocks(blocks: List[Dict[str, str]], fallback: str) -> str:
    if fallback:
        return fallback[:400]
    for b in blocks:
        if b.get("kind") == "P" and len(b.get("text", "")) >= 20:
            return b["text"][:400]
    return ""

def _image_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _load_image_cache(state_dir: str) -> Dict[str, Dict[str, str]]:
    path = os.path.join(state_dir, "images.json")
    if not os.path.exists(path):
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("images", {})
    except Exception:
        return {}


def _save_image_cache(
    state_dir: str,
    images: Dict[str, Dict[str, str]],
) -> None:
    path = os.path.join(state_dir, "images.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    payload = {
        "schemaVersion": 1,
        "images": images,
    }

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def _download_image(url: str) -> Dict[str, str] | None:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        )

        with urlopen(req, timeout=20) as res:
            data = res.read()
            content_type = res.headers.get_content_type()

        if not content_type.startswith("image/"):
            guessed, _ = mimetypes.guess_type(url)
            content_type = guessed or ""

        if not content_type.startswith("image/"):
            return None

        return {
            "url": url,
            "mime": content_type,
            "data": base64.b64encode(data).decode("ascii"),
        }

    except Exception as e:
        log("画像取得失敗: %s: %s" % (url, e))
        return None
        
def run_site(
    site,
    gcfg,
    fetcher: Fetcher,
    args,
    image_cache: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    state = SiteState(gcfg.state_dir, site.id)
    max_items = min(site.max_items, int(gcfg.get("maxItemsPerSite", 80)))
    max_new = args.limit if args.limit else int(gcfg.get("maxNewArticlesPerRun", 40))
    body_max = int(gcfg.get("bodyMaxChars", 20000))
    body_min = int(gcfg.get("bodyMinChars", 200))
    recheck_ms = int(gcfg.get("recheckHours", 24)) * 3600 * 1000
    now = int(time.time() * 1000)

    # --limit が指定された試験実行では、一覧もその件数まで取得して
    # ページネーションを確認できるようにする。
    # 通常運用では max_items 件まで候補を集める。
    listing_limit = args.limit if args.limit else max_items

    candidates, errors = collect(fetcher, site, listing_limit + args.offset, state)
    candidates = candidates[args.offset:]
    log("%s: 候補 %d件（一覧の更新なし=%s）" % (site.id, len(candidates), not candidates))

    fetched = updated = added = skipped = 0
    for cand in candidates:
        key = normalize_url(cand.url)
        if not key:
            continue
        old = state.article(key)

        # ---- 取りに行くかどうかの判定（ここが「更新がある記事のみ」の中心） ----
        need = False
        if old is None:
            need = True                                    # 新規
        elif title_key(old.get("title", "")) != title_key(cand.title or old.get("title", "")):
            need = True                                    # 見出しが変わった＝記事が差し替わった
        elif cand.published_at and cand.published_at > int(old.get("publishedAt") or 0):
            need = True                                    # フィードの日付が進んだ
        elif not old.get("bodyBlocks") and site.allow_full_text:
            need = True                                    # 前回本文が取れていない
        elif now - int(old.get("checkedAt") or 0) > recheck_ms:
            need = True                                    # 一定時間ごとの再確認（条件付きGETで304なら無害）

        if not need:
            skipped += 1
            continue
        if fetched >= max_new:
            skipped += 1
            continue

        if not site.allow_full_text:
            # 再配信を許さない媒体は本文を取りに行かない。見出し・概要・画像URLだけ配信する。
            record = {
                "key": key, "id": old.get("id") if old else sha(key, length=12),
                "url": cand.url, "title": cand.title or (old or {}).get("title", ""),
                "summary": clean_text(cand.summary)[:400], "imageUrl": cand.image_url,
                "publishedAt": cand.published_at or (old or {}).get("publishedAt") or now,
                "updatedAt": now, "checkedAt": now, "bodyBlocks": [],
                "etag": "", "lastModified": "",
            }
            record["hash"] = sha(record["title"], record["summary"], record["imageUrl"], length=16)
            if old is None:
                added += 1
            elif old.get("hash") != record["hash"]:
                updated += 1
            state.put_article(key, record)
            continue

        res = fetcher.get(cand.url, (old or {}).get("etag", ""), (old or {}).get("lastModified", ""))
        fetched += 1
        if res.not_modified and old:
            old["checkedAt"] = now
            state.put_article(key, old)
            skipped += 1
            continue
        if not res.ok:
            errors.append("%s 記事取得失敗(%s): %s" % (site.id, res.status, cand.url))
            if old:
                old["checkedAt"] = now
                state.put_article(key, old)
            continue

        ex = extract(res.text, cand.url, site.article, body_max)
        blocks = ex.blocks
        if "itainews.com" in cand.url:
            log("itainews blocks: %s" % json.dumps(blocks, ensure_ascii=False))
        for block in blocks:
            image_urls = block.pop("imageUrls", [])
            if not image_urls:
                continue

            image_ids = []

            for image_url in image_urls:
                image_id = _image_id(image_url)
                image_ids.append(image_id)

                if image_url not in image_cache:
                    image_data = _download_image(image_url)
                    if image_data:
                        image_cache[image_url] = {
                            "id": image_id,
                            "url": image_data["url"],
                            "mime": image_data["mime"],
                            "data": image_data["data"],
                        }

            if image_ids:
                block["imageIds"] = [
                    image_id
                    for image_id in image_ids
                    if any(
                        image.get("id") == image_id
                        for image in image_cache.values()
                    )
                ]

        # 痛いニュースは専用抽出で
        # 「記事本文＋5chコメント」を取得するため、
        # 汎用の本文文字数チェックで結果を破棄しない。
        if "itainews.com" not in cand.url:
            if sum(len(b["text"]) for b in blocks) < body_min:
                # 本文が薄い（JavaScript組み立て・有料記事など）。
                # 見出しと概要だけ配信する。
                blocks = []
        title = cand.title or ex.title
        summary = _summary_from_blocks(blocks, clean_text(cand.summary))
        image = cand.image_url or ex.image_url
        published = cand.published_at or ex.published_at or (old or {}).get("publishedAt") or now
        record = {
            "key": key,
            "id": (old or {}).get("id") or sha(key, length=12),
            "url": cand.url,
            "title": clean_text(title),
            "summary": summary,
            "imageUrl": image,
            "publishedAt": int(published),
            "updatedAt": now,
            "checkedAt": now,
            "bodyBlocks": blocks,
            "etag": res.etag,
            "lastModified": res.last_modified,
        }
        record["hash"] = sha(record["title"], record["summary"], blocks_hash(blocks), length=16)
        if old is None:
            added += 1
        elif old.get("hash") != record["hash"]:
            updated += 1
        else:
            skipped += 1
        state.put_article(key, record)

    removed = state.prune(int(gcfg.get("keepDays", 14)), max_items)

    payload = site_payload(
        site,
        state.articles(),
        bool(gcfg.get("includeBody", True)),
    )
    written = False
    if not args.dry_run:
        state.save()
        written, _ = write_site(gcfg.publish_dir, payload)

    log("%s: 新規%d 更新%d 据置%d 取得%d 削除%d rev=%s 書込=%s"
        % (site.id, added, updated, skipped, fetched, removed, payload["rev"], written))

    return {
        "id": site.id, "name": site.name, "category": site.category,
        "file": "sites/%s.json" % site.id, "count": payload["count"],
        "rev": payload["rev"], "updatedAt": payload["generatedAt"],
        "allowFullText": site.allow_full_text,
        "added": added, "updated": updated, "fetched": fetched,
        "errors": errors,
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", action="append", default=[], help="対象サイトID（複数指定可）")
    ap.add_argument("--dry-run", action="store_true", help="public/ と state/ を書かない")
    ap.add_argument("--limit", type=int, default=0, help="1サイトの新規取得件数の上限")
    ap.add_argument("--validate", action="store_true", help="設定の検証だけ行う")
    ap.add_argument("--offset", type=int, default=0, help="候補を先頭から何件飛ばすか")
    args = ap.parse_args(argv)

    gcfg = cfgmod.load_global()
    sites = cfgmod.load_sites(args.site or None)
    problems = cfgmod.validate(sites)
    if problems:
        for p in problems:
            log("設定エラー: %s" % p)
        return 1
    if args.validate:
        log("設定OK: %d サイト" % len(sites))
        return 0

    fetcher = Fetcher(gcfg)
    image_cache = _load_image_cache(gcfg.state_dir)

    entries: List[Dict[str, Any]] = []
    all_errors: List[str] = []
    for site in sites:
        if not site.enabled:
            log("%s: enabled=false のため実行しない" % site.id)
            continue
        try:
            entry = run_site(
                site,
                gcfg,
                fetcher,
                args,
                image_cache,
            )
        except Exception as e:  # noqa: BLE001 1サイトの事故で全体を止めない
            log("%s: 想定外の失敗 %s" % (site.id, e))
            all_errors.append("%s: %s" % (site.id, e))
            continue
        all_errors += entry.pop("errors", [])
        entries.append(entry)

    if not args.dry_run:
        _save_image_cache(gcfg.state_dir, image_cache)

        written, image_bytes = write_images(
            gcfg.publish_dir,
            image_cache,
        )

        log(
            "images.json: %s %.1f MB"
            % (
                "書込" if written else "据置",
                image_bytes / 1024 / 1024,
            )
        )
    if entries and not args.dry_run:
        index_rev = write_index(gcfg.publish_dir, [
            {k: v for k, v in e.items() if k in
             ("id", "name", "category", "file", "count", "rev", "updatedAt", "allowFullText")}
            for e in entries
        ])
        log("index.json rev=%s サイト数=%d" % (index_rev, len(entries)))

    log("通信 %d回 / %.1f MB" % (fetcher.requests_made, fetcher.total_bytes / 1024 / 1024))
    if all_errors:
        log("警告 %d件:" % len(all_errors))
        for e in all_errors[:40]:
            log("  - %s" % e)
    # 一部サイトの失敗でワークフローを赤くしない（全滅の時だけ失敗扱い）
    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
