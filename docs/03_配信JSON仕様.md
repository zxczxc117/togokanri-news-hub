# 配信JSON仕様（アプリとの取り決め）

配信側（Python）とアプリ側（Kotlin）の境界です。**項目を増減するときは両方を直します。**
アプリ側の受け取り型は `NewsHubModels.kt` にあります。

---

## 1. public/index.json

アプリが最初に読むファイル。サイト一覧と**版(rev)**だけを持つので数KBです。

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-09-02T19:50:11+09:00",
  "rev": "9f2c1a0b7d31",
  "sites": [
    {
      "id": "4gamer",
      "name": "4Gamer.net",
      "category": "GAME",
      "file": "sites/4gamer.json",
      "count": 80,
      "rev": "3c7b91ee0a42",
      "updatedAt": "2026-09-02T19:50:08+09:00",
      "allowFullText": true
    }
  ]
}
```

| 項目 | 型 | 用途 |
|---|---|---|
| `schemaVersion` | int | 形を変えたら上げる。アプリは未知の項目を無視する作りなので通常は1のまま |
| `generatedAt` | string | 表示用 |
| `rev` | string | 全サイトの版の集約。1つでも更新があれば変わる |
| `sites[].id` | string | サイトID。アプリの取得元IDは `hub_<id>` |
| `sites[].category` | string | GENERAL / TECH / GAME / STOCK / MATOME |
| `sites[].file` | string | index.json から見た相対パス |
| `sites[].rev` | string | **差分判定の要**。前回と同じならアプリは本体JSONを取得しない |
| `sites[].allowFullText` | bool | falseなら本文なし（見出し・概要のみ） |

## 2. public/sites/&lt;id&gt;.json

```json
{
  "schemaVersion": 1,
  "siteId": "4gamer",
  "name": "4Gamer.net",
  "category": "GAME",
  "allowFullText": true,
  "rev": "3c7b91ee0a42",
  "generatedAt": "2026-09-02T19:50:08+09:00",
  "count": 80,
  "articles": [
    {
      "id": "b1c2d3e4f5a6",
      "url": "https://www.4gamer.net/games/999/G999999/20260902001/",
      "title": "記事の見出し",
      "summary": "一覧に出す概要（最大400文字）",
      "imageUrl": "https://.../thumb.jpg",
      "publishedAt": 1788400000000,
      "updatedAt": 1788400500000,
      "hash": "7d5a1e93c0b24f18",
      "bodyBlocks": [
        { "kind": "H", "text": "小見出し" },
        { "kind": "P", "text": "本文の段落" },
        { "kind": "LI", "text": "箇条書き" },
        { "kind": "QUOTE", "text": "引用" }
      ]
    }
  ]
}
```

| 項目 | 型 | 備考 |
|---|---|---|
| `articles[].id` | string | 配信側の安定ID（正規化URLのハッシュ）。端末の記事IDとは別 |
| `articles[].url` | string | 元記事のURL。端末側の重複判定はこれを正規化して行う |
| `articles[].publishedAt` | long | epoch millis。読めない場合は取得時刻 |
| `articles[].hash` | string | 見出し＋概要＋本文のハッシュ。**同じなら端末は何も触らない** |
| `articles[].bodyBlocks` | array | `kind` は H / P / LI / QUOTE（アプリの `HtmlText.Kind` と同じ）。本文なしは空配列 |

並び順は `publishedAt` の降順です。件数は `maxItems`（既定80、要望に沿って50〜100）。

## 3. 差分の効かせ方（二段構え）

```
配信側（GitHub Actions・1日3回）
  ├ 一覧を条件付きGET（If-None-Match / If-Modified-Since）
  │   → 304 ならそのサイトは以降の処理をしない
  ├ 記事ごとに state と突き合わせ
  │   新規 / 見出しが変わった / 日付が進んだ / 本文未取得 / 24時間経過 → 取得
  │   それ以外 → 通信しない
  └ 内容が変わらなければ sites/<id>.json を書き直さない（＝revも変わらない）

アプリ側（更新ボタン・定時取込）
  ├ index.json だけ取得
  ├ rev が前回と同じサイトは本体JSONを取得しない
  └ 取得したサイトでも hash が同じ記事は書き換えない（既読・お気に入りが保たれる）
```

更新が無い回のアプリ側の通信は **index.json の数KBだけ** です。

## 4. 項目を増やすときの手順

1. `collector/publisher.py` の `site_payload()` に項目を足す
2. `docs/03_配信JSON仕様.md`（この文書）を直す
3. アプリ側 `NewsHubModels.kt` に **既定値付きで** フィールドを足す
4. 使う側（`NewsHubRepository.kt`）で反映する

3の既定値を必ず付けるのが要点です。付けておけば、配信側だけ先に更新しても
古いアプリが読めなくなりません（`Store.json` は `ignoreUnknownKeys = true`）。
