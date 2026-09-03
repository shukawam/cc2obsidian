import unittest

from cc2obsidian import render, slugs
from cc2obsidian.model import Session, ToolCall, Turn

TS = slugs.to_jst("2026-08-22T23:01:00.000Z")  # JST 08:01


def make_session(turns, **kw):
    defaults = dict(
        session_id="abc12345-0000", cwd="/Users/x/work/demo", project="demo",
        title="タイトル", started_at=TS, ended_at=TS, turns=turns,
        model_counts={"claude-opus-5": 1}, tool_counts={}, user_turns=1,
    )
    defaults.update(kw)
    return Session(**defaults)


class BodyTest(unittest.TestCase):
    def test_title_heading_comes_first(self):
        body = render.render_body(make_session([Turn("user", TS, "hi")]))
        self.assertTrue(body.startswith("# タイトル\n"))

    def test_user_and_assistant_headings(self):
        body = render.render_body(make_session([
            Turn("user", TS, "質問"),
            Turn("assistant", TS, "回答"),
        ]))
        self.assertIn("## 👤 08:01", body)
        self.assertIn("## 🤖 08:01", body)
        self.assertIn("質問", body)
        self.assertIn("回答", body)

    def test_thinking_is_folded_into_details(self):
        body = render.render_body(make_session([Turn("assistant", TS, "答", thinking="考")]))
        self.assertIn("<details><summary>💭 thinking</summary>", body)
        self.assertIn("考", body)
        self.assertIn("</details>", body)

    def test_no_thinking_block_when_absent(self):
        body = render.render_body(make_session([Turn("assistant", TS, "答")]))
        self.assertNotIn("💭 thinking", body)

    def test_tool_call_is_folded_with_name_and_summary(self):
        call = ToolCall("Bash", "一覧を見る", "ls -la", "file.txt")
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("<details><summary>🔧 Bash — 一覧を見る</summary>", body)
        self.assertIn("ls -la", body)
        self.assertIn("file.txt", body)

    def test_errored_tool_call_is_marked(self):
        call = ToolCall("Bash", "落ちる", "false", "boom", is_error=True)
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("⚠️", body)

    def test_long_tool_result_is_truncated(self):
        long_out = "\n".join(f"line{i}" for i in range(300))
        call = ToolCall("Bash", "多い", "cmd", long_out)
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("行省略", body)
        self.assertNotIn("line150", body)

    def test_backticks_in_output_get_longer_fence(self):
        call = ToolCall("Bash", "fence", "cmd", "```\ninner\n```")
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("````", body)

    def test_empty_result_is_labelled(self):
        call = ToolCall("Bash", "無音", "cmd", "")
        body = render.render_body(make_session([Turn("assistant", TS, "", tool_calls=[call])]))
        self.assertIn("(出力なし)", body)

    def test_sidechain_turn_is_folded_into_details(self):
        turn = Turn("assistant", TS, "サブの答え", is_sidechain=True)
        body = render.render_body(make_session([turn]))
        self.assertIn("🧵 サブエージェント 08:01", body)
        self.assertIn("サブの答え", body)

    def test_turn_with_no_content_is_skipped(self):
        body = render.render_body(make_session([
            Turn("assistant", TS, ""),
            Turn("user", TS, "あり"),
        ]))
        self.assertEqual(body.count("## 🤖"), 0)

    def test_turn_phase_is_optional_metadata(self):
        turn = Turn("assistant", TS, "途中経過", phase="commentary")
        body = render.render_body(make_session([turn]))
        self.assertEqual(turn.phase, "commentary")
        self.assertIn("途中経過", body)


class NoteTest(unittest.TestCase):
    def test_note_is_frontmatter_then_body(self):
        note = render.render_note(make_session([Turn("user", TS, "hi")]))
        self.assertTrue(note.startswith("---\n"))
        self.assertIn("\n---\n\n# タイトル\n", note)
        self.assertTrue(note.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
