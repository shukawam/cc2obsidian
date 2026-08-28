"""週次分析のために、ノート群から軽量なダイジェストを作る。"""
import re
from datetime import datetime, timedelta
from pathlib import Path

from cc2obsidian.render import ROLE_HEADINGS, heading_regex
from cc2obsidian.slugs import JST

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
# render.py が実際に出す見出し形式 ("## 👤 HH:MM" / "## 🤖 HH:MM") から
# 正規表現を組み立てる。render.py 側の見出し形式が変われば、ここも
# 自動的に追随する（HeadingSyncTest がこの結びつきを検査する）。
_USER_HEADING = re.compile(heading_regex(ROLE_HEADINGS["user"]))
_ANY_HEADING = re.compile(heading_regex())
_DETAILS_OPEN = re.compile(r"^<details")
_DETAILS_CLOSE = re.compile(r"^</details>")
_FENCE = re.compile(r"^(`{3,})")


def _with_fence_flags(lines):
    """各行に「コードフェンスの中か」を添えて流す。

    ノート本文にはツール出力がそのまま埋まっており、そこに "<details>" や
    "## 🤖 12:34" と読める行が混ざる。行単位で構造を数えると折りたたみの
    ネストが狂い、以降のユーザー発話が丸ごと読み飛ばされる。切り詰め
    （truncate_output）で閉じタグ側だけが落ちると、ずれたまま最後まで
    復帰しない。

    render.py の _code_block は本文中の最長バッククォート連より長いフェンスを
    選ぶので、フェンスの中に同じ長さの区切り行が現れることはない。よって
    「開いたフェンスは、同じ長さ以上のバッククォートだけの行で閉じる」で
    正しく対応が取れる。

    フラグは構造（折りたたみ・見出し）の判定にだけ使う。本文としては
    フェンスの中身も残す — コードブロックだけのユーザー発話が空になって
    しまうため。
    """
    fence_len = 0
    for line in lines:
        match = _FENCE.match(line)
        if fence_len:
            if (match and len(match.group(1)) >= fence_len
                    and not line[len(match.group(1)):].strip()):
                fence_len = 0
            yield line, True
            continue
        if match:
            fence_len = len(match.group(1))
            yield line, True
            continue
        yield line, False


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
    for line, in_fence in _with_fence_flags(text.splitlines()):
        if not in_fence:
            if _DETAILS_OPEN.match(line):
                depth += 1
                continue
            if _DETAILS_CLOSE.match(line):
                depth = max(0, depth - 1)
                continue
        if depth:
            continue
        if not in_fence:
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
    """`Notes/raw/<YYYY-MM-DD>/` の日付（= セッションが実際に行われた日、JST）で絞り込む。

    ファイルの mtime は使わない — backfill --all はすべてのノートを「今」
    書き直すため、mtime ベースのフィルタは --since N を実質無視してしまう。
    走査は raw/ 配下のみ。daily/ や weekly/ はセッションノートではない。
    """
    notes_dir = Path(vault_root) / "Notes" / "raw"
    if not notes_dir.is_dir():
        return [], 0
    cutoff_date = datetime.now(JST).date() - timedelta(days=since_days)
    paths = []
    skipped = 0
    for p in notes_dir.rglob("*.md"):
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
