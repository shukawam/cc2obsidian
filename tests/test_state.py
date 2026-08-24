import json
import tempfile
import unittest
from pathlib import Path

from cc2obsidian.state import State


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "state.json"

    def tearDown(self):
        self.dir.cleanup()

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
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name != "state.json"]
        self.assertEqual(leftovers, [])

    def test_different_vault_needs_update_even_with_unchanged_mtime(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0, vault_root="/vault/a")
        self.assertTrue(st.needs_update("s1", 100.0, vault_root="/vault/b"))

    def test_same_vault_and_unchanged_mtime_needs_no_update(self):
        st = State(self.path)
        st.put("s1", "x.md", 100.0, vault_root="/vault/a")
        self.assertFalse(st.needs_update("s1", 100.0, vault_root="/vault/a"))

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


if __name__ == "__main__":
    unittest.main()
