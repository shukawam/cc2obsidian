# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**開発規約・構造・不変条件は `AGENTS.md` を正とする。作業前に必ず読むこと。** このファイルには Claude Code 固有の事情だけを置く。

## Claude Code 固有のこと

このリポジトリは Claude Code と Codex の両方のセッションを扱う。`cc2obsidian/parse.py` が Claude Code 用の adapter、`cc2obsidian/parse_codex.py` が Codex 用の adapter で、両者は共通の `Session` 中間表現に合流する。片方だけを直したつもりでも `render.py` 以降は共有なので、変更の影響範囲は両 source に及ぶ。

- 探索元は `~/.claude/projects/<project>/<session_id>.jsonl`。ファイル名の stem が session_id
- hook は `~/.claude/settings.json` の `SessionEnd` に登録し、`scripts/cc2obsidian.py hook --source claude-code` を呼ぶ。`timeout` は 30 秒（省略すると `SessionEnd` 全体で 1.5 秒の予算を共有し、超過すると外部から打ち切られてログにも残らない）
- state（`~/.claude/cc2obsidian-state.json`）とエラーログ（`~/.claude/cc2obsidian.log`）は Codex と共有する。Claude Code 側の既定パスがそのまま両 source の既定になっている
- `--source` を省略した CLI 呼び出しは `claude-code` として扱われる。既存の hook 登録や手順書を壊さないための後方互換であって、新しく書くものには明示すること

Codex 側の hook 登録（`~/.codex/hooks.json`、timeout 3 秒）と制約は `README.md` にある。

## スキル

`skills/weekly-review/`（週次の傾向分析）と `skills/dreaming/`（日次の深掘り復習）は `~/.claude/skills/` からシンボリックリンクされている。ここを編集すると、そのまま稼働中のスキルが変わる。`digest` の出力形式を変えるときは SKILL.md 側の手順とも整合を取ること。dreaming が書く `Notes/daily/<YYYY-MM-DD>.md` のセクション構成は、weekly-review が日次ノートを集約する前提で固定されている。
