"""session_id と出力ノートの対応を記録し、再生成の要否を判定する。"""
import json
import os
import tempfile
from pathlib import Path


def _normalize_vault(vault_root) -> str | None:
    """Vault のパス表現を揺れなく比較できる文字列に揃える。"""
    return None if vault_root is None else str(vault_root)


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

    def get(self, session_id: str, vault_root=None) -> dict | None:
        entry = self._data.get(session_id)
        if entry is None:
            return None
        # 記録された Vault と問い合わせ元の Vault が違えば、そのエントリは
        # この Vault にとって存在しないものとして扱う（needs_update と同じ規約）。
        if entry.get("vault") != _normalize_vault(vault_root):
            return None
        return entry

    def needs_update(self, session_id: str, source_mtime: float, vault_root=None) -> bool:
        entry = self._data.get(session_id)
        if entry is None:
            return True
        # 記録された Vault と問い合わせ元の Vault が違えば、そちらにはまだ
        # ノートが無いということなので更新が要る。旧バージョンが書いた
        # エントリには vault が無いので、それも不一致（＝要更新）とみなす。
        if entry.get("vault") != _normalize_vault(vault_root):
            return True
        return source_mtime > entry.get("source_mtime", 0)

    def put(self, session_id: str, relpath: str, source_mtime: float, vault_root=None) -> None:
        self._data[session_id] = {
            "path": relpath,
            "source_mtime": source_mtime,
            "vault": _normalize_vault(vault_root),
        }

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
