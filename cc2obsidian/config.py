"""cc2obsidian が読み書きするパスの解決。"""
import os
from pathlib import Path

DEFAULT_VAULT = "~/private/obsidian/Obsidian"


def vault_path() -> Path:
    return Path(os.environ.get("CC2OBSIDIAN_VAULT", DEFAULT_VAULT)).expanduser().resolve()


def state_path() -> Path:
    return Path("~/.claude/cc2obsidian-state.json").expanduser()


def log_path() -> Path:
    return Path("~/.claude/cc2obsidian.log").expanduser()


def projects_dir() -> Path:
    return Path("~/.claude/projects").expanduser()
