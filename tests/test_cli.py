import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cc2obsidian import cli
from cc2obsidian.state import State

ENTRY_USER = {
    "type": "user", "sessionId": "abc12345-0000", "cwd": "/Users/x/work/demo",
    "isSidechain": False, "timestamp": "2026-08-22T23:01:00.000Z",
    "message": {"role": "user", "content": "こんにちは"},
}
ENTRY_ASSISTANT = {
    "type": "assistant", "sessionId": "abc12345-0000", "cwd": "/Users/x/work/demo",
    "isSidechain": False, "timestamp": "2026-08-22T23:05:00.000Z",
    "message": {"role": "assistant", "model": "claude-opus-5",
                "content": [{"type": "text", "text": "どうも"}]},
}


def write_transcript(directory: Path, name="abc12345-0000.jsonl", entries=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    entries = entries or [ENTRY_USER, ENTRY_ASSISTANT]
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    return path


class ConvertOneTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.vault = self.root / "vault"
        self.state = State(self.root / "state.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_writes_a_note(self):
        src = write_transcript(self.root / "projects" / "demo")
        out = cli.convert_one(src, self.vault, self.state)
        self.assertTrue(out.exists())
        self.assertIn("こんにちは", out.read_text(encoding="utf-8"))

    def test_returns_none_for_transcript_without_turns(self):
        src = write_transcript(self.root / "projects" / "demo", "empty.jsonl",
                               [{"type": "mode", "mode": "default", "sessionId": "x"}])
        self.assertIsNone(cli.convert_one(src, self.vault, self.state))

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(cli.convert_one(self.root / "nope.jsonl", self.vault, self.state))


class IterTranscriptsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_finds_transcripts_in_every_project(self):
        write_transcript(self.root / "proj-a", "a.jsonl")
        write_transcript(self.root / "proj-b", "b.jsonl")
        self.assertEqual(len(cli.iter_transcripts(self.root, None)), 2)

    def test_since_filters_by_mtime(self):
        old = write_transcript(self.root / "proj-a", "old.jsonl")
        write_transcript(self.root / "proj-b", "new.jsonl")
        ancient = time.time() - 60 * 60 * 24 * 90
        import os
        os.utime(old, (ancient, ancient))
        got = cli.iter_transcripts(self.root, since_days=30)
        self.assertEqual([p.name for p in got], ["new.jsonl"])

    def test_missing_root_returns_empty(self):
        self.assertEqual(cli.iter_transcripts(self.root / "nope", None), [])


class HookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.vault = self.root / "vault"
        self.patches = [
            mock.patch("cc2obsidian.cli.config.vault_path", return_value=self.vault),
            mock.patch("cc2obsidian.cli.config.state_path", return_value=self.root / "state.json"),
            mock.patch("cc2obsidian.cli.config.log_path", return_value=self.root / "cc2obsidian.log"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def _run_hook(self, payload):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            return cli.main(["hook"])

    def test_converts_the_transcript_named_on_stdin(self):
        src = write_transcript(self.root / "projects" / "demo")
        self.assertEqual(self._run_hook({"transcript_path": str(src)}), 0)
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

    def test_exits_zero_on_malformed_stdin(self):
        with mock.patch("sys.stdin", io.StringIO("not json")):
            self.assertEqual(cli.main(["hook"]), 0)

    def test_exits_zero_when_transcript_is_missing(self):
        self.assertEqual(self._run_hook({"transcript_path": "/nope/x.jsonl"}), 0)

    def test_exits_zero_and_logs_when_conversion_raises(self):
        src = write_transcript(self.root / "projects" / "demo")
        with mock.patch("cc2obsidian.cli.convert_one", side_effect=RuntimeError("boom")):
            self.assertEqual(self._run_hook({"transcript_path": str(src)}), 0)
        self.assertIn("boom", (self.root / "cc2obsidian.log").read_text(encoding="utf-8"))

    def test_exits_zero_when_logging_itself_fails(self):
        with mock.patch("cc2obsidian.cli.config.log_path", side_effect=RuntimeError("log broken")):
            with mock.patch("sys.stdin", io.StringIO("not json")):
                self.assertEqual(cli.main(["hook"]), 0)


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.vault = self.root / "vault"
        self.projects = self.root / "projects"
        self.patches = [
            mock.patch("cc2obsidian.cli.config.vault_path", return_value=self.vault),
            mock.patch("cc2obsidian.cli.config.state_path", return_value=self.root / "state.json"),
            mock.patch("cc2obsidian.cli.config.log_path", return_value=self.root / "cc2obsidian.log"),
            mock.patch("cc2obsidian.cli.config.projects_dir", return_value=self.projects),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def test_converts_every_transcript(self):
        write_transcript(self.projects / "proj-a", "a.jsonl")
        self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

    def test_dry_run_writes_nothing(self):
        write_transcript(self.projects / "proj-a", "a.jsonl")
        self.assertEqual(cli.main(["backfill", "--all", "--dry-run"]), 0)
        self.assertFalse((self.vault / "Notes").exists())

    def test_second_run_skips_unchanged_transcripts(self):
        write_transcript(self.projects / "proj-a", "a.jsonl")
        cli.main(["backfill", "--all"])
        note = next((self.vault / "Notes").rglob("*.md"))
        before = note.stat().st_mtime_ns
        cli.main(["backfill", "--all"])
        self.assertEqual(note.stat().st_mtime_ns, before)

    def test_touched_transcript_is_reconverted(self):
        src = write_transcript(self.projects / "proj-a", "a.jsonl")
        cli.main(["backfill", "--all"])
        import os
        future = time.time() + 10
        os.utime(src, (future, future))
        cli.main(["backfill", "--all"])
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

    def test_one_bad_transcript_does_not_abort_the_run(self):
        (self.projects / "proj-bad").mkdir(parents=True)
        (self.projects / "proj-bad" / "bad.jsonl").write_text("{oops\n", encoding="utf-8")
        write_transcript(self.projects / "proj-good", "good.jsonl")
        self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

    def test_backfill_parses_each_transcript_once(self):
        write_transcript(self.projects / "proj-a", "a.jsonl")
        write_transcript(self.projects / "proj-b", "b.jsonl")
        parse_count = 0
        original_parse = cli.parse_transcript

        def counting_parse(path):
            nonlocal parse_count
            parse_count += 1
            return original_parse(path)

        with mock.patch("cc2obsidian.cli.parse_transcript", side_effect=counting_parse):
            self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(parse_count, 2)

    def test_second_vault_still_receives_notes_after_first_vault_backfill(self):
        # Regression test for the production incident: a backfill into a
        # scratch Vault must not poison the shared state file so that a
        # later backfill into the real Vault silently skips everything.
        write_transcript(self.projects / "proj-a", "a.jsonl")
        self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(len(list((self.vault / "Notes").rglob("*.md"))), 1)

        other_vault = self.root / "vault2"
        with mock.patch("cc2obsidian.cli.config.vault_path", return_value=other_vault):
            self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(len(list((other_vault / "Notes").rglob("*.md"))), 1)

    def test_backfill_does_not_parse_unchanged_transcripts(self):
        # Use default filename "abc12345-0000.jsonl" which matches the session_id,
        # so the cheap pre-check with path.stem will work and skip without parsing.
        write_transcript(self.projects / "proj-a")
        cli.main(["backfill", "--all"])
        parse_count = 0
        original_parse = cli.parse_transcript

        def counting_parse(path):
            nonlocal parse_count
            parse_count += 1
            return original_parse(path)

        with mock.patch("cc2obsidian.cli.parse_transcript", side_effect=counting_parse):
            self.assertEqual(cli.main(["backfill", "--all"]), 0)
        self.assertEqual(parse_count, 0)


if __name__ == "__main__":
    unittest.main()
