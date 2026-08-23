# cc2obsidian 設計

Claude Code のセッションを Obsidian の Vault に自動記録し、週次で振り返ってスキル化・ナレッジ化の候補を抽出する仕組み。

- 作成日: 2026-08-23
- ステータス: 設計合意済み

## 1. 目的

Claude Code のセッションは `~/.claude/projects/<cwd-slug>/<session-id>.jsonl` に全量が残っているが、活用されていない。これを Obsidian ネイティブな Markdown として Vault に蓄積し、週次で分析することで次の 2 つを得る。

1. 定型的な作業パターンを見つけてスキル化する
2. 汎用的に再利用できる知見を `Knowledge/` へ昇格させる

## 2. 前提

| 項目 | 値 |
|---|---|
| Vault | `/Users/shuhei.kawamura/private/obsidian/Obsidian/` |
| Vault 構成 | `Excalidraw/` `Knowledge/` `Notes/` |
| Vault の同期 | なし（ローカルのみ、git 管理外） |
| セッションログ | `~/.claude/projects/<cwd-slug>/<session-id>.jsonl` |
| 既存ログ量 | 約 92MB / 30 プロジェクト |
| Python | 3.14.5（stdlib のみ使用、外部依存なし） |
| ログのタイムゾーン | UTC（`timestamp` は ISO8601 の `Z`） |

## 3. スコープの決定事項

ブレインストーミングで合意した内容。

| 論点 | 決定 |
|---|---|
| 記録の粒度 | 会話は全文。ツール呼び出しと結果は `<details>` で折りたたみ、巨大な結果は切り詰め |
| 対象範囲 | 全プロジェクト（顧客案件を含む）。Vault はローカルのみのため機密面の追加リスクなし |
| 過去ログ | 全部遡って取り込む |
| 週次振り返り | 手動スキルのみ（cron は組まない） |

## 4. 成果物

```
~/work/cc2obsidian/                      # 実体
├── scripts/cc2obsidian.py               # 変換スクリプト（hook 兼 CLI）
├── skills/weekly-review/SKILL.md        # 週次振り返りスキル
├── tests/                               # 変換ロジックのテスト
└── docs/superpowers/specs/              # 本ドキュメント

~/.claude/skills/weekly-review -> ~/work/cc2obsidian/skills/weekly-review   # symlink
~/.claude/settings.json                  # SessionEnd hook を追加
~/.claude/cc2obsidian-state.json         # session_id -> 出力パス の対応表
```

`att` スキルと同じ流儀（`~/work/<project>/` に実体を置き、`skills/` 配下を `~/.claude/skills/` へシンボリックリンク）に揃える。

## 5. データフロー

```
セッション終了
  └→ SessionEnd hook (stdin に session_id, transcript_path, cwd, reason)
       └→ cc2obsidian.py hook
            └→ Vault/Notes/2026-08-23/0801-work-スキル作成相談.md

cc2obsidian.py backfill [--all|--since 30d] [--dry-run]   # 遡り取り込み・取りこぼし回収
cc2obsidian.py digest --since 7d                          # 週次スキルが読む軽量ダイジェスト
```

## 6. 出力フォーマット

### 6.1 パス

```
<Vault>/Notes/<YYYY-MM-DD>/<HHMM>-<project-slug>-<title-slug>.md
```

- 日付・時刻はセッション最初のメッセージの timestamp を **JST に変換**したもの
- `project-slug` は `cwd` の末尾ディレクトリ名
- `title-slug` は JSONL 中の `ai-title` エントリの最後の値。存在しない場合は最初のユーザー発話の先頭 30 文字
- スラッシュ・コロンなど Obsidian で問題になる文字は除去する
- 日付ディレクトリは存在しなければ作成する
- 同じ日・同じ時刻・同じプロジェクト・同じタイトルの別セッションが衝突した場合は、末尾に session_id の先頭 8 桁を付与して区別する

### 6.2 frontmatter

```yaml
---
date: 2026-08-23
time: "08:01"
project: work
cwd: /Users/shuhei.kawamura/work
session_id: 472a17cb-1f3b-488d-b335-0f7bdf7de956
title: スキル作成相談
duration_min: 42
user_turns: 5
tool_counts: {Bash: 6, AskUserQuestion: 4}
tags: [claude-code/session, project/work]
---
```

