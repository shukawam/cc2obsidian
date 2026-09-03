# AGENTS.md

このファイルは、このリポジトリを扱う coding agent 共通の開発ガイドである。Claude Code 固有の入口は `CLAUDE.md` にあるが、開発規約と不変条件はこのファイルを正とする。

cc2obsidian は Claude Code と Codex のセッション JSONL を共通の中間表現へ変換し、Obsidian Vault の Markdown として蓄積・振り返りするツール。最初の設計経緯は `docs/superpowers/specs/2026-08-23-cc2obsidian-design.md`、実装計画は `docs/superpowers/plans/2026-08-23-cc2obsidian.md` にある。

## コマンド

```bash
# テスト（必ずリポジトリルートから。-t . が無いと import が壊れる）
python3 -m unittest discover -s tests -t . -v

# 単一のテストクラス / メソッド
python3 -m unittest tests.test_digest.HeadingSyncTest -v
python3 -m unittest tests.test_render_body.BodyTest.test_user_and_assistant_headings -v

# 実 Vault を汚さない動作確認
CC2OBSIDIAN_VAULT=/tmp/vault python3 scripts/cc2obsidian.py backfill --source all --since 1 --dry-run

# 既存ログの取り込みと再生成
python3 scripts/cc2obsidian.py backfill --source all --all
python3 scripts/cc2obsidian.py backfill --source all --all --force
python3 scripts/cc2obsidian.py digest --since 7
```

外部依存を追加しないこと。Python 3.14 の stdlib のみを使う。テストは `unittest`（pytest は入っていない）。

## 構造

`scripts/cc2obsidian.py` は `sys.path` を通して `cc2obsidian.cli:main` を呼ぶだけの薄いエントリポイント。Claude Code と Codex の `SessionEnd` hook は source を指定してこの CLI を呼ぶ。

パイプラインは一方向に流れる。

```text
Claude JSONL → Claude parser ┐
                             ├→ model.py (Session/Turn/ToolCall) → render.py → vault.py → <Vault>/Notes/raw/
Codex JSONL  → Codex parser  ┘                                                ↕ state.py
                                                     Vault の .md → digest.py → 振り返りスキル
```

- `parse.py` — Claude Code JSONL の adapter。運用系エントリ（`SKIP_TYPES`）、`isMeta`、スラッシュコマンド機構（`SLASH_COMMAND_PREFIXES`）は会話ではないので落とす。`isSidechain`（サブエージェント）の発言は本文には残すが `model_counts` / `tool_counts` / `user_turns` の集計からは外す。`<synthetic>` モデルも集計対象外
- `parse_codex.py` — Codex rollout JSONL の adapter。raw transcript は安定 API ではないため、Claude parser と混ぜず境界を保つ。会話として採るのは `response_item` の user / assistant message、`reasoning` の公開 summary、`function_call` / `custom_tool_call` / `web_search_call` とその出力だけ。`peek_codex_metadata` は先頭の `session_meta` だけを読み、backfill が本文を読む前に subagent と session_id を判定するために使う
- `model.py` — harness 非依存の中間表現。`Session.source` の既定は `claude-code`、`source_version` と `Turn.phase` は任意
- `render.py` — Session → Markdown の純粋関数。思考とツール呼び出しは `<details>` に畳み、長い出力は先頭 40 行 + 末尾 10 行に切り詰める。frontmatter に `source` を出し、タグは `<source>/session`
- `vault.py` — 書き込みと冪等性。state とノート自身の frontmatter の両方を見て、上書き・改名・衝突回避を判断する
- `digest.py` — 書き出した `.md` を逆方向に読んで軽量ダイジェストを作る。`parse_frontmatter` は `vault.py` からも使われる
- `slugs.py` — JST 変換、ファイル名 slug 化、`Notes/raw/<YYYY-MM-DD>/<HHMM>-<project>-<title>.md` の組み立て
- `config.py` — パス解決と source 定義を集約。テストは基本ここを `mock.patch` する

CLI の source 契約は次の通り。

- `hook --source claude-code|codex`。省略時は後方互換のため `claude-code`
- `backfill --source claude-code|codex|all`。省略時は後方互換のため `claude-code`
- Claude Code の探索元は `~/.claude/projects`、Codex は `~/.codex/sessions`
- state とエラーログの既定パスは後方互換のためそれぞれ `~/.claude/cc2obsidian-state.json` と `~/.claude/cc2obsidian.log`

## 設計上の制約

以下は既にバグを踏んで固めた不変条件。変更時は対応するテストと利用側を一緒に更新すること。

**hook は絶対に失敗させない。** `cmd_hook` は何があっても 0 を返し、例外は既定で `~/.claude/cc2obsidian.log` へ落とす。非ゼロ終了はユーザーのセッション終了に副作用を与える。特に Codex の `SessionEnd` は同期実行で timeout は最大 3 秒なので、hook 経路へ重い走査や外部 I/O を追加しない。

