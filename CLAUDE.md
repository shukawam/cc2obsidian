# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Claude Code のセッション JSONL を Obsidian Vault の Markdown へ変換し、週次で振り返るためのツール。設計の経緯は `docs/superpowers/specs/2026-08-23-cc2obsidian-design.md`、実装計画は `docs/superpowers/plans/2026-08-23-cc2obsidian.md` にある。

## コマンド

```bash
# テスト（必ずリポジトリルートから。-t . が無いと import が壊れる）
python3 -m unittest discover -s tests -t . -v

# 単一のテストクラス / メソッド
python3 -m unittest tests.test_digest.HeadingSyncTest -v
python3 -m unittest tests.test_render_body.BodyTest.test_user_and_assistant_headings -v

# 手元での動作確認（実 Vault を汚さないよう環境変数で逃がす）
CC2OBSIDIAN_VAULT=/tmp/vault python3 scripts/cc2obsidian.py backfill --since 1 --dry-run

python3 scripts/cc2obsidian.py backfill --all      # 既存ログを全部取り込む
python3 scripts/cc2obsidian.py backfill --all --force   # render/parse を直したあと作り直す
python3 scripts/cc2obsidian.py digest --since 7    # 週次分析用ダイジェストを stdout へ
```

外部依存を追加しないこと。Python 3.14 の stdlib のみ。テストは `unittest`（pytest は入っていない）。

## 構造

`scripts/cc2obsidian.py` は `sys.path` を通して `cc2obsidian.cli:main` を呼ぶだけの薄いエントリポイント。`~/.claude/settings.json` の `SessionEnd` hook がここを叩く。

パイプラインは一方向に流れる。

```
JSONL → parse.py → model.py(Session/Turn/ToolCall) → render.py → vault.py → <Vault>/Notes/
                                                                    ↕ state.py
                                        Vault の .md → digest.py → 週次スキル
```

- **parse.py** — JSONL を中間表現へ。運用系エントリ（`SKIP_TYPES`）、`isMeta`、スラッシュコマンド機構（`SLASH_COMMAND_PREFIXES`）は会話ではないので落とす。`isSidechain`（サブエージェント）の発言は本文には残すが `model_counts` / `tool_counts` / `user_turns` の集計からは外す。`<synthetic>` モデルも集計対象外
- **render.py** — Session → Markdown。純粋関数。思考とツール呼び出しは `<details>` に畳み、長い出力は先頭 40 行 + 末尾 10 行に切り詰める
- **vault.py** — 書き込みと冪等性。`state.py` の記録と、ノート自身の frontmatter の `session_id` の両方を見て、上書き・改名・衝突回避を判断する
- **digest.py** — 書き出した .md を逆方向に読んで軽量ダイジェストを作る。`parse_frontmatter` は vault.py からも使われる
- **slugs.py** — JST 変換、ファイル名 slug 化、`Notes/<YYYY-MM-DD>/<HHMM>-<project>-<title>.md` の組み立て
- **config.py** — パス解決を一箇所に集約。テストは基本ここを `mock.patch` する

## 設計上の制約

以下は既にバグを踏んで固めた不変条件。壊さないこと。

**hook は絶対に失敗させない。** `cmd_hook` は何があっても 0 を返し、例外は `~/.claude/cc2obsidian.log` へ落とす。ここで非ゼロを返すとユーザーのセッション終了に副作用が出る。

**digest はノート本文をコードフェンス込みで読む。** ツール出力の中に `<details>` や `## 🤖 12:34` と読める行が混ざるため、`digest._with_fence_flags` がフェンスの内外を判定し、構造（折りたたみの深さ・ターン境界）の判定はフェンスの外の行だけで行う。フェンスの中身も本文としては残す（コードブロックだけのユーザー発話が空になるため）。切り詰めで閉じタグ側だけが落ちると行単位の数え方はずれたまま復帰しないので、この判定を外すと以降の発話が丸ごと digest から消える。

**残る制約:** アシスタント／ユーザーの地の文が閉じていないコードフェンスを含む場合、そこから先の構造判定は正しくならない。実 Vault 70 ノートで 1 件、1 発話が digest から欠ける。完全に直すには本文と構造を分離した出力形式が要る。

**render.py の見出し形式と digest.py の抽出は連動している。** `render.py:heading_regex()` が唯一の定義元で、digest.py はそこから正規表現を組み立てる。見出しを `## 👤 HH:MM` 形式から変える場合は `heading_regex` を直すだけでよい設計になっている。`tests/test_digest.py:HeadingSyncTest` がこの結びつきを検査する。