`cwd` が `~/customer/<name>/...` の場合は `customer/<name>` タグも付与する。

### 6.3 本文

~~~~markdown
# スキル作成相談

## 👤 08:01
（ユーザー発話 全文）

## 🤖 08:05
（アシスタントのテキスト 全文）

<details><summary>💭 thinking</summary>

（thinking 全文）
</details>

<details><summary>🔧 Bash — Inspect Obsidian vault structure</summary>

```bash
ls -la "$V"
```
```
(出力 先頭40行)
… 128行省略 …
(末尾10行)
```
</details>
~~~~

### 6.4 整形ルール

| 対象 | 扱い |
|---|---|
| user / assistant のテキスト | 全文をそのまま |
| thinking | `<details>` に畳んで保存（何に迷ったかが週次分析で効くため捨てない） |
| tool_use | `<details>` の summary にツール名と description、本体に入力パラメータ |
| tool_result | 同じ `<details>` の中。60 行を超えたら先頭 40 行 + 末尾 10 行に切り詰め、間に省略行数を明記 |
| サブエージェント（`isSidechain: true`） | `<details>` に畳んで保存 |
| `attachment` / `file-history-*` / `mode` など運用系エントリ | 出力しない |

## 7. 冪等性と取りこぼし対策

### 7.1 state ファイル

`~/.claude/cc2obsidian-state.json` に `session_id → {path, source_mtime}` を保持する。

- 既知の session_id は**同じファイルを上書き**する（セッション再開で重複ノートを作らない）
- タイトルが変わった場合は旧ファイルをリネームしてから書き込む

### 7.2 取りこぼし

`SessionEnd` hook は `exit` / `clear` / `logout` では発火するが、**強制終了やクラッシュでは発火しない**。そのため `backfill` は次の条件でノートを再生成する。

- state に存在しない session_id
- JSONL の mtime が state に記録された `source_mtime` より新しい

`backfill` は初回移行だけでなく、恒久的な取りこぼし回収経路として機能する。

## 8. 週次振り返りスキル

### 8.1 digest を噛ませる理由

1 週間分のノートをそのまま読むとコンテキストが溢れる（数十セッション）。`digest` サブコマンドが期間内のノートから次の情報だけを抽出し、1 本のテキストに集約する。

- 各ノートの frontmatter
- ユーザー発話の全文
- ツール使用統計

スキルはこれを 1 回読んで分析する。

### 8.2 スキルの出力

`<Vault>/Notes/weekly/<YYYY>-W<ww>.md` に次の 4 セクションを書く。

1. **繰り返し出現した作業パターン** — スキル化候補。根拠となるセッションノートへ `[[wikilink]]` を張る
2. **汎用的に再利用できる知見** — `Knowledge/` 昇格候補。**提案のみ**を書き、実ファイルの作成はユーザー承認後に行う
3. **詰まった箇所・手戻り** — 改善余地
4. **プロジェクト別の時間配分**

期間はデフォルトで直近 7 日。引数で明示指定もできる。

## 9. テスト方針

変換ロジック（切り詰め、slug 化、JST 変換、frontmatter 生成）は実データに依存しない純粋関数として切り出し、テストを書く。

導入手順は次の順で行う。hook を先に登録すると、不具合があった場合にセッション終了のたびにエラーが出るため。

1. `backfill --dry-run` を流し、既存セッションが期待通り Markdown 化されるか目視確認
2. 問題なければ `backfill --all` を本実行
3. 最後に `SessionEnd` hook を `~/.claude/settings.json` に登録

登録する hook は次の形。既存の `PreToolUse` (rtk) はそのまま残す。

```json
"SessionEnd": [
  {
    "hooks": [
      { "type": "command", "command": "python3 ~/work/cc2obsidian/scripts/cc2obsidian.py hook" }
    ]
  }
]
```

hook は失敗してもセッション終了を妨げないよう、例外を握りつぶして常に exit 0 で返す。エラーは `~/.claude/cc2obsidian.log` に追記する。

## 10. やらないこと

- cron / スケジュール実行（手動スキルのみ）
- 除外リスト・許可リストによる記録対象の絞り込み（全プロジェクトを記録する）
- Vault の git 管理や同期設定の変更
- `Knowledge/` への自動昇格（週次スキルは提案までを行う）