**source 境界を崩さない。** state の主キーは `<source>:<session_id>`。旧形式の prefix なしキーを読むのは `claude-code` の問い合わせ時だけ。frontmatter の所有権も `source` と `session_id` の組で判定し、`source` の無い旧ノートだけを `claude-code` とみなす。別 source の同じ session id やノートを上書きしない。

**Codex の session_id は `session_meta.payload.id` を使う。** `session_id` フィールドは 0.144 より前の rollout に存在せず、subagent の rollout では親スレッドを指す。rollout ファイルに対して一意なのは `id` の方で、root セッションでは両者は一致する。hook が渡す `session_id` は hint に留め、parser と backfill が同じ規則で決めることで state のキーがぶれないようにする。

**Codex の subagent rollout はノートにしない。** 先頭の `session_meta` の `source` が `{"subagent": ...}` なら親セッションと内容が重複するうえ、Codex は subagent に `SessionEnd` を発火しない。subagent の rollout は「自分の meta」「親の meta」の順に 2 本持つので、判定に使うのは最初の 1 本だけ。実ログ 183 本のうち 98 本がこれに当たる。

**Codex のターン境界は reasoning と assistant message で切る。** Codex は 1 ターンの中で「reasoning → 短い commentary → ツール呼び出し」を繰り返し、Claude のような「1 メッセージ = 思考 + 本文 + ツール」の単位を持たない。reasoning が来たら、直前のターンに本文かツール呼び出しがある場合に区切る。assistant message は、ツール呼び出しを積んだターンには合流させずに新しいターンを開く（Codex では発話がツール実行の後に来るため、合流させると本文とツールの順序が逆になる。Claude は逆順なので合流してよい）。

**Codex の reasoning は summary だけを採る。** 実ログでは 5,898 件中 5,176 件が summary 空で `encrypted_content` しか持たない。復号できない文字列をノートに残さない。同じ理由で、subagent 間メッセージなどに現れる `gAAAAA…` 形式のペイロードは `[encrypted]` に置き換える。

**Codex の自動注入ブロックは content ブロック単位で落とす。** `<environment_context>` / `<recommended_plugins>` / `# AGENTS.md instructions for …` は、本物の依頼と同じ `message` の中に別ブロックとして同居する。message ごと捨てると発話そのものが消え、素通しすると環境情報がノートへ漏れる。`developer` ロールは丸ごと落とす（skills / permissions / collaboration mode などの内部指示が入る）。

**Codex のツール出力は 3 つの形を取る。** 素の文字列（`Plan updated`）、`{"output": ..., "metadata": {"exit_code": ...}}` の JSON 文字列、`input_text` / `input_image` ブロックの配列のいずれか。実ログではこの 3 形式が同時に存在するので、どれか一つを仮定すると本文が丸ごと消える。エラー判定は `metadata.exit_code` と `timed_out`。

**Codex raw transcript を安定スキーマと仮定しない。** Codex が hook に渡す `transcript_path` の形式は変更されうる。parser は新旧形式を局所的に吸収し、会話として明示的に許可した user / assistant content だけを採用する。developer/system 指示、自動注入された environment context、world state、暗号化された reasoning、内部イベントをノートへ漏らさない。重複して表現された item と tool event は ID 等で重複排除し、未知の entry はセッション全体を落とさず読み飛ばす。将来 App Server を利用する場合も中間表現より下流を変更しない。

**digest はノート本文をコードフェンス込みで読む。** ツール出力の中に `<details>` や `## 🤖 12:34` と読める行が混ざるため、`digest._with_fence_flags` がフェンスの内外を判定し、構造（折りたたみの深さ・ターン境界）の判定はフェンスの外の行だけで行う。フェンスの中身も本文としては残す（コードブロックだけのユーザー発話が空になるため）。切り詰めで閉じタグ側だけが落ちると行単位の数え方はずれたまま復帰しないので、この判定を外すと以降の発話が丸ごと digest から消える。

**残る制約:** アシスタント／ユーザーの地の文が閉じていないコードフェンスを含む場合、そこから先の構造判定は正しくならない。完全に直すには本文と構造を分離した出力形式が要る。

**render.py の見出し形式と digest.py の抽出は連動している。** `render.py:heading_regex()` が唯一の定義元で、digest.py はそこから正規表現を組み立てる。見出しを `## 👤 HH:MM` 形式から変える場合は `heading_regex` を更新する。`tests/test_digest.py:HeadingSyncTest` がこの結びつきを検査する。

**state の書き戻しは自分が put したキーだけ。** `save()` はディスクを読み直して `_dirty` のキーだけを重ねる。`self._data` を丸ごと重ねると、ロード時点の古いエントリまで書き戻され、その間に他プロセスが更新したキーが巻き戻る。

