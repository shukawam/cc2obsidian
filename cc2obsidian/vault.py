"""Vault へのノート書き込み。冪等性とファイル名衝突を扱う。"""
import os
import tempfile
from pathlib import Path

from .digest import parse_frontmatter
from .model import DEFAULT_SOURCE, Session
from .render import render_note
from .slugs import note_relpath
from .state import State


def _note_identity(path: Path) -> tuple[str, str] | None:
    """frontmatter から ``(source, session_id)`` を読む。

    source の無い既存ノートは Claude Code が生成したものとして扱う。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fields = parse_frontmatter(text)
    session_id = fields.get("session_id")
    if not session_id:
        return None
    return fields.get("source") or DEFAULT_SOURCE, session_id


def _is_owned_by(path: Path, session: Session) -> bool:
    return _note_identity(path) == (session.source, session.session_id)


def _is_owned_by_another(path: Path, session: Session) -> bool:
    """所有者を読み取れて、しかもそれが自分でない場合だけ True。

    frontmatter を読めないノート（手で壊した・書きかけ）は「他人のもの」では
    ない。state が自分のパスだと記録しているなら、そこへ書き直してよい。
    """
    identity = _note_identity(path)
    return identity is not None and identity != (session.source, session.session_id)


def _target_relpath(vault_root: Path, session: Session, st: State) -> Path:
    """書き込み先の相対パスを決める。他セッションと衝突したら短い id を足す。"""
    relpath = note_relpath(
        session.started_at, session.project, session.title, session.session_id
    )
    known = st.get(session.session_id, vault_root=vault_root, source=session.source)
    if known and known.get("path") == str(relpath):
        target = vault_root / relpath
        if not _is_owned_by_another(target, session):
            return relpath  # 自分の既存ノート。そのまま上書きする

    target = vault_root / relpath
    if target.exists():
        # state にエントリが無い（失われた）場合でも、そこにある実ファイルの
        # frontmatter が自分自身の session_id を指しているなら、それは
        # 自分のノートである。Vault を正として、そのまま上書きする。
        if _is_owned_by(target, session):
            return relpath
        # 本当に他セッションのノートが場所を取っている
        return note_relpath(
            session.started_at, session.project, session.title,
            session.session_id, disambiguate=True,
        )
    return relpath


def _atomic_write(target: Path, content: str) -> None:
    """同一ディレクトリの一時ファイルへ書いてから置き換える。

    write_text は既存ファイルをその場で切り詰めるため、同じパスを更新する
    途中で失敗すると旧ノートまで失われる。置き換え方式なら、失敗しても
    ディスク上には元のノートがそのまま残る。
    """
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_note(
    vault_root: Path,
    session: Session,
    st: State,
    source_mtime: float,
    dry_run: bool = False,
) -> Path:
    relpath = _target_relpath(vault_root, session, st)
    target = vault_root / relpath

    # dry-run でもレンダリングまでは通す。README は dry-run を「変換に
    # 問題があれば hook 登録前に検出する」手順として案内しており、
    # 描画を飛ばすとその保証が無くなる。
    content = render_note(session)

    if dry_run:
        return target

    # タイトル変更などでパスが移る場合、消してよいのは本当に自分の
    # セッションのノートだけ。state のエントリが指す path を無条件に
    # 信用せず、そのファイル自身の frontmatter で確認する（state キーが
    # 別セッションと衝突していても他人のノートを消さないため）。
    old_path = None
    known = st.get(session.session_id, vault_root=vault_root, source=session.source)
    if known and known.get("path") != str(relpath):
        candidate = vault_root / known["path"]
        if _is_owned_by(candidate, session):
            old_path = candidate

    # 新しいノートを書き切ってから古いノートを消す。逆順だと、書き込みが
    # 失敗した場合に新旧どちらのノートも残らなくなる。
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, content)

    if old_path is not None:
        old_path.unlink(missing_ok=True)

    st.put(session.session_id, str(relpath), source_mtime, vault_root=vault_root,
           source=session.source)
    return target