**state の書き戻しは自分が put したキーだけ。** `save()` はディスクを読み直して `_dirty` のキーだけを重ねる。`self._data` を丸ごと重ねると、ロード時点の古いエントリまで書き戻され、その間に他プロセスが更新したキーが巻き戻る。

**state のエントリは Vault ごとにスコープされる。** `State.get` / `needs_update` / `put` はすべて `vault_root` を取り、記録された Vault と一致しなければ「そこにノートは無い」と扱う。Vault を切り替えたときに全ノートが再生成されるのはこの仕様による。

**state の書き込みは read-merge-write。** `save()` は書く直前にディスクを読み直してマージする（キー衝突はメモリ側が勝つ）。hook と backfill が同時に走っても片方の書き込みが消えない。書き込みは tempfile + `os.replace` で atomic。

**State の初期化で OSError を握りつぶさない。** 読めない state を空として扱うと、次の `save()` が既存エントリを全消しする。JSON が壊れている場合のみ捨てて作り直す。

**記録があってもノート本体が無ければ作り直す。** `needs_update` は `vault_root / entry["path"]` の存在も見る。これが無いと、ノートを消しても state が残っている限り「変換済み」と判定され二度と復元されない。

**ノートの書き込みは一時ファイル + `os.replace`。** `write_text` は既存ファイルをその場で切り詰めるため、同じパスを更新する途中で失敗すると旧ノートまで壊れる。`vault._atomic_write` を経由すること。

**dry-run でもレンダリングは通す。** README が dry-run を「hook 登録前に変換の問題を検出する手順」として案内しているため、描画を飛ばすとその保証が消える。書き込みだけを止める。

**他セッションのノートを消さない。** 改名でパスが移る場合、削除してよいのは frontmatter の `session_id` が自分と一致するファイルだけ。state が指すパスを無条件に信用しない。書き込み順は「新を書き切ってから旧を消す」。

**backfill は `finally` で state を保存する。** `KeyboardInterrupt` で中断しても、変換済みのぶんは記録されて再実行時に重複しない。`--dry-run` のときは中断されても書かない。

**digest の `--since N` はノートの mtime ではなく `Notes/<YYYY-MM-DD>/` の日付で絞る。** `backfill --all` は全ノートを「今」書き直すため、mtime ベースだと `--since` が無意味になる。

**mtime はパースの前に取る。** 後で取ると、パースとの隙間に追記されたぶんを読まないまま新しい mtime を記録し、以後スキップされて永久に取り込まれない。先に取れば失敗方向が「次回拾い直す」側に倒れる。

**1 エントリの破損でセッション全体を失わない。** `parse_transcript` はエントリ単位で例外を捕まえて読み飛ばす。

**添付は本体を埋めずプレースホルダを残す。** `[image image/png]` の形。text ブロックが無いというだけでターンごと落とすと、ファイルを貼っただけの発話が記録から消える。

**切り詰めは行数と文字数の両方。** 実ログには 87,831 文字の tool input と、60 行以下で 1 万文字超の tool result がある。tool input にも `truncate_output` を通す。

**frontmatter の引用は `yaml_scalar` / `yaml_flow_scalar` に任せる。** 引用形は `json.dumps`（改行・引用符・バックスラッシュを正しく処理し、YAML の double-quoted スカラーとしても妥当）。改行・先頭の `-`・前後空白・`true`/数値/日付に見える値は引用しないと文字列として読まれない。`,` `[` `]` `{` `}` は `tags: [...]` や `{k: v}` の中でだけ区切りになるので `yaml_flow_scalar` を使う。

**タイムゾーンは JST 固定**（`slugs.JST`）。JSONL の `timestamp` は UTC の ISO8601。

**`digest --since N` は N+1 暦日を拾う**（N 日前のディレクトリ〜今日）。`tests/test_digest.py:DateDirectoryFilterTest` が境界を固定しているので、変える場合はテストの意図ごと変えること。

## スキル

`skills/weekly-review/`（週次の傾向分析）と `skills/dreaming/`（日次の深掘り復習）は `~/.claude/skills/` からシンボリックリンクされている。ここを編集すると、そのまま稼働中のスキルが変わる。`digest` の出力形式を変えるときは SKILL.md 側の手順とも整合を取ること。dreaming が書く `Notes/daily/<YYYY-MM-DD>.md` のセクション構成は、weekly-review が日次ノートを集約する前提で固定されている。
