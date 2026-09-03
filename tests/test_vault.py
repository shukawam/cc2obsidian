import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc2obsidian import slugs, vault
from cc2obsidian.model import Session, Turn
from cc2obsidian.state import State

TS = slugs.to_jst("2026-08-22T23:01:00.000Z")  # JST 2026-08-23 08:01


def make_session(session_id="abc12345-0000", title="タイトル", source="claude-code"):
    return Session(
        session_id=session_id, cwd="/Users/x/work/demo", project="demo",
        title=title, started_at=TS, ended_at=TS,
        turns=[Turn("user", TS, "hi")],
        model_counts={"claude-opus-5": 1}, tool_counts={}, user_turns=1,
        source=source,
    )


class WriteNoteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.state = State(self.root / "state.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_creates_dated_directory_and_file(self):
        out = vault.write_note(self.root, make_session(), self.state, 100.0)
        self.assertTrue(out.exists())
        self.assertEqual(out.parent.name, "2026-08-23")
        self.assertIn("# タイトル", out.read_text(encoding="utf-8"))

    def test_records_state(self):
        vault.write_note(self.root, make_session(), self.state, 100.0)
        entry = self.state.get("abc12345-0000", vault_root=self.root)
        self.assertEqual(entry["source_mtime"], 100.0)

    def test_rewriting_same_session_overwrites_in_place(self):
        vault.write_note(self.root, make_session(), self.state, 100.0)
        vault.write_note(self.root, make_session(), self.state, 200.0)
        notes = list((self.root / "Notes" / "raw" / "2026-08-23").glob("*.md"))
        self.assertEqual(len(notes), 1)

    def test_changed_title_moves_the_note(self):
        first = vault.write_note(self.root, make_session(title="旧題"), self.state, 100.0)
        second = vault.write_note(self.root, make_session(title="新題"), self.state, 200.0)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertIn("新題", second.name)

    def test_collision_with_other_session_gets_short_id(self):
        vault.write_note(self.root, make_session(session_id="aaaaaaaa-1111"), self.state, 100.0)
        out = vault.write_note(self.root, make_session(session_id="bbbbbbbb-2222"), self.state, 100.0)
        self.assertTrue(out.name.endswith("-bbbbbbbb.md"))
        notes = list((self.root / "Notes" / "raw" / "2026-08-23").glob("*.md"))
        self.assertEqual(len(notes), 2)

    def test_same_session_id_from_different_sources_do_not_overwrite(self):
        claude = vault.write_note(
            self.root, make_session(source="claude-code"), self.state, 100.0
        )
        codex = vault.write_note(
            self.root, make_session(source="codex"), self.state, 100.0
        )

        self.assertNotEqual(claude, codex)
        self.assertTrue(claude.exists())
        self.assertTrue(codex.exists())
        self.assertIn("source: claude-code", claude.read_text(encoding="utf-8"))
        self.assertIn("source: codex", codex.read_text(encoding="utf-8"))
        self.assertEqual(
            self.state.get("abc12345-0000", source="codex", vault_root=self.root)["path"],
            str(codex.relative_to(self.root)),
        )

    def test_source_less_legacy_note_belongs_to_claude_not_codex(self):
        session = make_session(source="claude-code")
        original = vault.write_note(self.root, session, self.state, 100.0)
        legacy = original.read_text(encoding="utf-8").replace(
            "source: claude-code\n", ""
        )
        original.write_text(legacy, encoding="utf-8")

        lost_state = State(self.root / "lost-state.json")
        claude = vault.write_note(self.root, session, lost_state, 200.0)
        codex = vault.write_note(
            self.root, make_session(source="codex"), lost_state, 200.0
        )

        self.assertEqual(claude, original)
        self.assertNotEqual(codex, original)
        self.assertEqual(len(list(original.parent.glob("*.md"))), 2)

    def test_source_collision_does_not_delete_the_other_sources_note(self):
        claude = vault.write_note(
            self.root,
            make_session(title="Claudeのノート", source="claude-code"),
            self.state,
            100.0,
        )
        codex = make_session(title="Codexの旧題", source="codex")
        old_codex = vault.write_note(self.root, codex, self.state, 100.0)
        new_codex = vault.write_note(
            self.root,
            make_session(title="Codexの新題", source="codex"),
            self.state,
            200.0,
        )

        self.assertTrue(claude.exists())
        self.assertFalse(old_codex.exists())
        self.assertTrue(new_codex.exists())

    def test_recorded_note_is_rebuilt_even_if_its_frontmatter_is_gone(self):
        # state が「このパスは自分のノート」と記録している以上、中身が壊れて
        # 所有者を読めなくなっても、そこへ書き直す。読めない = 他人のもの、と
        # 扱うと --force がノートを直せず、隣に別名のノートが増えていく。
        session = make_session()
        note = vault.write_note(self.root, session, self.state, 100.0)
        note.write_text("手で壊した", encoding="utf-8")

        rebuilt = vault.write_note(self.root, session, self.state, 200.0)

        self.assertEqual(rebuilt, note)
        self.assertIn("session_id: abc12345-0000", note.read_text(encoding="utf-8"))
        self.assertEqual(len(list(note.parent.glob("*.md"))), 1)

    def test_stale_cross_vault_entry_does_not_clobber_unrelated_note(self):
        # session X was previously converted into a different Vault (vault_a).
        # Its state entry, if read without Vault awareness, would look like
        # "this session already lives at <relpath> in *this* Vault" and skip
        # the collision check entirely.
        vault_a = self.root / "vault_a"
        vault_b = self.root / "vault_b"
        session_x = make_session(session_id="xxxxxxxx-0000", title="タイトル")
        relpath = slugs.note_relpath(session_x.started_at, session_x.project,
                                      session_x.title, session_x.session_id)
        self.state.put("xxxxxxxx-0000", str(relpath), 100.0, vault_root=vault_a)

        # vault_b already holds an *unrelated* session's note at that same path.
        unrelated_path = vault_b / relpath
        unrelated_path.parent.mkdir(parents=True, exist_ok=True)
        unrelated_path.write_text("UNRELATED NOTE CONTENT", encoding="utf-8")

        out = vault.write_note(vault_b, session_x, self.state, 200.0)

        self.assertEqual(unrelated_path.read_text(encoding="utf-8"), "UNRELATED NOTE CONTENT")
        self.assertNotEqual(out, unrelated_path)
        self.assertTrue(out.name.endswith("-xxxxxxxx.md"))
        self.assertIn("タイトル", out.read_text(encoding="utf-8"))

    def test_own_note_recovered_via_frontmatter_when_state_is_lost(self):
        # State was lost (e.g. an OSError while reading it, or it simply
        # never got written). The Vault already holds this session's own
        # note. Without consulting the note's own session_id frontmatter,
        # the code can only see "some file is already there" and
        # disambiguates, permanently duplicating the note.
        session = make_session(session_id="abc12345-0000", title="タイトル")
        vault.write_note(self.root, session, self.state, 100.0)

        lost_state = State(self.root / "nonexistent-state.json")
        out = vault.write_note(self.root, session, lost_state, 200.0)

        notes = list((self.root / "Notes" / "raw" / "2026-08-23").glob("*.md"))
        self.assertEqual(len(notes), 1)
        self.assertFalse(out.name.endswith("-abc12345.md"))

    def test_does_not_delete_a_different_sessions_note_at_a_shared_state_key(self):
        # Simulate a state entry whose recorded path actually belongs to a
        # different session (e.g. a hand-edited entry, or the empty-
        # session_id shared-key case). write_note must verify ownership via
        # the target file's own frontmatter before unlinking it.
        unrelated = make_session(session_id="zzzzzzzz-9999", title="他人のノート")
        unrelated_path = vault.write_note(self.root, unrelated, self.state, 50.0)

        session = make_session(session_id="abc12345-0000", title="自分のノート")
        self.state.put(session.session_id, str(unrelated_path.relative_to(self.root)),
                        100.0, vault_root=self.root)

        vault.write_note(self.root, session, self.state, 200.0)

        self.assertTrue(unrelated_path.exists())
        self.assertIn("他人のノート", unrelated_path.read_text(encoding="utf-8"))

    def test_failed_write_preserves_old_note_on_title_change(self):
        first = vault.write_note(self.root, make_session(title="旧題"), self.state, 100.0)
        with mock.patch("cc2obsidian.vault._atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                vault.write_note(self.root, make_session(title="新題"), self.state, 200.0)
        self.assertTrue(first.exists())

    def test_failed_rewrite_preserves_the_previous_note_at_the_same_path(self):
        # 同じパスを更新する場合、write_text は既存ファイルをその場で切り詰める。
        # 途中で失敗すると旧ノートまで壊れる。一時ファイル + os.replace なら
        # 失敗しても元のノートが丸ごと残る。
        first = vault.write_note(self.root, make_session(), self.state, 100.0)
        before = first.read_text(encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                vault.write_note(self.root, make_session(), self.state, 200.0)
        self.assertEqual(first.read_text(encoding="utf-8"), before)
        self.assertEqual(list(first.parent.iterdir()), [first])

    def test_dry_run_renders_the_note(self):
        # README は dry-run を「変換に問題があれば hook 登録前に検出できる」
        # 手順として案内している。レンダリングを通さないとそれが成り立たない。
        with mock.patch("cc2obsidian.vault.render_note", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                vault.write_note(self.root, make_session(), self.state, 100.0, dry_run=True)

    def test_dry_run_writes_nothing(self):
        out = vault.write_note(self.root, make_session(), self.state, 100.0, dry_run=True)
        self.assertFalse(out.exists())
        self.assertIsNone(self.state.get("abc12345-0000"))


if __name__ == "__main__":
    unittest.main()
