import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc2obsidian import state
from cc2obsidian.state import State


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "state.json"
        self.vault = Path(self.dir.name) / "vault"

    def tearDown(self):
        self.dir.cleanup()

    def _note(self, relpath: str) -> Path:
        """Vault に実体のノートを置く。needs_update は実体の有無も見る。"""
        note = self.vault / relpath
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("x", encoding="utf-8")
        return note

    def test_missing_file_starts_empty(self):
        self.assertIsNone(State(self.path).get("nope"))

    def test_corrupt_file_starts_empty(self):
        self.path.write_text("{ broken", encoding="utf-8")
        self.assertIsNone(State(self.path).get("nope"))

    def test_oserror_reading_state_propagates(self):
        # A directory where a file is expected: read_text() raises OSError
        # (IsADirectoryError). Unlike malformed JSON, this must not be
        # silently swallowed into an empty state — that would make save()
        # clobber a state file we merely failed to *read*.
        self.path.mkdir()
        with self.assertRaises(OSError):
            State(self.path)

    def test_put_then_get_roundtrips_through_disk(self):
        st = State(self.path)
        st.put("s1", "Notes/2026-08-23/a.md", 123.0)
        st.save()
        self.assertEqual(State(self.path).get("s1")["path"], "Notes/2026-08-23/a.md")

    def test_save_creates_parent_directory(self):
        nested = Path(self.dir.name) / "deep" / "state.json"
        st = State(nested)
        st.put("s1", "x.md", 1.0)
        st.save()
        self.assertTrue(nested.exists())

    def test_unknown_session_needs_update(self):
        self.assertTrue(State(self.path).needs_update("s1", 100.0))

    def test_unchanged_mtime_needs_no_update(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0)
        self.assertFalse(st.needs_update("s1", 100.0))

    def test_newer_mtime_needs_update(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0)
        self.assertTrue(st.needs_update("s1", 101.0))

    def test_save_is_atomic_leaving_no_tmp_file(self):
        st = State(self.path)
        st.put("s1", "x.md", 1.0)
        st.save()
        leftovers = [p.name for p in self.path.parent.iterdir()
                     if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    @unittest.skipIf(state.fcntl is None, "fcntl is not available on this platform")
    def test_save_holds_a_dedicated_file_lock(self):
        st = State(self.path)
        st.put("s1", "x.md", 1.0)
        with mock.patch.object(state.fcntl, "flock") as flock:
            st.save()

        flags = [call.args[1] for call in flock.call_args_list]
        self.assertEqual(flags, [state.fcntl.LOCK_EX, state.fcntl.LOCK_UN])
        self.assertTrue(Path(str(self.path) + ".lock").is_file())

    def test_different_vault_needs_update_even_with_unchanged_mtime(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0, vault_root="/vault/a")
        self.assertTrue(st.needs_update("s1", 100.0, vault_root="/vault/b"))

    def test_same_vault_and_unchanged_mtime_needs_no_update(self):
        self._note("x.md")
        st = State(self.path)
        st.put("s1", "x.md", 100.0, vault_root=self.vault)
        self.assertFalse(st.needs_update("s1", 100.0, vault_root=self.vault))

    def test_legacy_entry_without_vault_needs_update(self):
        st = State(self.path)
        st._data["s1"] = {"path": "x.md", "source_mtime": 100.0}  # no vault key, as an older version wrote it
        self.assertTrue(st.needs_update("s1", 100.0, vault_root="/vault/a"))

    def test_get_returns_entry_for_its_own_vault(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0, vault_root="/vault/a")
        self.assertEqual(st.get("s1", vault_root="/vault/a")["path"], "x.md")

    def test_get_returns_none_for_a_different_vault(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0, vault_root="/vault/a")
        self.assertIsNone(st.get("s1", vault_root="/vault/b"))

    def test_same_session_id_is_namespaced_by_source(self):
        st = State(self.path)
        st.put("same", "claude.md", 1.0)
        st.put("same", "codex.md", 2.0, source="codex")

        self.assertEqual(st.get("same")["path"], "claude.md")
        self.assertEqual(st.get("same", source="codex")["path"], "codex.md")
        self.assertFalse(st.needs_update("same", 2.0, source="codex"))

    def test_put_writes_a_namespaced_key(self):
        st = State(self.path)
        st.put("s1", "x.md", 1.0)
        st.save()
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("claude-code:s1", stored)
        self.assertNotIn("s1", stored)

    def test_legacy_unprefixed_key_is_visible_only_to_claude(self):
        self.path.write_text(json.dumps({
            "same": {"path": "legacy.md", "source_mtime": 1.0, "vault": None}
        }), encoding="utf-8")
        st = State(self.path)

        self.assertEqual(st.get("same")["path"], "legacy.md")
        self.assertIsNone(st.get("same", source="codex"))
        self.assertTrue(st.needs_update("same", 1.0, source="codex"))

    def test_save_merges_a_concurrent_writers_entries(self):
        # Two State instances both load the (empty) file. One writes s2 and
        # saves first (e.g. the hook, mid-session). The other -- which never
        # saw s2 -- then writes s1 and saves. Without a merge, the second
        # save would dump only {s1}, permanently losing s2.
        a = State(self.path)
        b = State(self.path)
        a.put("s1", "a.md", 1.0)
        b.put("s2", "b.md", 2.0)

        b.save()
        a.save()

        merged = State(self.path)
        self.assertEqual(merged.get("s1")["path"], "a.md")
        self.assertEqual(merged.get("s2")["path"], "b.md")
    def test_save_does_not_revert_a_key_another_writer_updated(self):
        # A と B が同じ内容をロードする。B が X を新しい値へ更新して保存。
        # そのあと A が「X には触れず」Y だけ足して保存する。A が持っている
        # X はロード時点の古い値なので、self._data を丸ごと上書きに使うと
        # B の更新が巻き戻る。書き戻してよいのは自分が触ったキーだけ。
        seed = State(self.path)
        seed.put("X", "old.md", 1.0)
        seed.save()

        a = State(self.path)
        b = State(self.path)
        b.put("X", "new.md", 2.0)
        b.save()
        a.put("Y", "y.md", 3.0)
        a.save()

        merged = State(self.path)
        self.assertEqual(merged.get("X")["path"], "new.md")
        self.assertEqual(merged.get("Y")["path"], "y.md")
    def test_needs_update_when_the_recorded_note_is_gone(self):
        # ノートを消したら作り直せるべき。state だけが残っていると
        # 「変換済み」と判定され、二度と復元されない。
        st = State(self.path)
        st.put("s1", "Notes/2026-08-23/x.md", 100.0, vault_root=self.vault)
        self.assertTrue(st.needs_update("s1", 100.0, vault_root=self.vault))

        self._note("Notes/2026-08-23/x.md")
        self.assertFalse(st.needs_update("s1", 100.0, vault_root=self.vault))


if __name__ == "__main__":
    unittest.main()
