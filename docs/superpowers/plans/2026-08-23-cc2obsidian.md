# cc2obsidian Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Code のセッション JSONL を Obsidian ネイティブな Markdown に変換して Vault へ蓄積し、週次でスキル化・ナレッジ化候補を抽出できるようにする。

**Architecture:** stdlib のみの Python パッケージ `cc2obsidian/` を作り、`scripts/cc2obsidian.py` を薄いエントリポイントにする。JSONL を中間表現（`Session`/`Turn`/`ToolCall`）へパースする層と、中間表現を Markdown へ描画する層を分離し、純粋関数として単体テストできるようにする。書き込みは state ファイルによる冪等管理を通す。`SessionEnd` hook から `hook` サブコマンドを呼び、取りこぼしは `backfill` で回収する。

**Tech Stack:** Python 3.14（stdlib のみ / 外部依存なし）、`unittest`（pytest は未インストールのため使わない）、`git`

**Spec:** `docs/superpowers/specs/2026-08-23-cc2obsidian-design.md`

## Global Constraints

- **外部依存を追加しない。** stdlib のみ。`pip install` を必要とする実装は不可
- **テストは `unittest`。** 実行は必ずリポジトリルートから `python3 -m unittest discover -s tests -t . -v`
- **タイムゾーンは JST 固定**（`timezone(timedelta(hours=9))`）。JSONL の `timestamp` は UTC の ISO8601（末尾 `Z`）
- **Vault パス**: 既定 `~/private/obsidian/Obsidian`、環境変数 `CC2OBSIDIAN_VAULT` で上書き可
- **state ファイル**: `~/.claude/cc2obsidian-state.json`
- **ログファイル**: `~/.claude/cc2obsidian.log`
- **hook は絶対に失敗させない。** 例外を握りつぶし常に exit 0。エラーはログへ追記
- **tool_result の切り詰め**: 60 行超で先頭 40 行 + 末尾 10 行、間に省略行数を明記
- **集計からの除外**: `<synthetic>` モデル、`isSidechain: true`、`isMeta: true` の user エントリ
- **コミットは各タスク末尾で 1 回。** `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` を付ける

## File Structure

| ファイル | 責務 |
|---|---|
| `cc2obsidian/config.py` | Vault / state / log のパス解決のみ |
| `cc2obsidian/model.py` | `ToolCall` / `Turn` / `Session` の dataclass 定義のみ |
| `cc2obsidian/slugs.py` | JST 変換、slug 化、出力パス組み立て、cwd からの project/customer 抽出 |
| `cc2obsidian/parse.py` | JSONL → `Session`。tool_use と tool_result の突き合わせ、除外判定、メタ集計 |
| `cc2obsidian/render.py` | `Session` → Markdown。切り詰め、frontmatter、本文 |
| `cc2obsidian/state.py` | state ファイルの読み書きと再生成要否の判定 |
| `cc2obsidian/vault.py` | Vault への書き込み。リネーム、衝突回避、dry-run |
| `cc2obsidian/digest.py` | 期間内ノートから軽量ダイジェストを生成 |
| `cc2obsidian/cli.py` | argparse。`hook` / `backfill` / `digest` サブコマンド |
| `scripts/cc2obsidian.py` | sys.path を通して `cli.main()` を呼ぶだけ |
| `skills/weekly-review/SKILL.md` | 週次振り返りスキル |

---

### Task 1: プロジェクト骨格と slugs

**Files:**
- Create: `cc2obsidian/__init__.py`, `cc2obsidian/config.py`, `cc2obsidian/slugs.py`
- Create: `tests/__init__.py`, `tests/test_slugs.py`
- Create: `scripts/cc2obsidian.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `config.vault_path() -> Path`
  - `config.state_path() -> Path`
  - `config.log_path() -> Path`
  - `config.projects_dir() -> Path`
  - `slugs.JST: timezone`
  - `slugs.to_jst(iso: str) -> datetime`
  - `slugs.slugify(text: str, max_len: int = 40) -> str`
  - `slugs.project_from_cwd(cwd: str) -> str`
  - `slugs.customer_from_cwd(cwd: str) -> str | None`
  - `slugs.note_relpath(started: datetime, project: str, title: str, session_id: str, disambiguate: bool = False) -> Path`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_slugs.py`:

```python
import unittest
from datetime import datetime
from pathlib import Path

from cc2obsidian import slugs


class ToJstTest(unittest.TestCase):
    def test_converts_utc_iso_to_jst(self):
        got = slugs.to_jst("2026-08-23T08:01:35.036Z")
        self.assertEqual(got.year, 2026)
        self.assertEqual(got.month, 8)
        self.assertEqual(got.day, 23)
        self.assertEqual(got.hour, 17)
        self.assertEqual(got.minute, 1)

    def test_date_rolls_over_into_next_jst_day(self):
        got = slugs.to_jst("2026-08-22T15:30:00.000Z")
        self.assertEqual((got.month, got.day, got.hour), (8, 23, 0))

    def test_accepts_offset_form(self):
        got = slugs.to_jst("2026-08-23T08:01:35+00:00")
        self.assertEqual(got.hour, 17)


class SlugifyTest(unittest.TestCase):
    def test_keeps_japanese_text(self):
        self.assertEqual(slugs.slugify("スキル作成相談"), "スキル作成相談")

    def test_replaces_path_hostile_characters(self):
        self.assertEqual(slugs.slugify("a/b:c*d?e"), "a-b-c-d-e")

    def test_collapses_whitespace_into_single_hyphen(self):
        self.assertEqual(slugs.slugify("hello   world"), "hello-world")

    def test_truncates_to_max_len(self):
        self.assertEqual(len(slugs.slugify("x" * 100, max_len=40)), 40)

    def test_strips_leading_and_trailing_hyphens(self):
        self.assertEqual(slugs.slugify("  --hi--  "), "hi")

    def test_empty_input_yields_untitled(self):
        self.assertEqual(slugs.slugify("   "), "untitled")


class CwdTest(unittest.TestCase):
    def test_project_is_last_path_segment(self):
        self.assertEqual(slugs.project_from_cwd("/Users/x/work/konnect-demo"), "konnect-demo")

    def test_project_handles_trailing_slash(self):
        self.assertEqual(slugs.project_from_cwd("/Users/x/work/"), "work")

    def test_customer_extracted_from_customer_dir(self):
        self.assertEqual(slugs.customer_from_cwd("/Users/x/customer/mizuho/dify"), "mizuho")

    def test_customer_is_none_outside_customer_dir(self):
        self.assertIsNone(slugs.customer_from_cwd("/Users/x/work/foo"))


class NoteRelpathTest(unittest.TestCase):
    def setUp(self):
        self.started = slugs.to_jst("2026-08-22T23:01:00.000Z")  # JST 2026-08-23 08:01

    def test_builds_dated_directory_and_filename(self):
        got = slugs.note_relpath(self.started, "work", "スキル作成相談", "472a17cb-1f3b")
        self.assertEqual(got, Path("Notes/2026-08-23/0801-work-スキル作成相談.md"))

    def test_disambiguate_appends_short_session_id(self):
        got = slugs.note_relpath(self.started, "work", "スキル作成相談", "472a17cb-1f3b", disambiguate=True)
        self.assertEqual(got, Path("Notes/2026-08-23/0801-work-スキル作成相談-472a17cb.md"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc2obsidian'`

- [ ] **Step 3: 最小実装を書く**

`cc2obsidian/__init__.py`: 空ファイル

`cc2obsidian/config.py`:

```python
"""cc2obsidian が読み書きするパスの解決。"""
import os
from pathlib import Path

DEFAULT_VAULT = "~/private/obsidian/Obsidian"


def vault_path() -> Path:
    return Path(os.environ.get("CC2OBSIDIAN_VAULT", DEFAULT_VAULT)).expanduser()


def state_path() -> Path:
    return Path("~/.claude/cc2obsidian-state.json").expanduser()


def log_path() -> Path:
    return Path("~/.claude/cc2obsidian.log").expanduser()


def projects_dir() -> Path:
    return Path("~/.claude/projects").expanduser()
```

`cc2obsidian/slugs.py`:

```python
"""時刻変換、slug 化、Vault 内の出力パス組み立て。"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

# Obsidian / macOS のファイル名で問題になる文字
_HOSTILE = re.compile(r'[/\\:*?"<>|#^\[\]]')
_WS = re.compile(r"\s+")
_DASHES = re.compile(r"-{2,}")


def to_jst(iso: str) -> datetime:
    """UTC の ISO8601 文字列を JST の aware datetime に変換する。"""
    text = iso.replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(JST)


def slugify(text: str, max_len: int = 40) -> str:
    """日本語を保ったまま、ファイル名に使える形へ整える。"""
    s = _HOSTILE.sub("-", text)
    s = _WS.sub("-", s)
    s = _DASHES.sub("-", s).strip("-. ")
    s = s[:max_len].strip("-. ")
    return s or "untitled"


def project_from_cwd(cwd: str) -> str:
    return Path(cwd.rstrip("/")).name or "unknown"


def customer_from_cwd(cwd: str) -> str | None:
    """~/customer/<name>/... なら <name> を返す。"""
    parts = Path(cwd).parts
    if "customer" in parts:
        i = parts.index("customer")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def note_relpath(
    started: datetime,
    project: str,
    title: str,
    session_id: str,
    disambiguate: bool = False,
) -> Path:
    """Vault ルートからの相対パスを組み立てる。"""
    stem = f"{started:%H%M}-{slugify(project, 24)}-{slugify(title)}"
    if disambiguate:
        stem = f"{stem}-{session_id[:8]}"
    return Path("Notes") / f"{started:%Y-%m-%d}" / f"{stem}.md"
```

