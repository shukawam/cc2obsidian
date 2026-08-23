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
