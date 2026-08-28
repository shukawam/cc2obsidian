# cc2obsidian

Claude Code のセッションを Obsidian Vault へ記録し、週次で振り返る。

## 仕組み

- `SessionEnd` hook が `scripts/cc2obsidian.py hook` を呼び、セッション JSONL を Markdown に変換して Vault へ書く
- 出力先は `<Vault>/Notes/raw/<YYYY-MM-DD>/<HHMM>-<project>-<title>.md`
- 会話は全文、thinking とツール呼び出しは `<details>` で折りたたむ。長いツール出力は先頭 40 行 + 末尾 10 行に切り詰める
- `/weekly-review` スキルが直近 7 日を分析し、`<Vault>/Notes/weekly/` に週次ノートを書く

## インストール

ノート取得には `SessionEnd` hook の登録が必要。

`~/.claude/settings.json` の `hooks` セクション下に次のキーをマージする。既に `hooks` セクションがある場合は、その中身を丸ごと置き換えず、既存の hook エントリ（例: `PreToolUse` など）は必ず保持したまま `SessionEnd` キーだけを追加・マージすること。

```json
"SessionEnd": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/work/cc2obsidian/scripts/cc2obsidian.py hook",
        "timeout": 30
      }
    ]
  }
]
```

`timeout` は省略しないこと。`SessionEnd` の hook は既定で **1.5 秒**の予算を共有し、`timeout` を指定した場合だけそこまで（最大 60 秒）引き上げられる。実測では 5.1MB のセッションで 0.1 秒なので通常は足りるが、予算を超えると外部から打ち切られ、`cc2obsidian.log` にも記録が残らない。

推奨手順: `backfill --all --dry-run` で変換内容を確認 → `backfill --all` で既存ログを取り込む → hook を登録。この順序で、変換に問題があれば hook 登録前に検出される。

## コマンド

```bash
python3 scripts/cc2obsidian.py backfill --all --dry-run   # 変換されるものを確認
python3 scripts/cc2obsidian.py backfill --all             # 既存ログを全部取り込む
python3 scripts/cc2obsidian.py backfill --since 7         # 直近 7 日ぶんだけ
python3 scripts/cc2obsidian.py backfill --all --force     # 変換ロジックを直したあと作り直す
python3 scripts/cc2obsidian.py digest --since 7           # 週次分析用ダイジェスト
```

`SessionEnd` は強制終了時には発火しない。取りこぼしは `backfill` が回収する。

`backfill` は変換に失敗したセッションがあれば終了コード 1 を返す。通常は変換済みのセッションを飛ばすが、`--force` を付けると作り直す（変換ロジックを直したあとに使う）。ノートを削除した場合は `--force` なしでも作り直される。

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
