"""Vault へのノート書き込み。冪等性とファイル名衝突を扱う。"""
from pathlib import Path

from .digest import parse_frontmatter
from .model import Session
from .render import render_note
from .slugs import note_relpath
from .state import State


def _note_session_id(path: Path) -> str | None:
    """ノートの frontmatter から session_id を読む。読めなければ None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_frontmatter(text).get("session_id")


def _target_relpath(vault_root: Path, session: Session, st: State) -> Path:
    """書き込み先の相対パスを決める。他セッションと衝突したら短い id を足す。"""
    relpath = note_relpath(
        session.started_at, session.project, session.title, session.session_id
    )
    known = st.get(session.session_id, vault_root=vault_root)
    if known and known.get("path") == str(relpath):
        return relpath  # 自分の既存ノート。そのまま上書きする

    target = vault_root / relpath
    if target.exists():
        # state にエントリが無い（失われた）場合でも、そこにある実ファイルの
        # frontmatter が自分自身の session_id を指しているなら、それは
        # 自分のノートである。Vault を正として、そのまま上書きする。
        if _note_session_id(target) == session.session_id:
            return relpath
        # 本当に他セッションのノートが場所を取っている
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

    # タイトル変更などでパスが移る場合、消してよいのは本当に自分の
    # セッションのノートだけ。state のエントリが指す path を無条件に
    # 信用せず、そのファイル自身の frontmatter で確認する（state キーが
    # 別セッションと衝突していても他人のノートを消さないため）。
    old_path = None
    known = st.get(session.session_id, vault_root=vault_root)
    if known and known.get("path") != str(relpath):
        candidate = vault_root / known["path"]
        if _note_session_id(candidate) == session.session_id:
            old_path = candidate

    # 新しいノートを書き切ってから古いノートを消す。逆順だと、書き込みが
    # 失敗した場合に新旧どちらのノートも残らなくなる。
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_note(session), encoding="utf-8")

    if old_path is not None:
        old_path.unlink(missing_ok=True)

    st.put(session.session_id, str(relpath), source_mtime, vault_root=vault_root)
    return target
