# -*- coding: utf-8 -*-
"""記事ページから 見出し・本文ブロック・画像・日付 を抜き出す。

■基本方針
1. サイト個別の bodySelectors があれば、それを優先する。
2. 痛いニュースは専用抽出を行う。
3. それ以外は汎用の本文ブロック抽出を行う。
4. 本文抽出に失敗した場合、bodySelectors が明示されていれば
   無理にページ全体から本文を推定しない。
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings(
    "ignore",
    category=XMLParsedAsHTMLWarning,
)

from .normalize import clean_text, trim_blocks


# 汎用本文として扱うタグ
BLOCK_TAGS = (
    "h1",
    "h2",
    "h3",
    "h4",
    "p",
    "li",
    "blockquote",
)


# 明確なノイズ
NOISE_WORDS = (
    "関連記事",
    "おすすめ記事",
    "広告",
    "スポンサー",
    "この記事のURL",
    "share on",
    "follow us",
)


@dataclass
class Extracted:
    title: str = ""
    blocks: List[dict] = field(default_factory=list)
    image_url: str = ""
    published_at: int = 0
    dropped: List[str] = field(default_factory=list)

    @property
    def body_chars(self) -> int:
        return sum(
            len(b["text"])
            for b in self.blocks
        )


def _kind_of(tag_name: str) -> str:
    """HTMLタグからアプリ用block種別へ変換する。"""
    tag_name = tag_name.lower()

    if tag_name.startswith("h"):
        return "H"

    if tag_name == "li":
        return "LI"

    if tag_name == "blockquote":
        return "QUOTE"

    return "P"


# ============================================================
# 痛いニュース専用
# ============================================================

def _itainews_comment_header(text: str) -> bool:
    """痛いニュースの5chレス開始行か判定する。

    対応例:

        1 名前：爆笑ゴリラ ★：2026/09/06(日) ...
        2: パレオくん ...
        103: 買いトリーマン ...
    """
    text = clean_text(text)

    if not text:
        return False

    return bool(
        re.match(
            r"^\d+\s*(?:名前\s*[：:]|[：:])\s*.+",
            text,
        )
    )


def _itainews_stop_line(text: str) -> bool:
    """5ch本文終了位置か判定する。"""
    text = clean_text(text)

    return bool(
        re.match(
            r"^元スレ\s*[：:]",
            text,
        )
    )


def _itainews_add_block(
    out: List[dict],
    kind: str,
    text: str,
    min_chars: int,
    dropped: List[str],
) -> None:
    """痛いニュース用blockを追加する。"""
    text = clean_text(text)

    if not text:
        return

    # URLだけの行
    if re.fullmatch(r"https?://\S+", text):
        dropped.append(
            "url-only: " + text[:40]
        )
        return

    # 明確なノイズ
    low = text.lower()

    if any(
        word.lower() in low
        for word in NOISE_WORDS
    ):
        dropped.append(
            "noise: " + text[:40]
        )
        return

    # Pだけ短すぎる場合は除外
    if (
        kind == "P"
        and len(text) < max(min_chars, 1)
    ):
        dropped.append(
            "short: " + text[:40]
        )
        return

    out.append(
        {
            "kind": kind,
            "text": text,
        }
    )


ITAINEWS_COMMENT_RE = re.compile(
    r"^\s*\d+\s*(?:名前\s*[：:]|[：:])"
)

ITAINEWS_STOP_RE = re.compile(
    r"^\s*元スレ\s*[：:]"
)

ITAINEWS_IMAGE_RE = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:jpe?g|png|gif|webp)(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)

ITAINEWS_BODY_SELECTORS = (
    "#articlebody .blogbody > div.main.entry-content",
    "#articlebody .main.entry-content",
    ".main.entry-content",
)


def _itainews_blocks(node, min_chars, dropped):
    work = BeautifulSoup(str(node), "lxml")

    for tag in work.select(
        "script, style, noscript, iframe, form, nav, footer, aside, "
        "h1, h2, h3, h4, h5, h6"
    ):
        tag.decompose()

    twitter_quotes = []

    for blockquote in work.find_all("blockquote"):
        classes = blockquote.get("class") or []

        if "twitter-tweet" not in classes:
            continue

        text = clean_text(
            blockquote.get_text(" ", strip=True)
        )

        if not text:
            blockquote.decompose()
            continue

        index = len(twitter_quotes)
        twitter_quotes.append(text)

        token = f"__ITAINEWS_TWITTER_QUOTE_{index}__"

        blockquote.replace_with(
            work.new_string(f"\n{token}\n")
        )

    raw_text = work.get_text("\n", strip=False)

    raw_text = (
        raw_text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    blocks = []
    current_comment = []

    def flush_comment():
        if not current_comment:
            return

        text = clean_text(" ".join(current_comment))

        if text:
            block = {
                "kind": "P",
                "text": text,
            }

            image_urls = []
            for url in ITAINEWS_IMAGE_RE.findall(text):
                if url not in image_urls:
                    image_urls.append(url)

            if image_urls:
                block["imageUrls"] = image_urls

            blocks.append(block)

        current_comment.clear()
        
    for raw_line in raw_text.split("\n"):
        line = clean_text(raw_line)

        if not line:
            continue

        if ITAINEWS_STOP_RE.match(line):
            flush_comment()
            break

        m = re.fullmatch(
            r"__ITAINEWS_TWITTER_QUOTE_(\d+)__",
            line,
        )

        if m:
            flush_comment()

            index = int(m.group(1))

            if 0 <= index < len(twitter_quotes):
                quote = twitter_quotes[index]

                if quote:
                    blocks.append({
                        "kind": "QUOTE",
                        "text": quote,
                    })

            continue

        if ITAINEWS_COMMENT_RE.match(line):
            flush_comment()
            current_comment.append(line)
            continue

        if current_comment:
            current_comment.append(line)
            continue

        if re.fullmatch(
            r"https?://\S+",
            line,
            flags=re.IGNORECASE,
        ):
            dropped.append("url-only: " + line[:80])
            continue

        if len(line) >= max(min_chars, 1):
            blocks.append({
                "kind": "P",
                "text": line,
            })

    flush_comment()

    return blocks
    
# ============================================================
# 汎用本文抽出
# ============================================================

def _blocks_from(
    node,
    min_chars: int,
    stop_texts: List[str],
    dropped: List[str],
) -> List[dict]:
    """指定された本文コンテナから本文ブロックだけを抽出する。"""

    out: List[dict] = []

    # --------------------------------------------------------
    # 本文内ノイズ
    # --------------------------------------------------------
    noise_selectors = [
        "script",
        "style",
        "noscript",
        "iframe",
        "form",
        "nav",
        "footer",
        "aside",
        ".share",
        ".sharing",
        ".social",
        ".sns",
        ".related",
        ".recommend",
        ".recommended",
        ".advertisement",
        ".ads",
        ".ad",
    ]

    for sel in noise_selectors:
        try:
            for el in node.select(sel):
                el.decompose()
        except (
            ValueError,
            TypeError,
        ):
            continue

    # --------------------------------------------------------
    # 本文ブロック
    # --------------------------------------------------------
    for el in node.find_all(BLOCK_TAGS):

        # 親blockの子なら重複するので除外
        parent = el.parent
        nested_block = False

        while (
            parent is not None
            and parent is not node
        ):
            if (
                getattr(parent, "name", None)
                in BLOCK_TAGS
            ):
                nested_block = True
                break

            parent = parent.parent

        if nested_block:
            continue

        text = clean_text(
            el.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue

        # URLだけ
        if re.fullmatch(
            r"https?://\S+",
            text,
        ):
            dropped.append(
                "url-only: " + text[:40]
            )
            continue

        low = text.lower()

        # ノイズ
        if any(
            word.lower() in low
            for word in NOISE_WORDS
        ):
            dropped.append(
                "noise: " + text[:40]
            )
            continue

        # 停止文字列
        if any(
            stop
            and stop.lower() in low
            for stop in stop_texts
        ):
            dropped.append(
                "stopAtText: " + text[:40]
            )
            break

        kind = _kind_of(
            el.name.lower()
        )

        # 短すぎるP
        if (
            kind == "P"
            and len(text)
            < max(min_chars, 1)
        ):
            dropped.append(
                "short: " + text[:40]
            )
            continue

        # リンクだけ
        links = el.find_all("a")

        if links:
            link_text = clean_text(
                " ".join(
                    a.get_text(
                        " ",
                        strip=True,
                    )
                    for a in links
                )
            )

            if (
                link_text
                and link_text == text
            ):
                dropped.append(
                    "link-only: "
                    + text[:40]
                )
                continue

        out.append(
            {
                "kind": kind,
                "text": text,
            }
        )

    return out


# ============================================================
# 共通処理
# ============================================================

def _drop_nodes(
    soup: BeautifulSoup,
    selectors: List[str],
    dropped: List[str],
) -> None:
    """指定セレクタのノードを削除する。"""

    for sel in selectors:
        try:
            for el in soup.select(sel):
                dropped.append(
                    "dropSelector %s" % sel
                )
                el.decompose()

        except Exception:
            dropped.append(
                "dropSelector（無効なセレクタ）: %s"
                % sel
            )


def _densest(
    soup: BeautifulSoup,
    min_chars: int,
    stop_texts: List[str],
    dropped: List[str],
) -> List[dict]:
    """汎用推定。

    段落文字数が最も多い入れ物を本文とみなす。
    """

    best = None
    best_len = 0

    for node in soup.find_all(
        [
            "article",
            "main",
            "div",
            "section",
        ]
    ):
        length = sum(
            len(
                clean_text(
                    p.get_text(
                        " ",
                        strip=True,
                    )
                )
            )
            for p in node.find_all(
                ("p", "li")
            )
        )

        link_chars = sum(
            len(
                clean_text(
                    a.get_text(
                        " ",
                        strip=True,
                    )
                )
            )
            for a in node.find_all("a")
        )

        if (
            length > best_len
            and (
                length == 0
                or link_chars
                / max(length, 1)
                < 0.5
            )
        ):
            best = node
            best_len = length

    target = best or soup

    return _blocks_from(
        target,
        min_chars,
        stop_texts,
        dropped,
    )


# ============================================================
# メイン
# ============================================================

def extract(
    html: str,
    url: str,
    article_cfg: dict,
    max_chars: int,
) -> Extracted:

    soup = BeautifulSoup(
        html,
        "lxml",
    )

    dropped: List[str] = []

    # --------------------------------------------------------
    # ページ全体の不要要素
    # --------------------------------------------------------
    _drop_nodes(
        soup,
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "form",
        ],
        dropped,
    )

    _drop_nodes(
        soup,
        article_cfg.get(
            "dropSelectors"
        ) or [],
        dropped,
    )

    # --------------------------------------------------------
    # タイトル
    # --------------------------------------------------------
    title = ""

    title_selectors = (
        article_cfg.get(
            "titleSelectors"
        )
        or ["h1"]
    ) + [
        "meta[property='og:title']",
        "title",
    ]

    for sel in title_selectors:
        try:
            el = soup.select_one(sel)
        except Exception:
            continue

        if el is None:
            continue

        if el.name == "meta":
            title = clean_text(
                el.get("content") or ""
            )
        else:
            title = clean_text(
                el.get_text(
                    " ",
                    strip=True,
                )
            )

        if title:
            break

    # --------------------------------------------------------
    # 本文
    # --------------------------------------------------------
    min_chars = int(
        article_cfg.get(
            "minParagraphChars",
            0,
        )
        or 0
    )

    stop_texts = (
        article_cfg.get(
            "stopAtTexts"
        )
        or []
    )

    blocks: List[dict] = []

    body_selectors = (
        article_cfg.get(
            "bodySelectors"
        )
        or []
    )

    for sel in body_selectors:

        try:
            node = soup.select_one(sel)
        except Exception:
            dropped.append(
                "bodySelector（無効なセレクタ）: %s"
                % sel
            )
            continue

        if node is None:
            dropped.append(
                "bodySelector-not-found: %s"
                % sel
            )
            continue

        # ----------------------------------------------------
        # 痛いニュース
        #
        # セレクタ文字列に依存しない。
        # URLだけでサイト判定する。
        #
        # これにより config 側のselectorを少し変更しても
        # 専用抽出が使われる。
        # ----------------------------------------------------
        if "itainews.com" in url:
            blocks = _itainews_blocks(
                node,
                min_chars,
                dropped,
            )
        else:
            blocks = _blocks_from(
                node,
                min_chars,
                stop_texts,
                dropped,
            )

        if blocks:
            break

    # --------------------------------------------------------
    # bodySelectorsが未指定の場合のみ汎用推定
    # --------------------------------------------------------
    if not blocks and not body_selectors:
        blocks = _densest(
            soup,
            min_chars,
            stop_texts,
            dropped,
        )

    if not blocks and body_selectors:
        dropped.append(
            "body-not-found"
        )

    # --------------------------------------------------------
    # 画像
    # --------------------------------------------------------
    image = ""

    for sel in (
        article_cfg.get(
            "imageSelectors"
        )
        or []
    ):
        try:
            el = soup.select_one(sel)
        except Exception:
            continue

        if el is None:
            continue

        image = (
            el.get("content")
            or el.get("src")
            or ""
        ).strip()

        if image:
            image = urljoin(
                url,
                image,
            )
            break

    # --------------------------------------------------------
    # 公開日時
    # --------------------------------------------------------
    published = 0

    from .listing import parse_date_ms

    for sel in (
        article_cfg.get(
            "dateSelectors"
        )
        or []
    ):
        try:
            el = soup.select_one(sel)
        except Exception:
            continue

        if el is None:
            continue

        raw = (
            el.get("content")
            or el.get("datetime")
            or el.get("title")
            or el.get_text(
                " ",
                strip=True,
            )
        )

        published = parse_date_ms(
            raw or ""
        )

        if published:
            break

    # JSON-LD fallback
    if not published:
        m = re.search(
            r'"datePublished"\s*:\s*"([^"]+)"',
            html,
        )

        if m:
            published = parse_date_ms(
                m.group(1)
            )

    # --------------------------------------------------------
    # 完了
    # --------------------------------------------------------
    return Extracted(
        title=title,
        blocks=trim_blocks(
            blocks,
            max_chars,
        ),
        image_url=(
            image
            if image.startswith("https://")
            else ""
        ),
        published_at=published,
        dropped=dropped[:30],
    )