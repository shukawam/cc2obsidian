"""週次分析のために、ノート群から軽量なダイジェストを作る。"""
import re
from datetime import datetime, timedelta
from pathlib import Path

from cc2obsidian.slugs import JST

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


def _note_paths(vault_root: Path, since_days: int) -> tuple[list[Path], int]:
    """`Notes/<YYYY-MM-DD>/` の日付（= セッションが実際に行われた日、JST）で絞り込む。

    ファイルの mtime は使わない — backfill --all はすべてのノートを「今」
    書き直すため、mtime ベースのフィルタは --since N を実質無視してしまう。
    """
    notes_dir = Path(vault_root) / "Notes"
    if not notes_dir.is_dir():
        return [], 0
    cutoff_date = datetime.now(JST).date() - timedelta(days=since_days)
    paths = []
    skipped = 0
    for p in notes_dir.rglob("*.md"):
        if p.parent.name == "weekly":
            continue
        try:
            note_date = datetime.strptime(p.parent.name, "%Y-%m-%d").date()
        except ValueError:
            skipped += 1
            continue
        try:
            p.stat()
        except OSError:
            skipped += 1
            continue
        if note_date >= cutoff_date:
            paths.append(p)
    return sorted(paths), skipped


def build_digest(vault_root: Path, since_days: int) -> str:
    paths, stat_skipped = _note_paths(vault_root, since_days)
    if not paths:
        if stat_skipped > 0:
            return f"# セッションダイジェスト（直近 {since_days} 日）\n\n対象なし（読み取れなかったノート: {stat_skipped} 件）\n"
        return f"# セッションダイジェスト（直近 {since_days} 日）\n\n対象なし\n"

    blocks = [""]
    rendered = 0
    read_skipped = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            read_skipped += 1
            continue
        fields = parse_frontmatter(text)
        meta = " / ".join(f"{k}={fields[k]}" for k in DIGEST_FIELDS if k in fields)
        blocks.append(f"## [[{path.stem}]]")
        blocks.append(meta)
        blocks.append("")
        blocks.append("### ユーザー発話")
        for turn in extract_user_turns(text):
            blocks.append(f"- {turn}")
        blocks.append("")
        rendered += 1

    total_skipped = stat_skipped + read_skipped
    header = f"# セッションダイジェスト（直近 {since_days} 日 / {rendered} セッション）"
    if total_skipped > 0:
        header += f"（読み取れなかったノート: {total_skipped} 件）"
    blocks[0] = header

    return "\n".join(blocks) + "\n"
