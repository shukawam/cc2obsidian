import json
import tempfile
import unittest
from pathlib import Path

from cc2obsidian import parse


def write_jsonl(entries):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for e in entries:
        tmp.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.close()
    return Path(tmp.name)


BASE = {"sessionId": "abc12345-0000", "cwd": "/Users/x/work/demo", "isSidechain": False}


def user_entry(text, ts="2026-08-22T23:01:00.000Z", **kw):
    return {**BASE, "type": "user", "timestamp": ts,
            "message": {"role": "user", "content": text}, **kw}


def assistant_entry(content, ts="2026-08-22T23:02:00.000Z", model="claude-opus-5", **kw):
    return {**BASE, "type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": model, "content": content}, **kw}


class ParseTest(unittest.TestCase):
    def test_returns_none_when_no_real_turns(self):
        p = write_jsonl([{"type": "mode", "mode": "default", "sessionId": "abc"}])
        self.assertIsNone(parse.parse_transcript(p))

    def test_extracts_user_and_assistant_text(self):
        p = write_jsonl([
            user_entry("こんにちは"),
            assistant_entry([{"type": "text", "text": "どうも"}]),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])
        self.assertEqual(s.turns[0].text, "こんにちは")
        self.assertEqual(s.turns[1].text, "どうも")

    def test_user_turns_counts_only_real_user_input(self):
        p = write_jsonl([
            user_entry("質問"),
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash",
                              "input": {"command": "ls", "description": "list"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]),
            user_entry("Caveat: system note", isMeta=True),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.user_turns, 1)

    def test_meta_user_entries_are_dropped(self):
        p = write_jsonl([
            user_entry("本題"),
            user_entry("システム注入", isMeta=True),
            assistant_entry([{"type": "text", "text": "ok"}]),
        ])
        s = parse.parse_transcript(p)
        self.assertNotIn("システム注入", "".join(t.text for t in s.turns))

    def test_tool_result_is_attached_to_its_tool_use(self):
        p = write_jsonl([
            user_entry("やって"),
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash",
                              "input": {"command": "ls -la", "description": "一覧"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1", "content": "file.txt"}]),
        ])
        s = parse.parse_transcript(p)
        call = s.turns[1].tool_calls[0]
        self.assertEqual(call.tool_name, "Bash")
        self.assertEqual(call.summary, "一覧")
        self.assertIn("ls -la", call.input_text)
        self.assertEqual(call.result_text, "file.txt")
        self.assertFalse(call.is_error)

    def test_tool_result_array_content_is_flattened(self):
        p = write_jsonl([
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/a"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1",
                         "content": [{"type": "text", "text": "line1"},
                                     {"type": "image"},
                                     {"type": "text", "text": "line2"}]}]),
        ])
        s = parse.parse_transcript(p)
        call = s.turns[0].tool_calls[0]
        self.assertIn("line1", call.result_text)
        self.assertIn("line2", call.result_text)
        self.assertIn("[image]", call.result_text)

    def test_tool_result_error_flag(self):
        p = write_jsonl([
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "false"}}]),
            user_entry([{"type": "tool_result", "tool_use_id": "t1", "content": "boom", "is_error": True}]),
        ])
        s = parse.parse_transcript(p)
        self.assertTrue(s.turns[0].tool_calls[0].is_error)

    def test_thinking_is_captured(self):
        p = write_jsonl([
            assistant_entry([{"type": "thinking", "thinking": "考え中"},
                             {"type": "text", "text": "答え"}]),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.turns[0].thinking, "考え中")
        self.assertEqual(s.turns[0].text, "答え")

    def test_model_counts_exclude_synthetic(self):
        p = write_jsonl([
            assistant_entry([{"type": "text", "text": "a"}], model="claude-opus-5"),
            assistant_entry([{"type": "text", "text": "b"}], model="claude-sonnet-5"),
            assistant_entry([{"type": "text", "text": "c"}], model="claude-opus-5"),
            assistant_entry([{"type": "text", "text": "d"}], model="<synthetic>"),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.model_counts, {"claude-opus-5": 2, "claude-sonnet-5": 1})

    def test_sidechain_entries_are_excluded_from_counts(self):
        side = assistant_entry(
            [{"type": "text", "text": "sub"},
             {"type": "tool_use", "id": "s1", "name": "Grep", "input": {"pattern": "x"}}],
            model="claude-haiku-4-5-20251001")
        side["isSidechain"] = True
        p = write_jsonl([assistant_entry([{"type": "text", "text": "main"}]), side])
        s = parse.parse_transcript(p)
        self.assertNotIn("claude-haiku-4-5-20251001", s.model_counts)
        self.assertNotIn("Grep", s.tool_counts)

    def test_sidechain_turns_are_kept_in_the_conversation(self):
        side = assistant_entry([{"type": "text", "text": "sub"}])
        side["isSidechain"] = True
        p = write_jsonl([assistant_entry([{"type": "text", "text": "main"}]), side])
        s = parse.parse_transcript(p)
        self.assertEqual(len(s.turns), 2)
        self.assertFalse(s.turns[0].is_sidechain)
        self.assertTrue(s.turns[1].is_sidechain)

    def test_sidechain_user_turns_are_not_counted(self):
        side = user_entry("サブへの指示")
        side["isSidechain"] = True
        p = write_jsonl([user_entry("本題"), side])
        s = parse.parse_transcript(p)
        self.assertEqual(s.user_turns, 1)

    def test_tool_counts_are_tallied(self):
        p = write_jsonl([
            assistant_entry([{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                             {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pwd"}}]),
            assistant_entry([{"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "/a"}}]),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.tool_counts, {"Bash": 2, "Read": 1})

    def test_title_comes_from_last_ai_title(self):
        p = write_jsonl([
            user_entry("何か"),
            {"type": "ai-title", "aiTitle": "古い題", "sessionId": "abc12345-0000"},
            {"type": "ai-title", "aiTitle": "新しい題", "sessionId": "abc12345-0000"},
        ])
        self.assertEqual(parse.parse_transcript(p).title, "新しい題")

    def test_title_falls_back_to_first_user_text(self):
        p = write_jsonl([user_entry("スキルを作りたいので相談させて欲しい。" + "あ" * 50)])
        s = parse.parse_transcript(p)
        self.assertEqual(len(s.title), 30)
        self.assertTrue(s.title.startswith("スキルを作りたい"))

    def test_session_metadata(self):
        p = write_jsonl([
            user_entry("start", ts="2026-08-22T23:00:00.000Z"),
            assistant_entry([{"type": "text", "text": "end"}], ts="2026-08-22T23:42:00.000Z"),
        ])
        s = parse.parse_transcript(p)
        self.assertEqual(s.session_id, "abc12345-0000")
        self.assertEqual(s.project, "demo")
        self.assertEqual(s.cwd, "/Users/x/work/demo")
        self.assertEqual(s.started_at.hour, 8)
        self.assertEqual(s.duration_min, 42)

    def test_malformed_lines_are_skipped(self):
        p = write_jsonl([user_entry("ok")])
        with p.open("a", encoding="utf-8") as fh:
            fh.write("{ this is not json\n")
        s = parse.parse_transcript(p)
        self.assertEqual(s.turns[0].text, "ok")


if __name__ == "__main__":
    unittest.main()
