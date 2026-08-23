import tempfile
import time
import unittest
from pathlib import Path

from cc2obsidian import digest

NOTE = """---
date: 2026-08-23
time: "08:01"
project: demo
session_id: abc12345-0000
title: スキル作成相談
duration_min: 42
user_turns: 2
model: claude-opus-5
tool_counts: {Bash: 6}
tags: [claude-code/session, project/demo]
---

# スキル作成相談

## 👤 08:01
最初の質問です

<details><summary>💭 thinking</summary>

内心の声

</details>

## 🤖 08:02
回答します

<details><summary>🔧 Bash — 一覧</summary>

```bash
ls
```

</details>

## 👤 08:10
二つ目の質問です
"""


class FrontmatterTest(unittest.TestCase):
    def test_reads_key_values(self):
        fm = digest.parse_frontmatter(NOTE)
        self.assertEqual(fm["date"], "2026-08-23")
        self.assertEqual(fm["project"], "demo")
        self.assertEqual(fm["model"], "claude-opus-5")

    def test_strips_quotes(self):
        self.assertEqual(digest.parse_frontmatter(NOTE)["time"], "08:01")

    def test_missing_frontmatter_yields_empty(self):
        self.assertEqual(digest.parse_frontmatter("# just a heading"), {})


class UserTurnsTest(unittest.TestCase):
    def test_extracts_every_user_turn(self):
        turns = digest.extract_user_turns(NOTE)
        self.assertEqual(turns, ["最初の質問です", "二つ目の質問です"])

    def test_ignores_assistant_and_folded_blocks(self):
        joined = "\n".join(digest.extract_user_turns(NOTE))
        self.assertNotIn("回答します", joined)
        self.assertNotIn("内心の声", joined)
        self.assertNotIn("ls", joined)


class BuildDigestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.notes = self.root / "Notes" / "2026-08-23"
        self.notes.mkdir(parents=True)

    def tearDown(self):
        self.dir.cleanup()

    def test_includes_metadata_and_user_turns(self):
        (self.notes / "0801-demo-スキル作成相談.md").write_text(NOTE, encoding="utf-8")
        out = digest.build_digest(self.root, since_days=3650)
        self.assertIn("スキル作成相談", out)
        self.assertIn("claude-opus-5", out)
        self.assertIn("最初の質問です", out)
        self.assertNotIn("回答します", out)

    def test_note_links_use_wikilink_form(self):
        (self.notes / "0801-demo-スキル作成相談.md").write_text(NOTE, encoding="utf-8")
        self.assertIn("[[0801-demo-スキル作成相談]]", digest.build_digest(self.root, 3650))

    def test_old_notes_are_excluded(self):
        old = self.notes / "0801-demo-古い.md"
        old.write_text(NOTE, encoding="utf-8")
        import os
        ancient = time.time() - 86400 * 90
        os.utime(old, (ancient, ancient))
        self.assertNotIn("古い", digest.build_digest(self.root, since_days=7))

    def test_weekly_notes_are_not_included(self):
        weekly = self.root / "Notes" / "weekly"
        weekly.mkdir(parents=True)
        (weekly / "2026-W34.md").write_text(NOTE, encoding="utf-8")
        self.assertNotIn("2026-W34", digest.build_digest(self.root, 3650))

    def test_empty_vault_says_so(self):
        self.assertIn("対象なし", digest.build_digest(self.root, since_days=7))

    def test_unreadable_note_is_skipped_without_aborting(self):
        (self.notes / "0801-demo-スキル作成相談.md").write_text(NOTE, encoding="utf-8")
        (self.notes / "0900-demo-壊れた.md").write_bytes(b"---\ndate: 2026-08-23\n---\n\xff\xfe invalid")
        out = digest.build_digest(self.root, since_days=3650)
        self.assertIn("最初の質問です", out)
        self.assertIn("読み取れなかったノート: 1 件", out)
        self.assertIn("1 セッション", out)
        self.assertNotIn("壊れた", out)


if __name__ == "__main__":
    unittest.main()
