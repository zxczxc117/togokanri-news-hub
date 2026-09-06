# togokanri-news-hub

TogoKanri「情報（ニュース）」ミニアプリ向けの **記事収集・配信JSON生成** リポジトリです。
GitHub Actions が1日3回動き、サイトごとに 50〜100件の記事（見出し・概要・本文・画像URL）を
`public/` 配下のJSONへ書き出します。アプリはそのJSONを読むだけになります。

## なぜこれを作るのか

RSSは媒体によって「見出し＋数行の抜粋」しか返さず、本文も画像も足りません。
端末から記事HTMLを1本ずつ取ると通信量と時間がかかり、サイト構造が変わるたびに
アプリを直すことになります。そこで **集める仕事をGitHub側へ移し、
端末は完成したJSONを読むだけ** にします。

## 3分でわかる全体像

```
GitHub Actions (1日3回)
  └ collector/  ── 一覧(RSS/HTML)を条件付きGET → 更新のある記事だけ本文取得
        │           （state/ に前回の記憶を持つので、更新が無ければ通信しない）
        ↓
  public/index.json         … サイト一覧＋サイトごとの版(rev)
  public/sites/<id>.json    … そのサイトの記事50〜100件（本文・画像URL入り）
        ↓
TogoKanri アプリ（情報タブ）
  └ index.json を見て「版が変わったサイトだけ」本体JSONを取得 → 記事へ取り込む
```

## 最短の手順

1. `docs/00_稼働手順書.md` の順にGitHubへ置く（10〜15分）
2. Actions を手動実行して `public/index.json` ができることを確認
3. アプリの 情報 → 設定 → 「配信JSON（GitHub）」に配信URLを貼って「今すぐ取得」

## フォルダの役割（詳細は docs/01_資産マップ.md）

| 場所 | 役割 | サイト個別調整で触るか |
|---|---|---|
| `config/sites/*.json` | サイトごとの取得元URL・セレクタ・件数 | **ここを触る** |
| `config/global.json` | 全サイト共通の上限・待ち時間 | ときどき |
| `collector/*.py` | 収集の処理本体 | 通常は触らない |
| `.github/workflows/collect.yml` | 1日3回の実行時刻 | 時刻を変えるときだけ |
| `public/` | 配信JSON（自動生成・手で編集しない） | 触らない |
| `state/` | 前回の記憶（自動生成・手で編集しない） | 触らない |
| `samples/`, `collector/selftest.py` | 通信しない机上検証 | セレクタ変更後に実行 |

## 動作確認（通信しない）

```
python -m collector.selftest      # 設定検証＋サンプルHTMLでの抽出確認＋差分判定の確認
python -m collector.main --validate
```

Windowsで手元試験する場合は `tools/local_run.bat` を実行（初回だけ .venv を作ります）。

## 注意（著作権・マナー）

- 個人利用の範囲で使うこと。配信JSONを一般公開・再配布する用途には向きません。
- 再配信を許していない媒体は `allowFullText: false` にしてあり、本文を保存しません
  （見出し・概要・画像URLのみ。アプリでは「元記事を開く」で読む形になります）。
- 相手サイトへの負荷を避けるため、既定で1リクエストごとに1秒待ち、
  1回の実行で新規に本文を取る件数を40件までにしています。
