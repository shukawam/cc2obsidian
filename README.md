# cc2obsidian

Claude Code と Codex のセッションを Obsidian Vault へ記録し、日次・週次で振り返る。

## 仕組み

- 各 harness の `SessionEnd` hook が `scripts/cc2obsidian.py hook --source ...` を呼ぶ
- Claude Code / Codex それぞれの JSONL adapter が、共通の `Session` 中間表現へ変換する
- 出力先は `<Vault>/Notes/raw/<YYYY-MM-DD>/<HHMM>-<project>-<title>.md`
- 会話は全文、思考とツール呼び出しは `<details>` で折りたたむ。長いツール出力は先頭 40 行 + 末尾 10 行に切り詰める
- frontmatter の `source` と `<source>/session` タグで `claude-code` / `codex` を区別する
- `dreaming` と `weekly-review` スキルが蓄積したセッションを振り返る

## インストール

ノート取得には、利用する harness の `SessionEnd` hook を登録する。以下の `~/work/cc2obsidian` は実際の clone 先に合わせる。

登録前に変換を確認し、既存ログを取り込むのがおすすめ。

```bash
CC2OBSIDIAN_VAULT=/tmp/cc2obsidian-vault \
  python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --source all --all --dry-run

python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --source all --all
```

### Claude Code

`~/.claude/settings.json` の `hooks` に `SessionEnd` を追加する。既に `hooks` や `SessionEnd` がある場合は丸ごと置き換えず、既存の設定を残して配列へマージする。

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/work/cc2obsidian/scripts/cc2obsidian.py hook --source claude-code",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

`--source` を省略した場合も `claude-code` になる。`timeout` は省略しないこと。大きなセッションでも変換を完了できるよう 30 秒を確保する。

### Codex

`~/.codex/hooks.json` に次を追加する。既存ファイルがある場合はトップレベルの `hooks` を残し、`SessionEnd` 配列へマージする。

```json
{
  "description": "Save Codex sessions to Obsidian.",
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/work/cc2obsidian/scripts/cc2obsidian.py hook --source codex",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

これは Codex の[公式 Hooks ドキュメント](https://developers.openai.com/codex/hooks)にある現行の `hooks.json` 形式。Codex の `SessionEnd` は既定 1 秒、指定できる最大値は 3 秒なので、`timeout` は `3` にする。追加・変更した非 managed hook は Codex の `/hooks` で内容を確認し、信頼してから有効になる。

Codex の `SessionEnd` は同期実行され、main thread にだけ発火する。会話を切り替えただけでは直ちに終了せず、開いている client が無い状態で idle になった場合は 30 分後に発火することがある。終了 hook が発火しない強制終了や進行中セッションは `backfill` で回収する。

## コマンド

```bash
# Claude Code と Codex の全ログを確認してから取り込む
python3 scripts/cc2obsidian.py backfill --source all --all --dry-run
python3 scripts/cc2obsidian.py backfill --source all --all

# source を限定する
python3 scripts/cc2obsidian.py backfill --source claude-code --since 7
python3 scripts/cc2obsidian.py backfill --source codex --since 7

# 変換ロジックを直したあと、全 source のノートを再生成する
python3 scripts/cc2obsidian.py backfill --source all --all --force

# 直近のノートから振り返り用ダイジェストを作る
python3 scripts/cc2obsidian.py digest --since 7
```

`hook --source` は `claude-code` または `codex`、`backfill --source` はそれらに `all` を加えた値を受け取る。どちらも `--source` を省略すると、後方互換のため `claude-code` だけを対象にする。

`backfill` は変換に失敗したセッションがあれば終了コード 1 を返す。通常は変換済みのセッションを飛ばすが、`--force` を付けると作り直す。ノート本体を削除した場合は `--force` なしでも作り直される。

`digest --since N` は source を問わず、`Notes/raw/` に書かれた直近 N 日分のノートを対象にする。

## データソースと互換性

| source | JSONL の探索元 | ノートのタグ |
|---|---|---|
| `claude-code` | `~/.claude/projects/` | `claude-code/session` |
| `codex` | `~/.codex/sessions/` | `codex/session` |

Codex が hook の `transcript_path` で渡す raw transcript は安定インターフェースではなく、将来形式が変わる可能性がある。このため Codex parser は独立 adapter とし、developer/system 指示、自動生成された環境情報、暗号化された reasoning などを除外している。読み取り可能な reasoning summary だけを思考として残す。形式変更には adapter 内で追従する。より安定した Codex App Server API の利用は将来の移行候補であり、Phase 1 はローカル JSONL を読む。

既存の Claude Code 利用を壊さないため、state とエラーログの既定パスはマルチ harness 化後も次のまま。

- state: `~/.claude/cc2obsidian-state.json`
- error log: `~/.claude/cc2obsidian.log`

state は `<source>:<session_id>` でセッションを識別する。以前の prefix 無し state と、`source` の無い既存ノートは `claude-code` として読み継ぐ。

## 設定

| 環境変数 | 既定 |
|---|---|
| `CC2OBSIDIAN_VAULT` | `~/private/obsidian/Obsidian` |

会話本文やツール出力を Vault に保存するため、同期先や共有範囲は機密情報を含む前提で設定すること。

## 振り返りスキル

リポジトリには次のスキルがある。

- `skills/dreaming/` — 1 日のセッションを全文レベルで振り返る
- `skills/weekly-review/` — 複数日のダイジェストから傾向を分析する

Codex から使う場合は、プロジェクトの `.agents/skills/` へリンクする。

```bash
mkdir -p .agents/skills
ln -s ../../skills/dreaming .agents/skills/dreaming
ln -s ../../skills/weekly-review .agents/skills/weekly-review
```

Claude Code から使う場合は、利用中の skills directory（例: `~/.claude/skills/`）から同じ2ディレクトリへリンクする。どちらのスキルも振り返り前に `backfill --source all` を実行し、Claude Code と Codex の両方を対象にする。

## 開発

```bash
python3 -m unittest discover -s tests -t . -v
```

開発規約と壊してはいけない不変条件は `AGENTS.md` を参照。外部依存なし、Python 3.14 の stdlib のみ。
