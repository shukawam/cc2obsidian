"""cc2obsidian が読み書きするパスの解決。"""
import os
from pathlib import Path

DEFAULT_VAULT = "~/private/obsidian/Obsidian"
DEFAULT_STATE = "~/.claude/cc2obsidian-state.json"
DEFAULT_LOG = "~/.claude/cc2obsidian.log"
DEFAULT_CLAUDE_PROJECTS = "~/.claude/projects"
DEFAULT_CODEX_SESSIONS = "~/.codex/sessions"
DEFAULT_CODEX_ARCHIVED_SESSIONS = "~/.codex/archived_sessions"


def vault_path() -> Path:
    return Path(os.environ.get("CC2OBSIDIAN_VAULT", DEFAULT_VAULT)).expanduser().resolve()


def state_path() -> Path:
    # Keep the historical default so existing installations retain their
    # idempotency data when Codex support is enabled.
    return Path(os.environ.get("CC2OBSIDIAN_STATE", DEFAULT_STATE)).expanduser()


def log_path() -> Path:
    return Path(os.environ.get("CC2OBSIDIAN_LOG", DEFAULT_LOG)).expanduser()


def projects_dir() -> Path:
    """Backward-compatible alias for the Claude Code transcript root."""
    return claude_projects_dir()


def claude_projects_dir() -> Path:
    return Path(os.environ.get(
        "CC2OBSIDIAN_CLAUDE_PROJECTS", DEFAULT_CLAUDE_PROJECTS,
    )).expanduser()


def codex_sessions_dir() -> Path:
    return Path(os.environ.get(
        "CC2OBSIDIAN_CODEX_SESSIONS", DEFAULT_CODEX_SESSIONS,
    )).expanduser()


def codex_archived_sessions_dir() -> Path:
    return Path(os.environ.get(
        "CC2OBSIDIAN_CODEX_ARCHIVED_SESSIONS", DEFAULT_CODEX_ARCHIVED_SESSIONS,
    )).expanduser()