`tests/__init__.py`: 空ファイル

`scripts/cc2obsidian.py`:

```python
#!/usr/bin/env python3
"""cc2obsidian のエントリポイント。リポジトリを sys.path に通して CLI を呼ぶ。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cc2obsidian.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（15 tests）

注: `scripts/cc2obsidian.py` は `cc2obsidian.cli` を import するが、`cli.py` は Task 6 で作る。このタスクでは `scripts/cc2obsidian.py` を実行しないこと。テストは import しないので通る。

- [ ] **Step 5: コミット**

```bash
cd ~/work/cc2obsidian
chmod +x scripts/cc2obsidian.py
git add cc2obsidian/__init__.py cc2obsidian/config.py cc2obsidian/slugs.py tests/__init__.py tests/test_slugs.py scripts/cc2obsidian.py
git commit -m "feat: add path config and slug utilities

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 中間表現と JSONL パーサ

**Files:**
- Create: `cc2obsidian/model.py`, `cc2obsidian/parse.py`
- Create: `tests/test_parse.py`

**Interfaces:**
- Consumes: `slugs.to_jst`, `slugs.project_from_cwd`
- Produces:
  - `model.ToolCall(tool_name: str, summary: str, input_text: str, result_text: str, is_error: bool)`
  - `model.Turn(role: str, ts: datetime, text: str, thinking: str, tool_calls: list[ToolCall], is_sidechain: bool)`
  - `model.Session(session_id, cwd, project, title, started_at, ended_at, turns, model_counts, tool_counts, user_turns)`
  - `Session.duration_min -> int`
  - `parse.parse_transcript(path: Path) -> Session | None`

**背景（実データで確認済み）:**
- `assistant` エントリの `.message.content` は `thinking` / `text` / `tool_use` ブロックの配列
- `user` エントリの `.message.content` は **文字列**、または `tool_result` / `text` / `image` / `document` ブロックの配列
- `tool_result.content` も **文字列または配列**（配列の要素は `text` / `image` / `tool_reference`）
- `tool_result` は `tool_use_id` で `tool_use` と結びつく
- `ai-title` エントリは `.aiTitle` にタイトルを持つ
- `isMeta: true` の user エントリはシステム注入（実データに 114 件）
- 除外すべきエントリ型: `attachment` / `file-history-delta` / `file-history-snapshot` / `mode` / `permission-mode` / `queue-operation` / `last-prompt` / `system`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_parse.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from cc2obsidian import parse


def write_jsonl(entries):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for e in entries:
        tmp.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.close()
    return Path(tmp.name)


BASE = {"sessionId": "abc12345-0000", "cwd": "/Users/x/work/demo", "isSidechain": False}


def user_entry(text, ts="2026-08-22T23:01:00.000Z", **kw):
    return {**BASE, "type": "user", "timestamp": ts,
            "message": {"role": "user", "content": text}, **kw}


def assistant_entry(content, ts="2026-08-22T23:02:00.000Z", model="claude-opus-5", **kw):
    return {**BASE, "type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": model, "content": content}, **kw}


