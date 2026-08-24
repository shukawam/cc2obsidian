---
name: weekly-review
description: Use when the user wants to look back at their recent Claude Code sessions to find repeated work worth turning into a skill or knowledge worth promoting - triggers on "週次振り返り", "今週の振り返り", "weekly review", "最近の作業を分析して", or asking what patterns show up in their recent sessions.
---

# 週次振り返り

Obsidian Vault に蓄積された Claude Code セッションを読み、定型作業とナレッジ候補を抽出する。

## 手順

### 1. 期間を決める

既定は直近 7 日。ユーザーが期間を指定したらそれに従う。

`digest --since N` が拾うのは「N 日前の日付ディレクトリから今日まで」＝ N+1 暦日ぶん。週次ノートのファイル名は実行日の ISO 週になるので、月曜に走らせると中身の大半は前週になる。週の途中で振り返るなら、対象期間を frontmatter の `period` に必ず明記すること。

### 2. 取りこぼしを回収する

`SessionEnd` は強制終了では発火しない。分析の前に必ずバックフィルして、hook が取りこぼしたセッションを回収する。

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --since <日数>
```

### 3. ダイジェストを取得する

ノートを直接読まないこと。1 週間分の全文はコンテキストに収まらない。既定値 7 日をユーザーの指定期間に置き換える。

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py digest --since <日数>
```

「対象なし」が返ったら、まだノートが無い。バックフィルを勧めて終了する。

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --all
```

### 4. 分析する

ダイジェストを読み、次の 4 点を抽出する。**根拠のないパターンを書かないこと。** 各項目には必ず出典セッションの `[[wikilink]]` を添える。

1. **繰り返し出現した作業パターン** — 2 回以上現れた手順。スキル化候補として、何を自動化できるかまで書く
2. **汎用的に再利用できる知見** — 他のプロジェクトでも効く知識。`Knowledge/` 昇格候補。**提案のみ書く。ファイルは作らない**
3. **詰まった箇所・手戻り** — やり直しや長引いた箇所。次に同じ轍を踏まないための示唆
4. **プロジェクト別の時間配分** — `duration_min` と `project` の集計。使ったモデルの内訳も添える

### 5. 週次ノートを書く

出力先は `<Vault>/Notes/weekly/<ISO年>-W<ISO週番号>.md`。Vault パスは既定 `~/private/obsidian/Obsidian`、環境変数 `CC2OBSIDIAN_VAULT` があればそちら。

ISO 週番号は次で得る。

```bash
python3 -c "from datetime import date; y,w,_ = date.today().isocalendar(); print(f'{y}-W{w:02d}')"
```

ノートの形:

```markdown
---
date: 2026-08-23
period: 2026-08-17 / 2026-08-23
sessions: 12
tags: [claude-code/weekly]
---

# 2026-W34 振り返り

## 繰り返し出現した作業パターン
## 再利用できる知見（Knowledge 昇格候補）
## 詰まった箇所・手戻り
## プロジェクト別の時間配分
```

### 6. 昇格を確認する

`Knowledge/` へ昇格させたい項目があれば、**どれを昇格するかユーザーに確認してから**ファイルを作る。勝手に作らない。

## 禁止事項

- ダイジェストを経由せずノートを直接大量に読むこと（コンテキストが溢れる）
- 出典セッションを示さずにパターンを主張すること
- ユーザーの承認なく `Knowledge/` にファイルを作ること
