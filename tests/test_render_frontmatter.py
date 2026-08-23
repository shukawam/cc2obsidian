import unittest
from datetime import datetime

from cc2obsidian import render, slugs
from cc2obsidian.model import Session, Turn


def make_session(**kw):
    started = slugs.to_jst("2026-08-22T23:01:00.000Z")
    ended = slugs.to_jst("2026-08-22T23:43:00.000Z")
    defaults = dict(
        session_id="472a17cb-1f3b-488d-b335-0f7bdf7de956",
        cwd="/Users/x/work/demo",
        project="demo",
        title="スキル作成相談",
        started_at=started,
        ended_at=ended,
        turns=[Turn(role="user", ts=started, text="hi")],
        model_counts={"claude-opus-5": 45},
        tool_counts={"Bash": 6},
        user_turns=5,
    )
    defaults.update(kw)
    return Session(**defaults)


class TruncateTest(unittest.TestCase):
    def test_short_output_is_unchanged(self):
        text = "\n".join(f"line{i}" for i in range(10))
        self.assertEqual(render.truncate_output(text), text)

    def test_exactly_at_threshold_is_unchanged(self):
        text = "\n".join(f"line{i}" for i in range(60))
        self.assertEqual(render.truncate_output(text), text)

    def test_long_output_keeps_head_and_tail(self):
        text = "\n".join(f"line{i}" for i in range(200))
        got = render.truncate_output(text).splitlines()
        self.assertEqual(got[0], "line0")
        self.assertEqual(got[39], "line39")
        self.assertEqual(got[-1], "line199")
        self.assertEqual(got[-10], "line190")

    def test_long_output_states_how_many_lines_were_dropped(self):
        text = "\n".join(f"line{i}" for i in range(200))
        self.assertIn("150 行省略", render.truncate_output(text))

    def test_empty_text(self):
        self.assertEqual(render.truncate_output(""), "")


class YamlScalarTest(unittest.TestCase):
    def test_plain_text_is_bare(self):
        self.assertEqual(render.yaml_scalar("スキル作成相談"), "スキル作成相談")

    def test_colon_forces_quoting(self):
        self.assertEqual(render.yaml_scalar("a: b"), '"a: b"')

    def test_quotes_are_escaped(self):
        self.assertEqual(render.yaml_scalar('say "hi": now'), '"say \\"hi\\": now"')

    def test_leading_hash_forces_quoting(self):
        self.assertEqual(render.yaml_scalar("#tag"), '"#tag"')


class FrontmatterTest(unittest.TestCase):
    def test_contains_core_fields(self):
        fm = render.render_frontmatter(make_session())
        self.assertIn("date: 2026-08-23", fm)
        self.assertIn('time: "08:01"', fm)
        self.assertIn("project: demo", fm)
        self.assertIn("session_id: 472a17cb-1f3b-488d-b335-0f7bdf7de956", fm)
        self.assertIn("duration_min: 42", fm)
        self.assertIn("user_turns: 5", fm)

    def test_starts_and_ends_with_delimiters(self):
        fm = render.render_frontmatter(make_session())
        self.assertTrue(fm.startswith("---\n"))
        self.assertTrue(fm.rstrip().endswith("---"))

    def test_single_model_omits_models_map(self):
        fm = render.render_frontmatter(make_session(model_counts={"claude-opus-5": 45}))
        self.assertIn("model: claude-opus-5", fm)
        self.assertNotIn("models:", fm)

    def test_multiple_models_emit_map_sorted_by_count(self):
        fm = render.render_frontmatter(make_session(
            model_counts={"claude-sonnet-5": 4, "claude-opus-5": 45}))
        self.assertIn("model: claude-opus-5", fm)
        self.assertIn("models: {claude-opus-5: 45, claude-sonnet-5: 4}", fm)

    def test_no_models_emits_unknown(self):
        fm = render.render_frontmatter(make_session(model_counts={}))
        self.assertIn("model: unknown", fm)

    def test_tool_counts_sorted_by_count(self):
        fm = render.render_frontmatter(make_session(tool_counts={"Read": 2, "Bash": 6}))
        self.assertIn("tool_counts: {Bash: 6, Read: 2}", fm)

    def test_empty_tool_counts_emits_empty_map(self):
        fm = render.render_frontmatter(make_session(tool_counts={}))
        self.assertIn("tool_counts: {}", fm)

    def test_tags_include_session_and_project(self):
        fm = render.render_frontmatter(make_session())
        self.assertIn("tags: [claude-code/session, project/demo]", fm)

    def test_customer_path_adds_customer_tag(self):
        fm = render.render_frontmatter(make_session(cwd="/Users/x/customer/mizuho/dify", project="dify"))
        self.assertIn("customer/mizuho", fm)

    def test_title_with_colon_is_quoted(self):
        fm = render.render_frontmatter(make_session(title="Kong: 設計"))
        self.assertIn('title: "Kong: 設計"', fm)


if __name__ == "__main__":
    unittest.main()
