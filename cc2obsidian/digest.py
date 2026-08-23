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
