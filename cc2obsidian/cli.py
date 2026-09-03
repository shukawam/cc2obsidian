"""cc2obsidian のコマンドライン。hook / backfill / digest を提供する。"""
import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import config
from .digest import build_digest
from .parse import parse_transcript
from .parse_codex import parse_codex_transcript, peek_codex_metadata
from .state import State
from .vault import write_note

SOURCE_CLAUDE = "claude-code"
SOURCE_CODEX = "codex"
SOURCE_ALL = "all"


def log_error(message: str) -> None:
    """hook を失敗させないため、エラーはファイルに落として黙って続行する。"""
    try:
        path = config.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def parse_source_transcript(
    path: Path,
    source: str,
    *,
    session_id_hint: str = "",
    cwd_hint: str = "",
    model_hint: str = "",
):
    """ハーネスごとの raw JSONL を共通の Session に変換する。"""
    if source == SOURCE_CLAUDE:
        return parse_transcript(path)
    if source == SOURCE_CODEX:
        return parse_codex_transcript(
            path,
            session_id_hint=session_id_hint,
            cwd_hint=cwd_hint,
            model_hint=model_hint,
        )
    raise ValueError(f"unsupported source: {source}")


def convert_one(
    path: Path,
    vault_root: Path,
    st: State,
    dry_run: bool = False,
    *,
    source: str = SOURCE_CLAUDE,
    session_id_hint: str = "",
    cwd_hint: str = "",
    model_hint: str = "",
) -> Path | None:
    """1 本の JSONL をノートへ変換する。会話が無ければ None。"""
    path = Path(path)
    if not path.is_file():
        return None
    # mtime はパースの「前」に取る。後で取ると、パースとの隙間に追記された
    # ぶんを読んでいないのに新しい mtime を記録してしまい、以後スキップされて
    # その追記が永久に取り込まれない。先に取っておけば、取りこぼしても
    # 記録が古いままなので次回の backfill が拾い直す。
    mtime = path.stat().st_mtime
    session = parse_source_transcript(
        path,
        source,
        session_id_hint=session_id_hint,
        cwd_hint=cwd_hint,
        model_hint=model_hint,
    )
    if session is None:
        return None
    return write_note(vault_root, session, st, mtime, dry_run=dry_run)


def iter_transcripts(projects_root: Path, since_days: int | None) -> list[Path]:
    projects_root = Path(projects_root)
    if not projects_root.is_dir():
        return []
    cutoff = time.time() - since_days * 86400 if since_days else None
    found = [p for p in sorted(projects_root.glob("*/*.jsonl"))
             if cutoff is None or p.stat().st_mtime >= cutoff]
    return found


def iter_codex_transcripts(sessions_root: Path, since_days: int | None) -> list[Path]:
    """Codex の日付階層と archived_sessions の両方に使える探索。"""
    sessions_root = Path(sessions_root)
    if not sessions_root.is_dir():
        return []
    cutoff = time.time() - since_days * 86400 if since_days else None
    return [
        path
        for path in sorted(sessions_root.rglob("*.jsonl"))
        if cutoff is None or path.stat().st_mtime >= cutoff
    ]


def iter_source_transcripts(source: str, since_days: int | None) -> list[tuple[str, Path]]:
    """source と transcript path の組を、重複を除いて返す。"""
    jobs: list[tuple[str, Path]] = []
    if source in (SOURCE_CLAUDE, SOURCE_ALL):
        jobs.extend(
            (SOURCE_CLAUDE, path)
            for path in iter_transcripts(config.projects_dir(), since_days)
        )
    if source in (SOURCE_CODEX, SOURCE_ALL):
        seen: set[Path] = set()
        for root in (config.codex_sessions_dir(), config.codex_archived_sessions_dir()):
            for path in iter_codex_transcripts(root, since_days):
                identity = path.resolve()
                if identity not in seen:
                    seen.add(identity)
                    jobs.append((SOURCE_CODEX, path))
    return jobs


def cmd_hook(args) -> int:
    """SessionEnd hook 本体。何があっても 0 を返す。"""
    try:
        payload = json.load(sys.stdin)
        transcript = payload.get("transcript_path")
        if not transcript:
            return 0
        st = State(config.state_path())
        if convert_one(
            Path(transcript).expanduser(),
            config.vault_path(),
            st,
            source=args.source,
            session_id_hint=str(payload.get("session_id") or ""),
            cwd_hint=str(payload.get("cwd") or ""),
            model_hint=str(payload.get("model") or ""),
        ) is not None:
            st.save()
    except Exception:
        log_error("hook failed: " + traceback.format_exc().replace("\n", " | "))
    return 0


