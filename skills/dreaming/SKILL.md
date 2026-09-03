---
name: dreaming
description: Use when the user wants an in-depth review of today's (or one specific day's) Claude Code and Codex sessions - triggers on "今日の振り返り", "日次振り返り", "dreaming", "daily review", or consolidating the day's work at the end of the day. For multi-day trend analysis use weekly-review instead.
---

# dreaming — 日次深掘り復習

その日の Claude Code / Codex セッションをノート全文レベルで復習し、詰まり・手戻り・うまくいった型・知見を日次ノートに固定化する。週次（weekly-review）が digest 経由の傾向分析なのに対し、日次はセッション数が少ないのでノート全文まで踏み込めるのが存在意義。

## 手順

### 1. 対象日を決める

既定は今日。ユーザーが日付を指定したらそれに従う。

**前日開始で対象日まで続いたセッションも対象。** ノートは開始日のディレクトリ（`Notes/raw/<開始日>/`）に入るため、対象日のディレクトリだけ見ると夜をまたいだ長時間セッションを取りこぼす。前日分は「開始時刻 + `duration_min` が対象日に食い込むもの」だけ拾い、ノートでは持ち越しと明記する。

### 2. バックフィルする

`SessionEnd` hook は強制終了・進行中セッションでは発火しないので、その日のセッションの大半が Vault に無いことは普通に起きる。Codex の `SessionEnd` はさらに、client を閉じて idle になるまで（最大 30 分）発火しないことがある。**必ず backfill で回収する。生 JSONL（`~/.claude/projects/`、`~/.codex/sessions/`）や cc2obsidian の内部 API を直接触らないこと** — CLI を迂回すると state（`~/.claude/cc2obsidian-state.json`）との整合が壊れる。

`--source all` を必ず付ける。省略すると後方互換で `claude-code` だけになり、その日の Codex セッションが丸ごと落ちる。

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --source all --since 1
```

対象日が過去なら `--since` を「今日 − 対象日 + 1」日に広げる。

### 3. ダイジェストで一覧を得る

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py digest --since 1
```

`--since 1` は前日ディレクトリ〜今日を拾う（手順 1 の持ち越し判定に前日分が要る）。対象日が過去なら backfill と同じ日数にし、対象外の日付のセッションは読み飛ばす。「対象なし」が返ったら `backfill --source all --all` を勧めて終了する。

### 4. トリアージして全文を読む

digest のメタデータで深掘り対象を選ぶ。

- **外す**: `user_turns` が 1 以下で `duration_min` が数分以内の自明なセッション（ログインだけ、単発の一問一答で完結など）。概況で 1 行触れる程度に留める。ただし知見の裏取りに必要ならノートを読んでよい（Knowledge 候補の出典には使える。深掘りセクションには入れない）
- **全文を読む**: 残りのセッションは `Notes/raw/<日付>/` のノートを 1 件ずつ Read する
- **harness をまたいで見る**: digest の `source` で Claude Code と Codex を見分けられる。同じテーマを両方で追いかけた日は、どちらで何が進んだかを突き合わせる
- **巨大ノートはフォールバック**: 目安 50KB 超は全文を読まない。digest の発話一覧 + 末尾（結論部）+ 見出し・キーワードの grep で要所だけ拾う

Vault パスは既定 `~/private/obsidian/Obsidian`、環境変数 `CC2OBSIDIAN_VAULT` があればそちら。

### 5. 深掘りする

セッションごとに次の観点で読む。**根拠のない主張を書かないこと。** 各項目には出典の `[[wikilink]]` を添える。リンク名は digest が出す表記（`[[<ファイル名>]]`）をそのまま使う — 自分でリンク名を発明しない。

1. **詰まった箇所と原因** — エラーの繰り返し、長引いた調査、環境起因の中断
2. **手戻りの経緯** — やり直しになった判断と、どこで防げたか
3. **うまくいった型** — 成果につながった指示の仕方・手順・レビューの構え
4. **次はどう頼むか** — 同じタスクをもう一度やるならプロンプト・段取りをどう変えるか
5. **得た知見** — 他プロジェクトでも効く知識（Knowledge 候補）

### 6. 日次ノートを書く

出力先は `<Vault>/Notes/daily/<YYYY-MM-DD>.md`。**この形式から変えない** — セクション構成は weekly-review が日次ノートを集約する前提で揃えてある。

```markdown
---
date: 2026-08-28
sessions: 7
tags: [claude-code/daily]
---

# 日次振り返り 2026-08-28

## 今日の概況
（主要な流れを 2〜4 本に要約。自明セッションはここで 1 行）

## セッション別の深掘り
（深掘り対象のみ。### [[wikilink]] 見出しで 1 セッションずつ）

## 詰まり・手戻り

## うまくいった型

## スキル化の芽

## Knowledge 候補

## 明日への持ち越し
```

`sessions` は対象セッションの総数（自明なもの・前日からの持ち越しも含む）。

## 禁止事項

- backfill を経由せず生 JSONL や cc2obsidian の内部 API から直接読むこと（state が壊れる）
- 巨大ノートを全文 Read してコンテキストを溢れさせること
- 出典 `[[wikilink]]` を示さずにパターンや知見を主張すること
- ユーザーの承認なく `Knowledge/` にファイルを作ること（候補の提案まで）
- 日次ノートのセクション構成・出力先を独自に変えること（週次集約が壊れる）