**state のエントリは Vault ごとにスコープされる。** `State.get` / `needs_update` / `put` は `vault_root` と `source` を受け、記録された Vault と一致しなければ「そこにノートは無い」と扱う。Vault を切り替えたときに全ノートが再生成されるのはこの仕様による。

**state の read-merge-replace は同じ lock の内側で行う。** Claude/Codex の hook と backfill は並行しうる。atomic replace だけでは read と replace の間の lost update を防げないため、隣接する lock file で排他した後にディスクを読み直して `_dirty` をマージし、tempfile + `os.replace` する。lock を stale 判定だけで削除しない。

**State の初期化で OSError を握りつぶさない。** 読めない state を空として扱うと、次の `save()` が既存エントリを全消しする。JSON が壊れている場合のみ捨てて作り直す。

**記録があってもノート本体が無ければ作り直す。** `needs_update` は `vault_root / entry["path"]` の存在も見る。これが無いと、ノートを消しても state が残っている限り「変換済み」と判定され二度と復元されない。

**ノートの書き込みは一時ファイル + `os.replace`。** `write_text` は既存ファイルをその場で切り詰めるため、同じパスを更新する途中で失敗すると旧ノートまで壊れる。`vault._atomic_write` を経由する。

**dry-run でもパースとレンダリングは通す。** README が dry-run を「hook 登録前に変換の問題を検出する手順」として案内している。書き込みだけを止める。

**他セッションのノートを消さない。** 改名でパスが移る場合、削除してよいのは frontmatter の `source` と `session_id` が自分と一致するファイルだけ。state が指すパスを無条件に信用しない。書き込み順は「新を書き切ってから旧を消す」。

**「所有者を読めない」と「他人のもの」を区別する。** state が「このパスは自分のノート」と記録しているなら、そこにあるファイルの frontmatter が読めなくても書き直す（`_is_owned_by_another`）。読めない = 他人のもの、と扱うと `--force` が壊れたノートを直せず、隣に別名のノートが増え続ける。書き込み先を譲るのは、frontmatter から `(source, session_id)` を読めて、しかもそれが自分でない場合だけ。

**backfill は `finally` で state を保存する。** `KeyboardInterrupt` で中断しても、変換済みのぶんは記録されて再実行時に重複しない。`--dry-run` のときは中断されても書かない。

**digest の `--since N` はノートの mtime ではなく `Notes/raw/<YYYY-MM-DD>/` の日付で絞る。** 走査は `Notes/raw/` 配下のみで、`daily/` と `weekly/` は対象外。`backfill --all` はノートを「今」書き直すため、mtime ベースだと `--since` が無意味になる。

**mtime はパースの前に取る。** 後で取ると、パースとの隙間に追記されたぶんを読まないまま新しい mtime を記録し、以後スキップされて永久に取り込まれない。先に取れば失敗方向が「次回拾い直す」側に倒れる。

**1 エントリの破損でセッション全体を失わない。** 各 parser は entry 単位で例外を捕まえて読み飛ばす。

**添付は本体を埋めずプレースホルダを残す。** `[image image/png]` の形。text block が無いというだけで turn ごと落とすと、ファイルを貼っただけの発話が記録から消える。

**切り詰めは行数と文字数の両方。** 実ログには 87,831 文字の tool input と、60 行以下で 1 万文字超の tool result がある。tool input にも `truncate_output` を通す。

**frontmatter の引用は `yaml_scalar` / `yaml_flow_scalar` に任せる。** 引用形は `json.dumps`（改行・引用符・バックスラッシュを正しく処理し、YAML の double-quoted scalar としても妥当）。改行・先頭の `-`・前後空白・`true`/数値/日付に見える値は引用しないと文字列として読まれない。`,` `[` `]` `{` `}` は flow collection の中でだけ区切りになるので `yaml_flow_scalar` を使う。

**タイムゾーンは JST 固定**（`slugs.JST`）。JSONL の `timestamp` は UTC の ISO 8601。

**`digest --since N` は N+1 暦日を拾う。** N 日前のディレクトリから今日までが対象。`tests/test_digest.py:DateDirectoryFilterTest` が境界を固定しているので、変える場合はテストの意図ごと変える。

## スキル

`skills/weekly-review/`（週次の傾向分析）と `skills/dreaming/`（日次の深掘り復習）は coding agent 共通のスキル。Codex ではリポジトリの `.agents/skills/`、Claude Code では `~/.claude/skills/` など各 harness の探索場所から、このリポジトリ内のディレクトリへシンボリックリンクして使う。

`digest` の出力形式を変えるときは両方の `SKILL.md` と整合を取ること。dreaming が書く `Notes/daily/<YYYY-MM-DD>.md` のセクション構成は、weekly-review が日次ノートを集約する前提で固定されている。
