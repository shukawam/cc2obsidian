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
    st.put(session.session_id, str(relpath), source_mtime, vault_root=vault_root)
    return target