class ParseTest(unittest.TestCase):
    def test_returns_none_when_no_real_turns(self):
        p = write_jsonl([{"type": "mode", "mode": "default", "sessionId": "abc"}])
        self.assertIsNone(parse.parse_transcript(p))

    def test_extracts_user_and_assistant_text(self):
        p = write_jsonl([
            user_entry("こんにちは"),
            assistant_entry([{"type": "text", "text": "どうも"}]),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])
        self.assertEqual(s.turns[0].text, "こんにちは")
        self.assertEqual(s.turns[1].text, "どうも")

    def test_user_turns_counts_only_real_user_input(self):
        p = write_jsonl([
            user_entry("質問"),
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash",
                              "input": {"command": "ls", "description": "list"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]),
            user_entry("Caveat: system note", isMeta=True),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.user_turns, 1)

    def test_meta_user_entries_are_dropped(self):
        p = write_jsonl([
            user_entry("本題"),
            user_entry("システム注入", isMeta=True),
            assistant_entry([{"type": "text", "text": "ok"}]),
        ])
        s = parse.parse_transcript(p)
        self.assertNotIn("システム注入", "".join(t.text for t in s.turns))

    def test_tool_result_is_attached_to_its_tool_use(self):
        p = write_jsonl([
            user_entry("やって"),
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash",
                              "input": {"command": "ls -la", "description": "一覧"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1", "content": "file.txt"}]),
        ])
        s = parse.parse_transcript(p)
        call = s.turns[1].tool_calls[0]
        self.assertEqual(call.tool_name, "Bash")
        self.assertEqual(call.summary, "一覧")
        self.assertIn("ls -la", call.input_text)
        self.assertEqual(call.result_text, "file.txt")
        self.assertFalse(call.is_error)

    def test_tool_result_array_content_is_flattened(self):
        p = write_jsonl([
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/a"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1",
                         "content": [{"type": "text", "text": "line1"},
                                     {"type": "image"},
                                     {"type": "text", "text": "line2"}]}]),
        ])
        s = parse.parse_transcript(p)
        call = s.turns[0].tool_calls[0]
        self.assertIn("line1", call.result_text)
        self.assertIn("line2", call.result_text)
        self.assertIn("[image]", call.result_text)

    def test_tool_result_error_flag(self):
        p = write_jsonl([
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "false"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1", "content": "boom", "is_error": True}]),
        ])
        s = parse.parse_transcript(p)
        self.assertTrue(s.turns[0].tool_calls[0].is_error)

    def test_thinking_is_captured(self):
        p = write_jsonl([
            assistant_entry([{"type": "thinking", "thinking": "考え中"},
                             {"type": "text", "text": "答え"}]),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.turns[0].thinking, "考え中")
        self.assertEqual(s.turns[0].text, "答え")

    def test_model_counts_exclude_synthetic(self):
        p = write_jsonl([
            assistant_entry([{"type": "text", "text": "a"}], model="claude-opus-5"),
            assistant_entry([{"type": "text", "text": "b"}], model="claude-sonnet-5"),
            assistant_entry([{"type": "text", "text": "c"}], model="claude-opus-5"),
            assistant_entry([{"type": "text", "text": "d"}], model="<synthetic>"),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.model_counts, {"claude-opus-5": 2, "claude-sonnet-5": 1})

    def test_sidechain_entries_are_excluded_from_counts(self):
        side = assistant_entry(
            [{"type": "text", "text": "sub"},
             {"type": "tool_use", "id": "s1", "name": "Grep", "input": {"pattern": "x"}}],
            model="claude-haiku-4-5-20251001")
        side["isSidechain"] = True
        p = write_jsonl([assistant_entry([{"type": "text", "text": "main"}]), side])
        s = parse.parse_transcript(p)
        self.assertNotIn("claude-haiku-4-5-20251001", s.model_counts)
        self.assertNotIn("Grep", s.tool_counts)

    def test_sidechain_turns_are_kept_in_the_conversation(self):
        side = assistant_entry([{"type": "text", "text": "sub"}])
        side["isSidechain"] = True
        p = write_jsonl([assistant_entry([{"type": "text", "text": "main"}]), side])
        s = parse.parse_transcript(p)
        self.assertEqual(len(s.turns), 2)
        self.assertFalse(s.turns[0].is_sidechain)
        self.assertTrue(s.turns[1].is_sidechain)

    def test_sidechain_user_turns_are_not_counted(self):
        side = user_entry("サブへの指示")
        side["isSidechain"] = True
        p = write_jsonl([user_entry("本題"), side])
        s = parse.parse_transcript(p)
        self.assertEqual(s.user_turns, 1)

    def test_tool_counts_are_tallied(self):
        p = write_jsonl([
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                             {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pwd"}}]),
            assistant_entry([{"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "/a"}}]),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.tool_counts, {"Bash": 2, "Read": 1})

    def test_title_comes_from_last_ai_title(self):
        p = write_jsonl([
            user_entry("何か"),
            {"type": "ai-title", "aiTitle": "古い題", "sessionId": "abc12345-0000"},
            {"type": "ai-title", "aiTitle": "新しい題", "sessionId": "abc12345-0000"},
        ])
        self.assertEqual(parse.parse_transcript(p).title, "新しい題")

    def test_title_falls_back_to_first_user_text(self):
        p = write_jsonl([user_entry("スキルを作りたいので相談させて欲しい。" + "あ" * 50)])
        s = parse.parse_transcript(p)
        self.assertEqual(len(s.title), 30)
        self.assertTrue(s.title.startswith("スキルを作りたい"))

    def test_session_metadata(self):
        p = write_jsonl([
            user_entry("start", ts="2026-08-22T23:00:00.000Z"),
            assistant_entry([{"type": "text", "text": "end"}], ts="2026-08-22T23:42:00.000Z"),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.session_id, "abc12345-0000")
        self.assertEqual(s.project, "demo")
        self.assertEqual(s.cwd, "/Users/x/work/demo")
        self.assertEqual(s.started_at.hour, 8)
        self.assertEqual(s.duration_min, 42)

    def test_malformed_lines_are_skipped(self):
        p = write_jsonl([user_entry("ok")])
        with p.open("a", encoding="utf-8") as fh:
            fh.write("{ this is not json\n")
        s = parse.parse_transcript(p)
        self.assertEqual(s.turns[0].text, "ok")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest tests.test_parse -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc2obsidian.parse'`

- [ ] **Step 3: 最小実装を書く**

`cc2obsidian/model.py`:

```python
"""JSONL とレンダリングの間に挟む中間表現。"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ToolCall:
    tool_name: str
    summary: str          # details の summary 行に出す短い説明
    input_text: str       # 整形済みの入力パラメータ
    result_text: str      # 切り詰め前の結果テキスト
    is_error: bool = False


@dataclass
class Turn:
    role: str             # "user" | "assistant"
    ts: datetime          # JST
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    is_sidechain: bool = False


@dataclass
class Session:
    session_id: str
    cwd: str
    project: str
    title: str
    started_at: datetime
    ended_at: datetime
    turns: list[Turn] = field(default_factory=list)
    model_counts: dict[str, int] = field(default_factory=dict)
    tool_counts: dict[str, int] = field(default_factory=dict)
    user_turns: int = 0

    @property
    def duration_min(self) -> int:
        return round((self.ended_at - self.started_at).total_seconds() / 60)
```

`cc2obsidian/parse.py`:

```python
"""セッション JSONL を Session 中間表現へ変換する。"""
import json
from pathlib import Path

from .model import Session, ToolCall, Turn
from .slugs import project_from_cwd, to_jst

SYNTHETIC_MODEL = "<synthetic>"
TITLE_FALLBACK_LEN = 30

# 会話ではない運用系エントリ
SKIP_TYPES = frozenset({
    "attachment", "file-history-delta", "file-history-snapshot",
    "mode", "permission-mode", "queue-operation", "last-prompt", "system",
})


def _read_entries(path: Path) -> list[dict]:
    entries = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 書き込み途中の行などは読み飛ばす
    return entries


def _flatten_result(content) -> str:
    """tool_result.content は文字列にも配列にもなる。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "image":
            parts.append("[image]")
        elif btype == "tool_reference":
            parts.append("[tool_reference]")
    return "\n".join(parts)


def _format_input(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    if "command" in tool_input:
        return str(tool_input["command"])
    return json.dumps(tool_input, ensure_ascii=False, indent=2)


def _summary_for(name: str, tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return name
    for key in ("description", "file_path", "pattern", "skill", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return name


def _collect_results(entries: list[dict]) -> dict[str, tuple[str, bool]]:
    """tool_use_id -> (結果テキスト, エラーか) の対応表を作る。"""
    results = {}
    for e in entries:
        if e.get("type") != "user":
            continue
        content = e.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = (
                    _flatten_result(block.get("content")),
                    bool(block.get("is_error")),
                )
    return results


def _user_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def parse_transcript(path: Path) -> Session | None:
    entries = _read_entries(path)
    if not entries:
        return None

    results = _collect_results(entries)
    turns: list[Turn] = []
    model_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    user_turns = 0
    title = ""
    session_id = ""
    cwd = ""

    for e in entries:
        etype = e.get("type")

        if etype == "ai-title":
            title = e.get("aiTitle") or title
            continue
        if etype in SKIP_TYPES:
            continue
        if etype not in ("user", "assistant"):
            continue

        session_id = session_id or e.get("sessionId", "")
        cwd = cwd or e.get("cwd", "")
        # サブエージェントの発言は会話としては残すが、集計からは外す
        is_side = bool(e.get("isSidechain"))

        ts = to_jst(e["timestamp"])
        message = e.get("message", {})

        if etype == "user":
            if e.get("isMeta"):
                continue
            text = _user_text(message.get("content"))
            if not text.strip():
                continue  # tool_result だけの user エントリ
            if not is_side:
                user_turns += 1
            turns.append(Turn(role="user", ts=ts, text=text, is_sidechain=is_side))
            continue

        model = message.get("model")
        if model and model != SYNTHETIC_MODEL and not is_side:
            model_counts[model] = model_counts.get(model, 0) + 1

        texts, thoughts, calls = [], [], []
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text", ""))
            elif btype == "thinking":
                thoughts.append(block.get("thinking", ""))
            elif btype == "tool_use":
                name = block.get("name", "unknown")
                if not is_side:
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                tool_input = block.get("input", {})
                result_text, is_error = results.get(block.get("id"), ("", False))
                calls.append(ToolCall(
                    tool_name=name,
                    summary=_summary_for(name, tool_input),
                    input_text=_format_input(tool_input),
                    result_text=result_text,
                    is_error=is_error,
                ))

        turns.append(Turn(
            role="assistant", ts=ts,
            text="\n".join(t for t in texts if t),
            thinking="\n".join(t for t in thoughts if t),
            tool_calls=calls,
            is_sidechain=is_side,
        ))

    if not turns:
        return None

    if not title:
        first_user = next((t.text for t in turns if t.role == "user"), "")
        title = first_user.strip()[:TITLE_FALLBACK_LEN] or "untitled"

    return Session(
        session_id=session_id,
        cwd=cwd,
        project=project_from_cwd(cwd) if cwd else "unknown",
        title=title,
        started_at=turns[0].ts,
        ended_at=turns[-1].ts,
        turns=turns,
        model_counts=model_counts,
        tool_counts=tool_counts,
        user_turns=user_turns,
    )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（Task 1 の 15 + 本タスクの 17 = 32 tests）

- [ ] **Step 5: 実データで動くことを確認**

Run:

```bash
cd ~/work/cc2obsidian
python3 -c "
from pathlib import Path
from cc2obsidian.parse import parse_transcript
p = Path('~/.claude/projects/-Users-shuhei-kawamura-work-konnect-entire-demo').expanduser()
f = sorted(p.glob('*.jsonl'))[0]
s = parse_transcript(f)
print(s.title, s.project, s.user_turns, s.duration_min)
print(s.model_counts, s.tool_counts)
"
```

Expected: タイトル・プロジェクト名・モデル数・ツール数が出力され、例外が出ない。

- [ ] **Step 6: コミット**

```bash
cd ~/work/cc2obsidian
git add cc2obsidian/model.py cc2obsidian/parse.py tests/test_parse.py
git commit -m "feat: parse session transcripts into an intermediate representation

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 切り詰めと frontmatter

**Files:**
- Create: `cc2obsidian/render.py`
- Create: `tests/test_render_frontmatter.py`

**Interfaces:**
- Consumes: `model.Session`, `slugs.customer_from_cwd`
- Produces:
  - `render.HEAD_LINES = 40`, `render.TAIL_LINES = 10`, `render.TRUNCATE_THRESHOLD = 60`
  - `render.truncate_output(text: str) -> str`
  - `render.yaml_scalar(value: str) -> str`
  - `render.render_frontmatter(session: Session) -> str`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_render_frontmatter.py`:

```python
import unittest
from datetime import datetime

from cc2obsidian import render, slugs
from cc2obsidian.model import Session, Turn


def make_session(**kw):
    started = slugs.to_jst("2026-08-22T23:01:00.000Z")
    ended = slugs.to_jst("2026-08-22T23:43:00.000Z")
    defaults = dict(
        session_id="472a17cb-1f3b-488d-b335-0f7bdf7de956",
        cwd="/Users/x/work/demo",
        project="demo",
        title="スキル作成相談",
        started_at=started,
        ended_at=ended,
        turns=[Turn(role="user", ts=started, text="hi")],
        model_counts={"claude-opus-5": 45},
        tool_counts={"Bash": 6},
        user_turns=5,
    )
    defaults.update(kw)
    return Session(**defaults)


class TruncateTest(unittest.TestCase):
    def test_short_output_is_unchanged(self):
        text = "\n".join(f"line{i}" for i in range(10))
        self.assertEqual(render.truncate_output(text), text)

    def test_exactly_at_threshold_is_unchanged(self):
        text = "\n".join(f"line{i}" for i in range(60))
        self.assertEqual(render.truncate_output(text), text)

    def test_long_output_keeps_head_and_tail(self):
        text = "\n".join(f"line{i}" for i in range(200))
        got = render.truncate_output(text).splitlines()
        self.assertEqual(got[0], "line0")
        self.assertEqual(got[39], "line39")
        self.assertEqual(got[-1], "line199")
        self.assertEqual(got[-10], "line190")

    def test_long_output_states_how_many_lines_were_dropped(self):
        text = "\n".join(f"line{i}" for i in range(200))
        self.assertIn("150 行省略", render.truncate_output(text))

    def test_empty_text(self):
        self.assertEqual(render.truncate_output(""), "")


class YamlScalarTest(unittest.TestCase):
    def test_plain_text_is_bare(self):
        self.assertEqual(render.yaml_scalar("スキル作成相談"), "スキル作成相談")

    def test_colon_forces_quoting(self):
        self.assertEqual(render.yaml_scalar("a: b"), '"a: b"')

    def test_quotes_are_escaped(self):
        self.assertEqual(render.yaml_scalar('say "hi": now'), '"say \\"hi\\": now"')

    def test_leading_hash_forces_quoting(self):
        self.assertEqual(render.yaml_scalar("#tag"), '"#tag"')


class FrontmatterTest(unittest.TestCase):
    def test_contains_core_fields(self):
        fm = render.render_frontmatter(make_session())
        self.assertIn("date: 2026-08-23", fm)
        self.assertIn('time: "08:01"', fm)
        self.assertIn("project: demo", fm)
        self.assertIn("session_id: 472a17cb-1f3b-488d-b335-0f7bdf7de956", fm)
        self.assertIn("duration_min: 42", fm)
        self.assertIn("user_turns: 5", fm)

    def test_starts_and_ends_with_delimiters(self):
        fm = render.render_frontmatter(make_session())
        self.assertTrue(fm.startswith("---\n"))
        self.assertTrue(fm.rstrip().endswith("---"))

    def test_single_model_omits_models_map(self):
        fm = render.render_frontmatter(make_session(model_counts={"claude-opus-5": 45}))
        self.assertIn("model: claude-opus-5", fm)
        self.assertNotIn("models:", fm)

    def test_multiple_models_emit_map_sorted_by_count(self):
        fm = render.render_frontmatter(make_session(
            model_counts={"claude-sonnet-5": 4, "claude-opus-5": 45}))
        self.assertIn("model: claude-opus-5", fm)
        self.assertIn("models: {claude-opus-5: 45, claude-sonnet-5: 4}", fm)

    def test_no_models_emits_unknown(self):
        fm = render.render_frontmatter(make_session(model_counts={}))
        self.assertIn("model: unknown", fm)

    def test_tool_counts_sorted_by_count(self):
        fm = render.render_frontmatter(make_session(tool_counts={"Read": 2, "Bash": 6}))
        self.assertIn("tool_counts: {Bash: 6, Read: 2}", fm)

    def test_empty_tool_counts_emits_empty_map(self):
        fm = render.render_frontmatter(make_session(tool_counts={}))
        self.assertIn("tool_counts: {}", fm)

    def test_tags_include_session_and_project(self):
        fm = render.render_frontmatter(make_session())
        self.assertIn("tags: [claude-code/session, project/demo]", fm)

    def test_customer_path_adds_customer_tag(self):
        fm = render.render_frontmatter(make_session(cwd="/Users/x/customer/mizuho/dify", project="dify"))
        self.assertIn("customer/mizuho", fm)

    def test_title_with_colon_is_quoted(self):
        fm = render.render_frontmatter(make_session(title="Kong: 設計"))
        self.assertIn('title: "Kong: 設計"', fm)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest tests.test_render_frontmatter -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc2obsidian.render'`

- [ ] **Step 3: 最小実装を書く**

`cc2obsidian/render.py`:

```python
"""Session 中間表現を Obsidian 向け Markdown へ描画する。"""
from .model import Session
from .slugs import customer_from_cwd

HEAD_LINES = 40
TAIL_LINES = 10
TRUNCATE_THRESHOLD = 60

_YAML_SPECIAL = set(':#[]{}&*!|>%@`"\'')


def truncate_output(text: str) -> str:
    """長いツール出力を先頭と末尾だけ残して切り詰める。"""
    lines = text.splitlines()
    if len(lines) <= TRUNCATE_THRESHOLD:
        return text
    dropped = len(lines) - HEAD_LINES - TAIL_LINES
    return "\n".join(
        lines[:HEAD_LINES] + [f"… {dropped} 行省略 …"] + lines[-TAIL_LINES:]
    )


def yaml_scalar(value: str) -> str:
    """YAML で誤読される文字を含むときだけ引用する。"""
    text = str(value)
    needs_quote = (
        not text
        or text[0] in _YAML_SPECIAL
        or any(ch in text for ch in (": ", " #"))
        or text.endswith(":")
    )
    if not needs_quote:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _sorted_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    """件数の降順、同数ならキー昇順。"""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _inline_map(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    body = ", ".join(f"{k}: {v}" for k, v in _sorted_counts(counts))
    return "{" + body + "}"


def render_frontmatter(session: Session) -> str:
    models = _sorted_counts(session.model_counts)
    primary = models[0][0] if models else "unknown"

    tags = ["claude-code/session", f"project/{session.project}"]
    customer = customer_from_cwd(session.cwd)
    if customer:
        tags.append(f"customer/{customer}")

    lines = [
        "---",
        f"date: {session.started_at:%Y-%m-%d}",
        f'time: "{session.started_at:%H:%M}"',
        f"project: {yaml_scalar(session.project)}",
        f"cwd: {yaml_scalar(session.cwd)}",
        f"session_id: {session.session_id}",
        f"title: {yaml_scalar(session.title)}",
        f"duration_min: {session.duration_min}",
        f"user_turns: {session.user_turns}",
        f"model: {primary}",
    ]
    if len(models) > 1:
        lines.append(f"models: {_inline_map(session.model_counts)}")
    lines.append(f"tool_counts: {_inline_map(session.tool_counts)}")
    lines.append(f"tags: [{', '.join(tags)}]")
    lines.append("---")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（32 + 19 = 51 tests）

- [ ] **Step 5: コミット**

```bash
cd ~/work/cc2obsidian
git add cc2obsidian/render.py tests/test_render_frontmatter.py
git commit -m "feat: render session frontmatter and truncate long tool output

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 本文レンダリング

**Files:**
- Modify: `cc2obsidian/render.py`（末尾に追記）
- Create: `tests/test_render_body.py`

**Interfaces:**
- Consumes: `render.truncate_output`, `render.render_frontmatter`, `model.Session`/`Turn`/`ToolCall`
- Produces:
  - `render.render_body(session: Session) -> str`
  - `render.render_note(session: Session) -> str`

**フェンスの注意:** ツール出力自体が ` ``` ` を含むことがある。出力に含まれる最長のバッククォート連長より 1 つ長いフェンスを使う。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_render_body.py`:

```python
import unittest

from cc2obsidian import render, slugs
from cc2obsidian.model import Session, ToolCall, Turn

TS = slugs.to_jst("2026-08-22T23:01:00.000Z")  # JST 08:01


def make_session(turns, **kw):
    defaults = dict(
        session_id="abc12345-0000", cwd="/Users/x/work/demo", project="demo",
        title="タイトル", started_at=TS, ended_at=TS, turns=turns,
        model_counts={"claude-opus-5": 1}, tool_counts={}, user_turns=1,
    )
    defaults.update(kw)
    return Session(**defaults)


class BodyTest(unittest.TestCase):
    def test_title_heading_comes_first(self):
        body = render.render_body(make_session([Turn("user", TS, "hi")]))
        self.assertTrue(body.startswith("# タイトル\n"))

    def test_user_and_assistant_headings(self):
        body = render.render_body(make_session([
            Turn("user", TS, "質問"),
            Turn("assistant", TS, "回答"),
        ]))
        self.assertIn("## 👤 08:01", body)
        self.assertIn("## 🤖 08:01", body)
        self.assertIn("質問", body)
        self.assertIn("回答", body)

    def test_thinking_is_folded_into_details(self):
        body = render.render_body(make_session([Turn("assistant", TS, "答", thinking="考")]))
        self.assertIn("<details><summary>💭 thinking</summary>", body)
        self.assertIn("考", body)
        self.assertIn("</details>", body)

    def test_no_thinking_block_when_absent(self):
        body = render.render_body(make_session([Turn("assistant", TS, "答")]))
        self.assertNotIn("💭 thinking", body)

    def test_tool_call_is_folded_with_name_and_summary(self):
        call = ToolCall("Bash", "一覧を見る", "ls -la", "file.txt")
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("<details><summary>🔧 Bash — 一覧を見る</summary>", body)
        self.assertIn("ls -la", body)
        self.assertIn("file.txt", body)

    def test_errored_tool_call_is_marked(self):
        call = ToolCall("Bash", "落ちる", "false", "boom", is_error=True)
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("⚠️", body)

    def test_long_tool_result_is_truncated(self):
        long_out = "\n".join(f"line{i}" for i in range(300))
        call = ToolCall("Bash", "多い", "cmd", long_out)
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("行省略", body)
        self.assertNotIn("line150", body)

    def test_backticks_in_output_get_longer_fence(self):
        call = ToolCall("Bash", "fence", "cmd", "```\ninner\n```")
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("````", body)

    def test_empty_result_is_labelled(self):
        call = ToolCall("Bash", "無音", "cmd", "")
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("(出力なし)", body)

    def test_sidechain_turn_is_folded_into_details(self):
        turn = Turn("assistant", TS, "サブの答え", is_sidechain=True)
        body = render.render_body(make_session([turn]))
        self.assertIn("🧵 サブエージェント 08:01", body)
        self.assertIn("サブの答え", body)

    def test_turn_with_no_content_is_skipped(self):
        body = render.render_body(make_session([
            Turn("assistant", TS, ""),
            Turn("user", TS, "あり"),
        ]))
        self.assertEqual(body.count("## 🤖"), 0)


class NoteTest(unittest.TestCase):
    def test_note_is_frontmatter_then_body(self):
        note = render.render_note(make_session([Turn("user", TS, "hi")]))
        self.assertTrue(note.startswith("---\n"))
        self.assertIn("\n---\n\n# タイトル\n", note)
        self.assertTrue(note.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest tests.test_render_body -v`
Expected: FAIL — `AttributeError: module 'cc2obsidian.render' has no attribute 'render_body'`

- [ ] **Step 3: 最小実装を書く**

`cc2obsidian/render.py` の末尾に追記:

```python
import re

from .model import ToolCall, Turn

_BACKTICKS = re.compile(r"`+")

ROLE_HEADINGS = {"user": "👤", "assistant": "🤖"}


def _fence_for(text: str) -> str:
    """本文に含まれるバッククォート連長より長いフェンスを返す。"""
    longest = max((len(m.group()) for m in _BACKTICKS.finditer(text)), default=0)
    return "`" * max(3, longest + 1)


def _code_block(text: str, lang: str = "") -> str:
    fence = _fence_for(text)
    return f"{fence}{lang}\n{text}\n{fence}"


def _render_tool_call(call: ToolCall) -> str:
    mark = "⚠️ " if call.is_error else ""
    summary = f"{mark}🔧 {call.tool_name} — {call.summary}"
    parts = [f"<details><summary>{summary}</summary>", ""]
    if call.input_text:
        lang = "bash" if call.tool_name == "Bash" else ""
        parts.append(_code_block(call.input_text, lang))
        parts.append("")
    result = truncate_output(call.result_text) if call.result_text else "(出力なし)"
    parts.append(_code_block(result))
    parts.append("")
    parts.append("</details>")
    return "\n".join(parts)


def _render_turn(turn: Turn) -> str | None:
    if not (turn.text.strip() or turn.thinking.strip() or turn.tool_calls):
        return None
    icon = ROLE_HEADINGS.get(turn.role, "•")
    parts = [f"## {icon} {turn.ts:%H:%M}", ""]
    if turn.text.strip():
        parts.append(turn.text.strip())
        parts.append("")
    if turn.thinking.strip():
        parts.append("<details><summary>💭 thinking</summary>")
        parts.append("")
        parts.append(turn.thinking.strip())
        parts.append("")
        parts.append("</details>")
        parts.append("")
    for call in turn.tool_calls:
        parts.append(_render_tool_call(call))
        parts.append("")
    rendered = "\n".join(parts).rstrip() + "\n"
    if turn.is_sidechain:
        return (f"<details><summary>🧵 サブエージェント {turn.ts:%H:%M}</summary>\n\n"
                f"{rendered}\n</details>\n")
    return rendered


def render_body(session: Session) -> str:
    blocks = [f"# {session.title}\n"]
    for turn in session.turns:
        rendered = _render_turn(turn)
        if rendered:
            blocks.append(rendered)
    return "\n".join(blocks)


def render_note(session: Session) -> str:
    note = render_frontmatter(session) + "\n" + render_body(session)
    return note if note.endswith("\n") else note + "\n"
```

注: `import re` と `from .model import ToolCall, Turn` は既存の import 群へまとめること。ファイル途中の import は残さない。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（51 + 12 = 63 tests）

- [ ] **Step 5: 実データを目視確認**

Run:

```bash
cd ~/work/cc2obsidian
python3 -c "
from pathlib import Path
from cc2obsidian.parse import parse_transcript
from cc2obsidian.render import render_note
f = sorted(Path('~/.claude/projects/-Users-shuhei-kawamura-work').expanduser().glob('*.jsonl'))[0]
print(render_note(parse_transcript(f))[:3000])
"
```

Expected: frontmatter に続いて `# タイトル`、`## 👤 HH:MM`、`<details>` で畳まれたツール呼び出しが見える。

- [ ] **Step 6: コミット**

```bash
cd ~/work/cc2obsidian
git add cc2obsidian/render.py tests/test_render_body.py
git commit -m "feat: render session body with folded thinking and tool calls

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: state 管理と Vault 書き込み

**Files:**
- Create: `cc2obsidian/state.py`, `cc2obsidian/vault.py`
- Create: `tests/test_state.py`, `tests/test_vault.py`

**Interfaces:**
- Consumes: `render.render_note`, `slugs.note_relpath`, `model.Session`
- Produces:
  - `state.State(path: Path)` / `.get(session_id) -> dict | None` / `.needs_update(session_id, source_mtime) -> bool` / `.put(session_id, relpath: str, source_mtime: float)` / `.save()`
  - `vault.write_note(vault_root: Path, session: Session, st: State, source_mtime: float, dry_run: bool = False) -> Path`

**冪等の規則:**
1. 同じ `session_id` は同じノートを上書きする
2. タイトル変更などで相対パスが変わったら、旧ファイルを削除してから新パスへ書く
3. 目標パスが**別の** session_id のノートで埋まっていたら、`disambiguate=True` で session_id 先頭 8 桁を付ける

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_state.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from cc2obsidian.state import State


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "state.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_starts_empty(self):
        self.assertIsNone(State(self.path).get("nope"))

    def test_corrupt_file_starts_empty(self):
        self.path.write_text("{ broken", encoding="utf-8")
        self.assertIsNone(State(self.path).get("nope"))

    def test_put_then_get_roundtrips_through_disk(self):
        st = State(self.path)
        st.put("s1", "Notes/2026-08-23/a.md", 123.0)
        st.save()
        self.assertEqual(State(self.path).get("s1")["path"], "Notes/2026-08-23/a.md")

    def test_save_creates_parent_directory(self):
        nested = Path(self.dir.name) / "deep" / "state.json"
        st = State(nested)
        st.put("s1", "x.md", 1.0)
        st.save()
        self.assertTrue(nested.exists())

    def test_unknown_session_needs_update(self):
        self.assertTrue(State(self.path).needs_update("s1", 100.0))

    def test_unchanged_mtime_needs_no_update(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0)
        self.assertFalse(st.needs_update("s1", 100.0))

    def test_newer_mtime_needs_update(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0)
        self.assertTrue(st.needs_update("s1", 101.0))

    def test_save_is_atomic_leaving_no_tmp_file(self):
        st = State(self.path)
        st.put("s1", "x.md", 1.0)
        st.save()
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name != "state.json"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
```

`tests/test_vault.py`:

```python
import tempfile
import unittest
from pathlib import Path

from cc2obsidian import slugs, vault
from cc2obsidian.model import Session, Turn
from cc2obsidian.state import State

TS = slugs.to_jst("2026-08-22T23:01:00.000Z")  # JST 2026-08-23 08:01


def make_session(session_id="abc12345-0000", title="タイトル"):
    return Session(
        session_id=session_id, cwd="/Users/x/work/demo", project="demo",
        title=title, started_at=TS, ended_at=TS,
        turns=[Turn("user", TS, "hi")],
        model_counts={"claude-opus-5": 1}, tool_counts={}, user_turns=1,
    )


class WriteNoteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.state = State(self.root / "state.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_creates_dated_directory_and_file(self):
        out = vault.write_note(self.root, make_session(), self.state, 100.0)
        self.assertTrue(out.exists())
        self.assertEqual(out.parent.name, "2026-08-23")
        self.assertIn("# タイトル", out.read_text(encoding="utf-8"))

    def test_records_state(self):
        vault.write_note(self.root, make_session(), self.state, 100.0)
        self.assertEqual(self.state.get("abc12345-0000")["source_mtime"], 100.0)

    def test_rewriting_same_session_overwrites_in_place(self):
        vault.write_note(self.root, make_session(), self.state, 100.0)
        vault.write_note(self.root, make_session(), self.state, 200.0)
        notes = list((self.root / "Notes" / "2026-08-23").glob("*.md"))
        self.assertEqual(len(notes), 1)

    def test_changed_title_moves_the_note(self):
        first = vault.write_note(self.root, make_session(title="旧題"), self.state, 100.0)
        second = vault.write_note(self.root, make_session(title="新題"), self.state, 200.0)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertIn("新題", second.name)

    def test_collision_with_other_session_gets_short_id(self):
        vault.write_note(self.root, make_session(session_id="aaaaaaaa-1111"), self.state, 100.0)
        out = vault.write_note(self.root, make_session(session_id="bbbbbbbb-2222"), self.state, 100.0)
        self.assertTrue(out.name.endswith("-bbbbbbbb.md"))
        notes = list((self.root / "Notes" / "2026-08-23").glob("*.md"))
        self.assertEqual(len(notes), 2)

    def test_dry_run_writes_nothing(self):
        out = vault.write_note(self.root, make_session(), self.state, 100.0, dry_run=True)
        self.assertFalse(out.exists())
        self.assertIsNone(self.state.get("abc12345-0000"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest tests.test_state tests.test_vault -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc2obsidian.state'`

- [ ] **Step 3: 最小実装を書く**

`cc2obsidian/state.py`:

```python
"""session_id と出力ノートの対応を記録し、再生成の要否を判定する。"""
import json
import os
import tempfile
from pathlib import Path


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except (json.JSONDecodeError, OSError):
                self._data = {}  # 壊れた state は捨てて作り直す

    def get(self, session_id: str) -> dict | None:
        return self._data.get(session_id)

    def needs_update(self, session_id: str, source_mtime: float) -> bool:
        entry = self._data.get(session_id)
        if entry is None:
            return True
        return source_mtime > entry.get("source_mtime", 0)

    def put(self, session_id: str, relpath: str, source_mtime: float) -> None:
        self._data[session_id] = {"path": relpath, "source_mtime": source_mtime}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
```

`cc2obsidian/vault.py`:

```python
"""Vault へのノート書き込み。冪等性とファイル名衝突を扱う。"""
from pathlib import Path

from .model import Session
from .render import render_note
from .slugs import note_relpath
from .state import State


def _target_relpath(vault_root: Path, session: Session, st: State) -> Path:
    """書き込み先の相対パスを決める。他セッションと衝突したら短い id を足す。"""
    relpath = note_relpath(
        session.started_at, session.project, session.title, session.session_id
    )
    known = st.get(session.session_id)
    if known and known.get("path") == str(relpath):
        return relpath  # 自分の既存ノート。そのまま上書きする

    if (vault_root / relpath).exists():
        # 他セッションのノートが場所を取っている
        return note_relpath(
            session.started_at, session.project, session.title,
            session.session_id, disambiguate=True,
        )
    return relpath


def write_note(
    vault_root: Path,
    session: Session,
    st: State,
    source_mtime: float,
    dry_run: bool = False,
) -> Path:
    relpath = _target_relpath(vault_root, session, st)
    target = vault_root / relpath

    if dry_run:
        return target

    known = st.get(session.session_id)
    if known and known.get("path") != str(relpath):
        (vault_root / known["path"]).unlink(missing_ok=True)  # タイトル変更で移動

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_note(session), encoding="utf-8")
    st.put(session.session_id, str(relpath), source_mtime)
    return target
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（63 + 14 = 77 tests）

- [ ] **Step 5: コミット**

```bash
cd ~/work/cc2obsidian
git add cc2obsidian/state.py cc2obsidian/vault.py tests/test_state.py tests/test_vault.py
git commit -m "feat: write notes into the vault idempotently via a state file

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: CLI（hook / backfill）

**Files:**
- Create: `cc2obsidian/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `config.*`, `parse.parse_transcript`, `vault.write_note`, `state.State`
- Produces:
  - `cli.convert_one(path: Path, vault_root: Path, st: State, dry_run: bool = False) -> Path | None`
  - `cli.iter_transcripts(projects_root: Path, since_days: int | None) -> list[Path]`
  - `cli.cmd_hook(args) -> int`
  - `cli.cmd_backfill(args) -> int`
  - `cli.main(argv: list[str] | None = None) -> int`

**hook の契約:** `SessionEnd` hook は stdin に JSON を受け取る（`session_id` / `transcript_path` / `cwd` / `reason`）。`transcript_path` が読めない場合や例外時も**必ず exit 0**。エラーは `config.log_path()` へ追記する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_cli.py`:

```python
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cc2obsidian import cli
from cc2obsidian.state import State

ENTRY_USER = {
    "type": "user", "sessionId": "abc12345-0000", "cwd": "/Users/x/work/demo",
    "isSidechain": False, "timestamp": "2026-08-22T23:01:00.000Z",
    "message": {"role": "user", "content": "こんにちは"},
}
ENTRY_ASSISTANT = {
    "type": "assistant", "sessionId": "abc12345-0000", "cwd": "/Users/x/work/demo",
    "isSidechain": False, "timestamp": "2026-08-22T23:05:00.000Z",
    "message": {"role": "assistant", "model": "claude-opus-5",
                "content": [{"type": "text", "text": "どうも"}]},
}


def write_transcript(directory: Path, name="abc12345-0000.jsonl", entries=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    entries = entries or [ENTRY_USER, ENTRY_ASSISTANT]
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    return path


class ConvertOneTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.vault = self.root / "vault"
        self.state = State(self.root / "state.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_writes_a_note(self):
        src = write_transcript(self.root / "projects" / "demo")
        out = cli.convert_one(src, self.vault, self.state)
        self.assertTrue(out.exists())
        self.assertIn("こんにちは", out.read_text(encoding="utf-8"))

    def test_returns_none_for_transcript_without_turns(self):
        src = write_transcript(self.root / "projects" / "demo", "empty.jsonl",
                               [{"type": "mode", "mode": "default", "sessionId": "x"}])
        self.assertIsNone(cli.convert_one(src, self.vault, self.state))

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(cli.convert_one(self.root / "nope.jsonl", self.vault, self.state))


class IterTranscriptsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_finds_transcripts_in_every_project(self):
        write_transcript(self.root / "proj-a", "a.jsonl")
        write_transcript(self.root / "proj-b", "b.jsonl")
        self.assertEqual(len(cli.iter_transcripts(self.root, None)), 2)

    def test_since_filters_by_mtime(self):
        old = write_transcript(self.root / "proj-a", "old.jsonl")
        write_transcript(self.root / "proj-b", "new.jsonl")
        ancient = time.time() - 60 * 60 * 24 * 90
        import os
        os.utime(old, (ancient, ancient))
        got = cli.iter_transcripts(self.root, since_days=30)
        self.assertEqual([p.name for p in got], ["new.jsonl"])

    def test_missing_root_returns_empty(self):
        self.assertEqual(cli.iter_transcripts(self.root / "nope", None), [])


class HookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.vault = self.root / "vault"
        self.patches = [
            mock.patch("cc2obsidian.cli.config.vault_path", return_value=self.vault),
            mock.patch("cc2obsidian.cli.config.state_path", return_value=self.root / "state.json"),
            mock.patch("cc2obsidian.cli.config.log_path", return_value=self.root / "cc2obsidian.log"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def _run_hook(self, payload):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            return cli.main(["hook"])

    def test_converts_the_transcript_named_on_stdin(self):
        src = write_transcript(self.root / "projects" / "demo")
        self.assertEqual(self._run_hook({"transcript_path": str(src)}), 0)
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

    def test_exits_zero_on_malformed_stdin(self):
        with mock.patch("sys.stdin", io.StringIO("not json")):
            self.assertEqual(cli.main(["hook"]), 0)

    def test_exits_zero_when_transcript_is_missing(self):
        self.assertEqual(self._run_hook({"transcript_path": "/nope/x.jsonl"}), 0)

    def test_exits_zero_and_logs_when_conversion_raises(self):
        src = write_transcript(self.root / "projects" / "demo")
        with mock.patch("cc2obsidian.cli.convert_one", side_effect=RuntimeError("boom")):
            self.assertEqual(self._run_hook({"transcript_path": str(src)}), 0)
        self.assertIn("boom", (self.root / "cc2obsidian.log").read_text(encoding="utf-8"))


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.vault = self.root / "vault"
        self.projects = self.root / "projects"
        self.patches = [
            mock.patch("cc2obsidian.cli.config.vault_path", return_value=self.vault),
            mock.patch("cc2obsidian.cli.config.state_path", return_value=self.root / "state.json"),
            mock.patch("cc2obsidian.cli.config.log_path", return_value=self.root / "cc2obsidian.log"),
            mock.patch("cc2obsidian.cli.config.projects_dir", return_value=self.projects),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def test_converts_every_transcript(self):
        write_transcript(self.projects / "proj-a", "a.jsonl")
        self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

    def test_dry_run_writes_nothing(self):
        write_transcript(self.projects / "proj-a", "a.jsonl")
        self.assertEqual(cli.main(["backfill", "--all", "--dry-run"]), 0)
        self.assertFalse((self.vault / "Notes").exists())

    def test_second_run_skips_unchanged_transcripts(self):
        write_transcript(self.projects / "proj-a", "a.jsonl")
        cli.main(["backfill", "--all"])
        note = next((self.vault / "Notes").rglob("*.md"))
        before = note.stat().st_mtime_ns
        cli.main(["backfill", "--all"])
        self.assertEqual(note.stat().st_mtime_ns, before)

    def test_touched_transcript_is_reconverted(self):
        src = write_transcript(self.projects / "proj-a", "a.jsonl")
        cli.main(["backfill", "--all"])
        import os
        future = time.time() + 10
        os.utime(src, (future, future))
        cli.main(["backfill", "--all"])
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

    def test_one_bad_transcript_does_not_abort_the_run(self):
        (self.projects / "proj-bad").mkdir(parents=True)
        (self.projects / "proj-bad" / "bad.jsonl").write_text("{oops\n", encoding="utf-8")
        write_transcript(self.projects / "proj-good", "good.jsonl")
        self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest tests.test_cli -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc2obsidian.cli'`

- [ ] **Step 3: 最小実装を書く**

`cc2obsidian/cli.py`:

```python
"""cc2obsidian のコマンドライン。hook / backfill / digest を提供する。"""
import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import config
from .parse import parse_transcript
from .state import State
from .vault import write_note


def log_error(message: str) -> None:
    """hook を失敗させないため、エラーはファイルに落として黙って続行する。"""
    try:
        path = config.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def convert_one(path: Path, vault_root: Path, st: State, dry_run: bool = False) -> Path | None:
    """1 本の JSONL をノートへ変換する。会話が無ければ None。"""
    path = Path(path)
    if not path.is_file():
        return None
    session = parse_transcript(path)
    if session is None:
        return None
    return write_note(vault_root, session, st, path.stat().st_mtime, dry_run=dry_run)


# 注: JSONL のファイル名は <session-id>.jsonl なので path.stem が session_id になる。
# 万一ずれても needs_update が True を返して再変換されるだけで、壊れはしない。
def iter_transcripts(projects_root: Path, since_days: int | None) -> list[Path]:
    projects_root = Path(projects_root)
    if not projects_root.is_dir():
        return []
    cutoff = time.time() - since_days * 86400 if since_days else None
    found = [p for p in sorted(projects_root.glob("*/*.jsonl"))
             if cutoff is None or p.stat().st_mtime >= cutoff]
    return found


def cmd_hook(args) -> int:
    """SessionEnd hook 本体。何があっても 0 を返す。"""
    try:
        payload = json.load(sys.stdin)
        transcript = payload.get("transcript_path")
        if not transcript:
            return 0
        st = State(config.state_path())
        if convert_one(Path(transcript).expanduser(), config.vault_path(), st) is not None:
            st.save()
    except Exception:
        log_error("hook failed: " + traceback.format_exc().replace("\n", " | "))
    return 0


def cmd_backfill(args) -> int:
    st = State(config.state_path())
    since = None if args.all else args.since
    transcripts = iter_transcripts(config.projects_dir(), since)
    vault_root = config.vault_path()

    converted = skipped = failed = 0
    for path in transcripts:
        try:
            if not st.needs_update(path.stem, path.stat().st_mtime):
                skipped += 1
                continue
            if convert_one(path, vault_root, st, dry_run=args.dry_run) is None:
                skipped += 1
            else:
                converted += 1
        except Exception as exc:
            failed += 1
            log_error(f"backfill failed for {path}: {exc!r}")
            print(f"  失敗: {path} ({exc!r})", file=sys.stderr)

    if not args.dry_run:
        st.save()

    label = "(dry-run) " if args.dry_run else ""
    print(f"{label}変換 {converted} / スキップ {skipped} / 失敗 {failed}"
          f"（対象 {len(transcripts)} 本）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cc2obsidian")
    sub = parser.add_subparsers(dest="command", required=True)

    hook = sub.add_parser("hook", help="SessionEnd hook から呼ばれる")
    hook.set_defaults(func=cmd_hook)

    backfill = sub.add_parser("backfill", help="既存の JSONL をまとめて変換する")
    group = backfill.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="全期間を対象にする")
    group.add_argument("--since", type=int, metavar="DAYS", default=30,
                       help="直近 N 日を対象にする（既定 30）")
    backfill.add_argument("--dry-run", action="store_true", help="書き込まずに件数だけ出す")
    backfill.set_defaults(func=cmd_backfill)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（77 + 15 = 92 tests）

- [ ] **Step 5: 使い捨ての Vault で dry-run を確認**

Run:

```bash
cd ~/work/cc2obsidian
CC2OBSIDIAN_VAULT=$HOME/work/cc2obsidian/.tmp-vault python3 scripts/cc2obsidian.py backfill --all --dry-run
```

Expected: `(dry-run) 変換 N / スキップ M / 失敗 0（対象 N 本）` が出て、`~/work/cc2obsidian/.tmp-vault` は作られない。

- [ ] **Step 6: コミット**

```bash
cd ~/work/cc2obsidian
git add cc2obsidian/cli.py tests/test_cli.py
git commit -m "feat: add hook and backfill commands

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: digest

**Files:**
- Create: `cc2obsidian/digest.py`
- Modify: `cc2obsidian/cli.py`（`cmd_digest` と `digest` サブパーサを追加）
- Create: `tests/test_digest.py`

**Interfaces:**
- Consumes: `config.vault_path`
- Produces:
  - `digest.parse_frontmatter(text: str) -> dict[str, str]`
  - `digest.extract_user_turns(text: str) -> list[str]`
  - `digest.build_digest(vault_root: Path, since_days: int) -> str`
  - `cli.cmd_digest(args) -> int`

**なぜ必要か:** 1 週間分のノートを丸ごと読むとコンテキストが溢れる。frontmatter とユーザー発話だけを抜き出して 1 本のテキストに束ねる。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_digest.py`:

~~~~python
import tempfile
import time
import unittest
from pathlib import Path

from cc2obsidian import digest

NOTE = """---
date: 2026-08-23
time: "08:01"
project: demo
session_id: abc12345-0000
title: スキル作成相談
duration_min: 42
user_turns: 2
model: claude-opus-5
tool_counts: {Bash: 6}
tags: [claude-code/session, project/demo]
---

# スキル作成相談

## 👤 08:01
最初の質問です

<details><summary>💭 thinking</summary>

内心の声

</details>

## 🤖 08:02
回答します

<details><summary>🔧 Bash — 一覧</summary>

```bash
ls
```

</details>

## 👤 08:10
二つ目の質問です
"""


class FrontmatterTest(unittest.TestCase):
    def test_reads_key_values(self):
        fm = digest.parse_frontmatter(NOTE)
        self.assertEqual(fm["date"], "2026-08-23")
        self.assertEqual(fm["project"], "demo")
        self.assertEqual(fm["model"], "claude-opus-5")

    def test_strips_quotes(self):
        self.assertEqual(digest.parse_frontmatter(NOTE)["time"], "08:01")

    def test_missing_frontmatter_yields_empty(self):
        self.assertEqual(digest.parse_frontmatter("# just a heading"), {})


class UserTurnsTest(unittest.TestCase):
    def test_extracts_every_user_turn(self):
        turns = digest.extract_user_turns(NOTE)
        self.assertEqual(turns, ["最初の質問です", "二つ目の質問です"])

    def test_ignores_assistant_and_folded_blocks(self):
        joined = "\n".join(digest.extract_user_turns(NOTE))
        self.assertNotIn("回答します", joined)
        self.assertNotIn("内心の声", joined)
        self.assertNotIn("ls", joined)


class BuildDigestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.notes = self.root / "Notes" / "2026-08-23"
        self.notes.mkdir(parents=True)

    def tearDown(self):
        self.dir.cleanup()

    def test_includes_metadata_and_user_turns(self):
        (self.notes / "0801-demo-スキル作成相談.md").write_text(NOTE, encoding="utf-8")
        out = digest.build_digest(self.root, since_days=3650)
        self.assertIn("スキル作成相談", out)
        self.assertIn("claude-opus-5", out)
        self.assertIn("最初の質問です", out)
        self.assertNotIn("回答します", out)

    def test_note_links_use_wikilink_form(self):
        (self.notes / "0801-demo-スキル作成相談.md").write_text(NOTE, encoding="utf-8")
        self.assertIn("[[0801-demo-スキル作成相談]]", digest.build_digest(self.root, 3650))

    def test_old_notes_are_excluded(self):
        old = self.notes / "0801-demo-古い.md"
        old.write_text(NOTE, encoding="utf-8")
        import os
        ancient = time.time() - 86400 * 90
        os.utime(old, (ancient, ancient))
        self.assertNotIn("古い", digest.build_digest(self.root, since_days=7))

    def test_weekly_notes_are_not_included(self):
        weekly = self.root / "Notes" / "weekly"
        weekly.mkdir(parents=True)
        (weekly / "2026-W34.md").write_text(NOTE, encoding="utf-8")
        self.assertNotIn("2026-W34", digest.build_digest(self.root, 3650))

    def test_empty_vault_says_so(self):
        self.assertIn("対象なし", digest.build_digest(self.root, since_days=7))


if __name__ == "__main__":
    unittest.main()
~~~~

- [ ] **Step 2: テストが落ちることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest tests.test_digest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc2obsidian.digest'`

- [ ] **Step 3: 最小実装を書く**

`cc2obsidian/digest.py`:

```python
"""週次分析のために、ノート群から軽量なダイジェストを作る。"""
import re
import time
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_USER_HEADING = re.compile(r"^## 👤 ")
_ANY_HEADING = re.compile(r"^## ")
_DETAILS_OPEN = re.compile(r"^<details")
_DETAILS_CLOSE = re.compile(r"^</details>")

DIGEST_FIELDS = ("date", "time", "project", "title", "duration_min",
                 "user_turns", "model", "models", "tool_counts", "tags")


def parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def extract_user_turns(text: str) -> list[str]:
    """## 👤 見出しの直下の本文だけを拾う。折りたたみの中は読まない。"""
    turns, buffer = [], None
    depth = 0
    for line in text.splitlines():
        if _DETAILS_OPEN.match(line):
            depth += 1
            continue
        if _DETAILS_CLOSE.match(line):
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        if _USER_HEADING.match(line):
            if buffer is not None:
                turns.append("\n".join(buffer).strip())
            buffer = []
            continue
        if _ANY_HEADING.match(line):
            if buffer is not None:
                turns.append("\n".join(buffer).strip())
            buffer = None
            continue
        if buffer is not None:
            buffer.append(line)
    if buffer is not None:
        turns.append("\n".join(buffer).strip())
    return [t for t in turns if t]


def _note_paths(vault_root: Path, since_days: int) -> list[Path]:
    notes_dir = Path(vault_root) / "Notes"
    if not notes_dir.is_dir():
        return []
    cutoff = time.time() - since_days * 86400
    paths = [
        p for p in notes_dir.rglob("*.md")
        if p.parent.name != "weekly" and p.stat().st_mtime >= cutoff
    ]
    return sorted(paths)


def build_digest(vault_root: Path, since_days: int) -> str:
    paths = _note_paths(vault_root, since_days)
    if not paths:
        return f"# セッションダイジェスト（直近 {since_days} 日）\n\n対象なし\n"

    blocks = [f"# セッションダイジェスト（直近 {since_days} 日 / {len(paths)} セッション）", ""]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        fields = parse_frontmatter(text)
        meta = " / ".join(f"{k}={fields[k]}" for k in DIGEST_FIELDS if k in fields)
        blocks.append(f"## [[{path.stem}]]")
        blocks.append(meta)
        blocks.append("")
        blocks.append("### ユーザー発話")
        for turn in extract_user_turns(text):
            blocks.append(f"- {turn}")
        blocks.append("")
    return "\n".join(blocks) + "\n"
```

`cc2obsidian/cli.py` に追記（`from .digest import build_digest` を既存 import 群へ、`cmd_digest` を `cmd_backfill` の下へ、サブパーサを `build_parser` 内へ）:

```python
def cmd_digest(args) -> int:
    print(build_digest(config.vault_path(), args.since), end="")
    return 0
```

```python
    dg = sub.add_parser("digest", help="週次分析用のダイジェストを標準出力へ")
    dg.add_argument("--since", type=int, metavar="DAYS", default=7,
                    help="直近 N 日を対象にする（既定 7）")
    dg.set_defaults(func=cmd_digest)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（92 + 10 = 102 tests）

- [ ] **Step 5: コミット**

```bash
cd ~/work/cc2obsidian
git add cc2obsidian/digest.py cc2obsidian/cli.py tests/test_digest.py
git commit -m "feat: add digest command for weekly review

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: weekly-review スキル

**Files:**
- Create: `skills/weekly-review/SKILL.md`
- Create: `README.md`
- Create symlink: `~/.claude/skills/weekly-review -> ~/work/cc2obsidian/skills/weekly-review`

**Interfaces:**
- Consumes: `python3 ~/work/cc2obsidian/scripts/cc2obsidian.py digest --since N`
- Produces: `<Vault>/Notes/weekly/YYYY-Www.md`

- [ ] **Step 1: SKILL.md を書く**

`skills/weekly-review/SKILL.md`:

~~~~markdown
---
name: weekly-review
description: Use when the user wants to look back at their recent Claude Code sessions to find repeated work worth turning into a skill or knowledge worth promoting - triggers on "週次振り返り", "今週の振り返り", "weekly review", "最近の作業を分析して", or asking what patterns show up in their recent sessions.
---

# 週次振り返り

Obsidian Vault に蓄積された Claude Code セッションを読み、定型作業とナレッジ候補を抽出する。

## 手順

### 1. 期間を決める

既定は直近 7 日。ユーザーが期間を指定したらそれに従う。

### 2. ダイジェストを取得する

ノートを直接読まないこと。1 週間分の全文はコンテキストに収まらない。

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py digest --since 7
```

「対象なし」が返ったら、まだノートが無い。バックフィルを勧めて終了する。

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --all
```

### 3. 分析する

ダイジェストを読み、次の 4 点を抽出する。**根拠のないパターンを書かないこと。** 各項目には必ず出典セッションの `[[wikilink]]` を添える。

1. **繰り返し出現した作業パターン** — 2 回以上現れた手順。スキル化候補として、何を自動化できるかまで書く
2. **汎用的に再利用できる知見** — 他のプロジェクトでも効く知識。`Knowledge/` 昇格候補。**提案のみ書く。ファイルは作らない**
3. **詰まった箇所・手戻り** — やり直しや長引いた箇所。次に同じ轍を踏まないための示唆
4. **プロジェクト別の時間配分** — `duration_min` と `project` の集計。使ったモデルの内訳も添える

### 4. 週次ノートを書く

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

### 5. 昇格を確認する

`Knowledge/` へ昇格させたい項目があれば、**どれを昇格するかユーザーに確認してから**ファイルを作る。勝手に作らない。

## 禁止事項

- ダイジェストを経由せずノートを直接大量に読むこと（コンテキストが溢れる）
- 出典セッションを示さずにパターンを主張すること
- ユーザーの承認なく `Knowledge/` にファイルを作ること
~~~~

注: SKILL.md は frontmatter (`name` / `description`) から始まること。書いたあとに `python3 -c "import pathlib;print(pathlib.Path('skills/weekly-review/SKILL.md').read_text().count(chr(96)*3))"` が偶数を返すか（フェンスが閉じているか）確認する。

- [ ] **Step 2: README を書く**

`README.md`:

~~~~markdown
# cc2obsidian

Claude Code のセッションを Obsidian Vault へ記録し、週次で振り返る。

## 仕組み

- `SessionEnd` hook が `scripts/cc2obsidian.py hook` を呼び、セッション JSONL を Markdown に変換して Vault へ書く
- 出力先は `<Vault>/Notes/<YYYY-MM-DD>/<HHMM>-<project>-<title>.md`
- 会話は全文、thinking とツール呼び出しは `<details>` で折りたたむ。長いツール出力は先頭 40 行 + 末尾 10 行に切り詰める
- `/weekly-review` スキルが直近 7 日を分析し、`<Vault>/Notes/weekly/` に週次ノートを書く

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
~~~~

- [ ] **Step 3: symlink を張って認識を確認**

```bash
ln -sfn ~/work/cc2obsidian/skills/weekly-review ~/.claude/skills/weekly-review
ls -l ~/.claude/skills/
head -4 ~/.claude/skills/weekly-review/SKILL.md
```

Expected: symlink が `~/work/cc2obsidian/skills/weekly-review` を指し、frontmatter の `name: weekly-review` が読める。

- [ ] **Step 4: digest が実際に動くことを確認**

```bash
cd ~/work/cc2obsidian
CC2OBSIDIAN_VAULT=$HOME/work/cc2obsidian/.tmp-vault python3 scripts/cc2obsidian.py digest --since 7
```

Expected: 「対象なし」または実際のダイジェストが出る（この時点ではまだ本番 Vault へ書いていないので「対象なし」が正常）。

- [ ] **Step 5: コミット**

```bash
cd ~/work/cc2obsidian
git add skills/weekly-review/SKILL.md README.md
git commit -m "feat: add weekly-review skill and README

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: 本番導入

**Files:**
- Modify: `~/.claude/settings.json`（`SessionEnd` hook を追加）

**Interfaces:**
- Consumes: Task 6 の `hook` サブコマンド
- Produces: 稼働状態

**順序が重要:** hook を先に登録すると、不具合があったときセッション終了のたびにエラーが出る。dry-run → 本実行 → hook 登録の順を守る。

- [ ] **Step 1: 全テストを通す**

Run: `cd ~/work/cc2obsidian && python3 -m unittest discover -s tests -t . -v`
Expected: PASS（102 tests、失敗 0）

- [ ] **Step 2: 本番 Vault に対して dry-run**

Run:

```bash
cd ~/work/cc2obsidian
python3 scripts/cc2obsidian.py backfill --all --dry-run
```

Expected: `(dry-run) 変換 N / スキップ M / 失敗 0（対象 N 本）`。**失敗が 1 件でもあれば先へ進まず原因を調べる。**

- [ ] **Step 3: 使い捨て Vault で出力を目視確認**

Run:

```bash
rm -rf ~/work/cc2obsidian/.tmp-vault
CC2OBSIDIAN_VAULT=$HOME/work/cc2obsidian/.tmp-vault python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --since 3
find ~/work/cc2obsidian/.tmp-vault -name '*.md' | head -5
head -40 "$(find ~/work/cc2obsidian/.tmp-vault -name '*.md' | head -1)"
du -sh ~/work/cc2obsidian/.tmp-vault
```

Expected: 日付ディレクトリの下に `HHMM-<project>-<title>.md` があり、frontmatter・見出し・折りたたみが期待通り。ユーザーに見せて確認を取る。

- [ ] **Step 4: 本番 Vault へ取り込む**

Run:

```bash
python3 ~/work/cc2obsidian/scripts/cc2obsidian.py backfill --all
find ~/private/obsidian/Obsidian/Notes -name '*.md' | wc -l
du -sh ~/private/obsidian/Obsidian/Notes
```

Expected: ノート数が dry-run の変換件数と一致し、Vault のサイズが妥当（元の 92MB より小さいはず）。

- [ ] **Step 5: SessionEnd hook を登録**

`~/.claude/settings.json` の `hooks` に追加する。既存の `PreToolUse`（rtk）は消さないこと。

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path("~/.claude/settings.json").expanduser()
settings = json.loads(path.read_text(encoding="utf-8"))
hooks = settings.setdefault("hooks", {})
hooks["SessionEnd"] = [
    {"hooks": [{"type": "command",
                "command": "python3 ~/work/cc2obsidian/scripts/cc2obsidian.py hook"}]}
]
path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(settings["hooks"], ensure_ascii=False, indent=2))
PY
```

Expected: `PreToolUse` と `SessionEnd` の両方が出力される。

- [ ] **Step 6: hook を手動で叩いて動作確認**

Run:

```bash
LATEST=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "{\"transcript_path\": \"$LATEST\"}" | python3 ~/work/cc2obsidian/scripts/cc2obsidian.py hook
echo "exit=$?"
cat ~/.claude/cc2obsidian.log 2>/dev/null || echo "(ログなし = エラーなし)"
```

Expected: `exit=0`、ログが空か存在しない。

- [ ] **Step 7: コミット**

```bash
cd ~/work/cc2obsidian
git add -A
git commit -m "chore: record production rollout

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" --allow-empty
```

- [ ] **Step 8: 実セッションで確認するようユーザーに伝える**

次に Claude Code を `exit` で終了したあと、`~/private/obsidian/Obsidian/Notes/<今日の日付>/` に新しいノートが増えているか確認してもらう。増えていなければ `~/.claude/cc2obsidian.log` を見る。
