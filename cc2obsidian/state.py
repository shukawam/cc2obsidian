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
