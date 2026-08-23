# cc2obsidian

Claude Code のセッションを Obsidian Vault へ記録し、週次で振り返る。

## 仕組み

- `SessionEnd` hook が `scripts/cc2obsidian.py hook` を呼び、セッション JSONL を Markdown に変換して Vault へ書く
- 出力先は `<Vault>/Notes/<YYYY-MM-DD>/<HHMM>-<project>-<title>.md`
- 会話は全文、thinking とツール呼び出しは `<details>` で折りたたむ。長いツール出力は先頭 40 行 + 末尾 10 行に切り詰める
- `/weekly-review` スキルが直近 7 日を分析し、`<Vault>/Notes/weekly/` に週次ノートを書く

## インストール

ノート取得には `SessionEnd` hook の登録が必要。

`~/.claude/settings.json` の `hooks` セクション下に次を追加する。既存の hook エントリは必ず保持すること。

```json
"hooks": {
  "SessionEnd": [
    {
      "hooks": [
        { "type": "command", "command": "python3 ~/work/cc2obsidian/scripts/cc2obsidian.py hook" }
      ]
    }
  ]
}
```

推奨手順: `backfill --all --dry-run` で変換内容を確認 → `backfill --all` で既存ログを取り込む → hook を登録。この順序で、変換に問題があれば hook 登録前に検出される。

## コマンド

```bash
python3 scripts/cc2obsidian.py backfill --all --dry-run   # 変換されるものを確認
python3 scripts/cc2obsidian.py backfill --all             # 既存ログを全部取り込む
python3 scripts/cc2obsidian.py backfill --since 7         # 直近 7 日ぶんだけ
python3 scripts/cc2obsidian.py digest --since 7           # 週次分析用ダイジェスト
```

`SessionEnd` は強制終了時には発火しない。取りこぼしは `backfill` が回収する。

## 設定

| 環境変数 | 既定 |
|---|---|
| `CC2OBSIDIAN_VAULT` | `~/private/obsidian/Obsidian` |

state は `~/.claude/cc2obsidian-state.json`、エラーログは `~/.claude/cc2obsidian.log`。

## 開発

```bash
python3 -m unittest discover -s tests -t . -v
```

外部依存なし。Python 3.14 の stdlib のみ。
