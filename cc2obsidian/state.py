"""session_id と出力ノートの対応を記録し、再生成の要否を判定する。"""
from contextlib import contextmanager
import json
import os
import tempfile
from pathlib import Path

from .model import DEFAULT_SOURCE

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows では排他なしで従来動作を保つ
    fcntl = None


def _state_key(session_id: str, source: str) -> str:
    """異なる会話ハーネスで同じ session_id が使われても衝突しないキー。"""
    return f"{source}:{session_id}"


@contextmanager
def _locked(lock_path: Path):
    """state の read-merge-replace 全体をプロセス間で直列化する。"""
    if fcntl is None:
        yield
        return

    # state 本体を lock すると os.replace 後は別 inode になり、待機中の
    # プロセスと新規プロセスが別々の inode を掴めてしまう。置き換えない
    # 専用ファイルをロック対象にする。
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _normalize_vault(vault_root) -> str | None:
    """Vault のパス表現を揺れなく比較できる文字列に揃える。"""
    return None if vault_root is None else str(vault_root)


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        # このインスタンスが実際に put したキー。save() で書き戻してよいのは
        # ここに入っているものだけ。
        self._dirty: set[str] = set()
        if self.path.exists():
            # OSError（読めない）はここで握りつぶさない。握りつぶすと、
            # 次の save() が「空の状態」を正規の内容として書き出してしまい、
            # 既存のエントリを丸ごと消してしまう。読めないなら諦めて例外を
            # 上に伝える方が安全。JSON が壊れているだけなら、そのファイルは
            # もう使えないので空として作り直す。
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except json.JSONDecodeError:
                self._data = {}  # 壊れた state は捨てて作り直す

    def _entry(self, session_id: str, source: str) -> dict | None:
        """名前空間付きエントリを返す。旧キーは Claude としてだけ読む。"""
        entry = self._data.get(_state_key(session_id, source))
        if entry is None and source == DEFAULT_SOURCE:
            entry = self._data.get(session_id)
        return entry

    def get(self, session_id: str, vault_root=None, *,
            source: str = DEFAULT_SOURCE) -> dict | None:
        entry = self._entry(session_id, source)
        if entry is None:
            return None
        # 記録された Vault と問い合わせ元の Vault が違えば、そのエントリは
        # この Vault にとって存在しないものとして扱う（needs_update と同じ規約）。
        if entry.get("vault") != _normalize_vault(vault_root):
            return None
        return entry

    def needs_update(self, session_id: str, source_mtime: float, vault_root=None, *,
                     source: str = DEFAULT_SOURCE) -> bool:
        entry = self._entry(session_id, source)
        if entry is None:
            return True
        # 記録された Vault と問い合わせ元の Vault が違えば、そちらにはまだ
        # ノートが無いということなので更新が要る。旧バージョンが書いた
        # エントリには vault が無いので、それも不一致（＝要更新）とみなす。
        if entry.get("vault") != _normalize_vault(vault_root):
            return True
        # 記録があってもノート本体が無ければ作り直す。ノートを消したのに
        # state が残っていると「変換済み」と判定され、二度と復元されない。
        relpath = entry.get("path")
        if vault_root is not None and relpath:
            if not (Path(vault_root) / relpath).exists():
                return True
        return source_mtime > entry.get("source_mtime", 0)

    def put(self, session_id: str, relpath: str, source_mtime: float, vault_root=None, *,
            source: str = DEFAULT_SOURCE) -> None:
        key = _state_key(session_id, source)
        self._data[key] = {
            "path": relpath,
            "source_mtime": source_mtime,
            "vault": _normalize_vault(vault_root),
        }
        self._dirty.add(key)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 書く直前にディスク上の内容を読み直し、自分がロードしてから他の
        # プロセス（hook / backfill の別実行）が書いたかもしれないエントリを
        # 下敷きにしてマージする。キーが競合したら自分（メモリ上）の値が勝つ。
        # これが無いと「後勝ち」の丸ごと上書きになり、片方の書き込みが
        # 消える。
        # 書き戻すのは自分が put したキーだけ。self._data を丸ごと重ねると、
        # ロード時点の古いエントリまで一緒に書き戻してしまい、その間に他の
        # プロセスが更新したキーが巻き戻る。
        mine = {k: self._data[k] for k in self._dirty if k in self._data}
        lock_path = self.path.with_name(self.path.name + ".lock")
        with _locked(lock_path):
            # 読み直しから置き換えまでを同じ lock の中に置く。単に各 write を
            # lock するだけでは、両者が古い state を読んでから順番に上書きし、
            # 先行プロセスのエントリを失う競合が残る。
            merged = dict(self._data)
            try:
                on_disk = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(on_disk, dict):
                    merged = {**on_disk, **mine}
            except (OSError, json.JSONDecodeError):
                pass  # 読めない/壊れているなら、自分の内容だけで書く

            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(merged, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
                self._data = merged
                self._dirty.clear()
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