def cmd_backfill(args) -> int:
    st = State(config.state_path())
    since = None if args.all else args.since
    jobs = iter_source_transcripts(args.source, since)
    vault_root = config.vault_path()

    converted = skipped = failed = 0
    try:
        for source, path in jobs:
            try:
                # Stat once and keep the mtime.
                mtime = path.stat().st_mtime

                # Cheap pre-check: if filename is the session ID (normal case),
                # this skips without parsing. If not, it returns True and we proceed.
                # This is the fast path that avoids parsing when nothing changed.
                if not args.force:
                    if source == SOURCE_CLAUDE:
                        session_id = path.stem
                    else:
                        metadata = peek_codex_metadata(path)
                        if metadata is not None and metadata.is_subagent:
                            skipped += 1
                            continue
                        session_id = metadata.session_id if metadata is not None else ""

                    if session_id and not st.needs_update(
                        session_id,
                        mtime,
                        vault_root=vault_root,
                        source=source,
                    ):
                        skipped += 1
                        continue

                # Parse the transcript to get the authoritative session_id.
                session = parse_source_transcript(path, source)
                if session is None:
                    skipped += 1
                    continue

                # Confirm with the authoritative key before converting.
                if not args.force and not st.needs_update(
                    session.session_id,
                    mtime,
                    vault_root=vault_root,
                    source=session.source,
                ):
                    skipped += 1
                    continue

                # Write directly with the session we already have, avoiding re-parse.
                result = write_note(vault_root, session, st, mtime, dry_run=args.dry_run)
                if result is not None:
                    converted += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                log_error(f"backfill failed for {path}: {exc!r}")
                print(f"  失敗: {path} ({exc!r})", file=sys.stderr)
    finally:
        # KeyboardInterrupt (and other BaseException) skips the `except
        # Exception` above and unwinds past the loop. Persist whatever was
        # already converted so an interrupted run doesn't duplicate notes
        # on retry. --dry-run still writes nothing, interrupted or not.
        if not args.dry_run:
            st.save()

    label = "(dry-run) " if args.dry_run else ""
    print(f"{label}変換 {converted} / スキップ {skipped} / 失敗 {failed}"
          f"（対象 {len(jobs)} 本）")
    # 失敗を 0 で返すと、スクリプトから回したときに成功と区別できない。
    # hook と違い backfill は対話的な CLI なので、正直に非ゼロを返す。
    return 1 if failed else 0


def cmd_digest(args) -> int:
    print(build_digest(config.vault_path(), args.since), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cc2obsidian")
    sub = parser.add_subparsers(dest="command", required=True)

    hook = sub.add_parser("hook", help="SessionEnd hook から呼ばれる")
    hook.add_argument(
        "--source",
        choices=(SOURCE_CLAUDE, SOURCE_CODEX),
        default=SOURCE_CLAUDE,
        help="入力元ハーネス（既定: claude-code）",
    )
    hook.set_defaults(func=cmd_hook)

    backfill = sub.add_parser("backfill", help="既存の JSONL をまとめて変換する")
    group = backfill.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="全期間を対象にする")
    group.add_argument("--since", type=int, metavar="DAYS", default=30,
                       help="直近 N 日を対象にする（既定 30）")
    backfill.add_argument("--dry-run", action="store_true", help="書き込まずに件数だけ出す")
    backfill.add_argument("--force", action="store_true",
                          help="変換済みでも作り直す（converter を直したあとに使う）")
    backfill.add_argument(
        "--source",
        choices=(SOURCE_CLAUDE, SOURCE_CODEX, SOURCE_ALL),
        default=SOURCE_CLAUDE,
        help="入力元ハーネス（既定: claude-code）",
    )
    backfill.set_defaults(func=cmd_backfill)

    dg = sub.add_parser("digest", help="週次分析用のダイジェストを標準出力へ")
    dg.add_argument("--since", type=int, metavar="DAYS", default=7,
                    help="直近 N 日を対象にする（既定 7）")
    dg.set_defaults(func=cmd_digest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
