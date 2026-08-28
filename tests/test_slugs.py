import unittest
from datetime import datetime
from pathlib import Path

from cc2obsidian import slugs


class ToJstTest(unittest.TestCase):
    def test_converts_utc_iso_to_jst(self):
        got = slugs.to_jst("2026-08-23T08:01:35.036Z")
        self.assertEqual(got.year, 2026)
        self.assertEqual(got.month, 8)
        self.assertEqual(got.day, 23)
        self.assertEqual(got.hour, 17)
        self.assertEqual(got.minute, 1)

    def test_date_rolls_over_into_next_jst_day(self):
        got = slugs.to_jst("2026-08-22T15:30:00.000Z")
        self.assertEqual((got.month, got.day, got.hour), (8, 23, 0))

    def test_accepts_offset_form(self):
        got = slugs.to_jst("2026-08-23T08:01:35+00:00")
        self.assertEqual(got.hour, 17)


class SlugifyTest(unittest.TestCase):
    def test_keeps_japanese_text(self):
        self.assertEqual(slugs.slugify("スキル作成相談"), "スキル作成相談")

    def test_replaces_path_hostile_characters(self):
        self.assertEqual(slugs.slugify("a/b:c*d?e"), "a-b-c-d-e")

    def test_collapses_whitespace_into_single_hyphen(self):
        self.assertEqual(slugs.slugify("hello   world"), "hello-world")

    def test_truncates_to_max_len(self):
        self.assertEqual(len(slugs.slugify("x" * 100, max_len=40)), 40)

    def test_strips_leading_and_trailing_hyphens(self):
        self.assertEqual(slugs.slugify("  --hi--  "), "hi")

    def test_empty_input_yields_untitled(self):
        self.assertEqual(slugs.slugify("   "), "untitled")


class CwdTest(unittest.TestCase):
    def test_project_is_last_path_segment(self):
        self.assertEqual(slugs.project_from_cwd("/Users/x/work/konnect-demo"), "konnect-demo")

    def test_project_handles_trailing_slash(self):
        self.assertEqual(slugs.project_from_cwd("/Users/x/work/"), "work")

    def test_customer_extracted_from_customer_dir(self):
        self.assertEqual(slugs.customer_from_cwd("/Users/x/customer/mizuho/dify"), "mizuho")

    def test_customer_is_none_outside_customer_dir(self):
        self.assertIsNone(slugs.customer_from_cwd("/Users/x/work/foo"))


class NoteRelpathTest(unittest.TestCase):
    def setUp(self):
        self.started = slugs.to_jst("2026-08-22T23:01:00.000Z")  # JST 2026-08-23 08:01

    def test_builds_dated_directory_and_filename(self):
        got = slugs.note_relpath(self.started, "work", "スキル作成相談", "472a17cb-1f3b")
        self.assertEqual(got, Path("Notes/raw/2026-08-23/0801-work-スキル作成相談.md"))

    def test_disambiguate_appends_short_session_id(self):
        got = slugs.note_relpath(self.started, "work", "スキル作成相談", "472a17cb-1f3b", disambiguate=True)
        self.assertEqual(got, Path("Notes/raw/2026-08-23/0801-work-スキル作成相談-472a17cb.md"))


if __name__ == "__main__":
    unittest.main()
