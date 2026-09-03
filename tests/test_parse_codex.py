import json
import tempfile
import unittest
from pathlib import Path

from cc2obsidian import parse_codex


def write_text(text):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    with tmp:
        tmp.write(text)
    return Path(tmp.name)


def write_jsonl(entries):
    return write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def session_meta(ts="2026-08-31T04:39:59.811Z", **payload):
    base = {
        "session_id": "01a0561d-f807-7ad2-aafb-406151f5d1e5",
        "id": "01a0561d-f807-7ad2-aafb-406151f5d1e5",
        "timestamp": ts,
        "cwd": "/Users/x/work/demo",
        "originator": "codex-tui",
        "cli_version": "0.151.0",
        "source": "cli",
    }
    return {"timestamp": ts, "type": "session_meta", "payload": {**base, **payload}}


def response_item(payload, ts="2026-08-31T04:40:00.000Z"):
    return {"timestamp": ts, "type": "response_item", "payload": payload}


def user_msg(text, ts="2026-08-31T04:40:00.000Z"):
    content = text if isinstance(text, list) else [{"type": "input_text", "text": text}]
    return response_item({"type": "message", "role": "user", "content": content}, ts)


def assistant_msg(text, ts="2026-08-31T04:41:00.000Z", phase="final_answer"):
    return response_item({"type": "message", "role": "assistant", "phase": phase,
                          "content": [{"type": "output_text", "text": text}]}, ts)


class PeekMetadataTest(unittest.TestCase):
    def test_reads_identity_from_the_first_session_meta(self):
        meta = parse_codex.peek_codex_metadata(write_jsonl([session_meta()]))
        self.assertEqual(meta.session_id, "01a0561d-f807-7ad2-aafb-406151f5d1e5")
        self.assertEqual(meta.cwd, "/Users/x/work/demo")
        self.assertEqual(meta.cli_version, "0.151.0")
        self.assertFalse(meta.is_subagent)

    def test_subagent_rollout_is_flagged_by_its_own_meta(self):
        # subagent の rollout は「自分の meta」「親の meta」の順に 2 本持つ。
        # 2 本目に引きずられて subagent 判定を落とさないこと。
        path = write_jsonl([
            session_meta(id="01a05632-a0f6-7851-886f-39e156d8e153",
                         source={"subagent": {"thread_spawn": {"depth": 1}}}),
            session_meta(),
        ])
        meta = parse_codex.peek_codex_metadata(path)
        self.assertTrue(meta.is_subagent)
        self.assertEqual(meta.session_id, "01a05632-a0f6-7851-886f-39e156d8e153")

    def test_legacy_rollout_without_session_id_uses_the_thread_id(self):
        payload = {"id": "0199e0e5-b0ad-7ff1-8714-168f3e75b13c",
                   "timestamp": "2025-10-14T04:06:13.421Z",
                   "cwd": "/Users/x/work/legacy", "originator": "codex_vscode",
                   "cli_version": "0.45.0-alpha.5", "instructions": None, "source": "vscode"}
        path = write_jsonl([{"timestamp": "2025-10-14T04:06:13.493Z",
                             "type": "session_meta", "payload": payload}])
        meta = parse_codex.peek_codex_metadata(path)
        self.assertEqual(meta.session_id, "0199e0e5-b0ad-7ff1-8714-168f3e75b13c")
        self.assertEqual(meta.cli_version, "0.45.0-alpha.5")

    def test_returns_none_without_a_session_meta(self):
        path = write_jsonl([{"type": "event_msg", "payload": {"type": "token_count"}}])
        self.assertIsNone(parse_codex.peek_codex_metadata(path))

    def test_broken_lines_do_not_hide_a_later_session_meta(self):
        path = write_text("{ not json\n" + json.dumps(session_meta()) + "\n")
        self.assertIsNotNone(parse_codex.peek_codex_metadata(path))


class MessageTest(unittest.TestCase):
    def test_extracts_user_and_assistant_text(self):
        path = write_jsonl([session_meta(), user_msg("こんにちは"), assistant_msg("どうも")])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])
        self.assertEqual(s.turns[0].text, "こんにちは")
        self.assertEqual(s.turns[1].text, "どうも")
        self.assertEqual(s.user_turns, 1)

    def test_session_identity_comes_from_the_session_meta(self):
        path = write_jsonl([session_meta(), user_msg("やあ"), assistant_msg("はい")])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.source, "codex")
        self.assertEqual(s.source_version, "0.151.0")
        self.assertEqual(s.session_id, "01a0561d-f807-7ad2-aafb-406151f5d1e5")
        self.assertEqual(s.cwd, "/Users/x/work/demo")
        self.assertEqual(s.project, "demo")

    def test_title_falls_back_to_the_first_user_message(self):
        path = write_jsonl([session_meta(), user_msg("Codex 対応を引き継いで"), assistant_msg("承知")])
        self.assertEqual(parse_codex.parse_codex_transcript(path).title, "Codex 対応を引き継いで")

    def test_assistant_phase_is_kept_on_the_turn(self):
        path = write_jsonl([
            session_meta(),
            user_msg("お願い"),
            assistant_msg("調べます", phase="commentary"),
            assistant_msg("結果です", phase="final_answer"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.phase for t in s.turns[1:]], ["commentary", "final_answer"])

    def test_developer_instructions_never_reach_the_note(self):
        path = write_jsonl([
            session_meta(),
            response_item({"type": "message", "role": "developer",
                           "content": [{"type": "input_text", "text": "<skills_instructions>秘密"}]}),
            user_msg("本題"),
            assistant_msg("はい"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])
        self.assertNotIn("秘密", "".join(t.text for t in s.turns))

    def test_injected_context_blocks_are_dropped_from_user_messages(self):
        path = write_jsonl([
            session_meta(),
            user_msg([
                {"type": "input_text", "text": "<recommended_plugins>\nBox\n</recommended_plugins>"},
                {"type": "input_text", "text": "<environment_context>\n  <cwd>/x</cwd>\n</environment_context>"},
            ]),
            user_msg([
                {"type": "input_text", "text": "<environment_context>\n  <cwd>/x</cwd>\n</environment_context>"},
                {"type": "input_text", "text": "実際の依頼"},
            ]),
            assistant_msg("はい"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])
        self.assertEqual(s.turns[0].text, "実際の依頼")
        self.assertEqual(s.user_turns, 1)

    def test_agents_md_injection_is_not_a_user_turn(self):
        path = write_jsonl([
            session_meta(),
            user_msg("# AGENTS.md instructions for /Users/x/work/demo\n\n<INSTRUCTIONS>..."),
            user_msg("本当の発話"),
            assistant_msg("はい"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.user_turns, 1)
        self.assertEqual(s.turns[0].text, "本当の発話")

    def test_images_become_placeholders_without_their_payload(self):
        path = write_jsonl([
            session_meta(),
            user_msg([
                {"type": "input_text", "text": "<image name=[Image #1]>"},
                {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo"},
                {"type": "input_text", "text": "</image>"},
                {"type": "input_text", "text": "[Image #1] これを読んで"},
            ]),
            assistant_msg("はい"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.turns[0].text, "[image image/png]\n[Image #1] これを読んで")
        self.assertNotIn("iVBORw0KGgo", s.turns[0].text)

    def test_attachment_only_message_is_still_a_turn(self):
        path = write_jsonl([
            session_meta(),
            user_msg([{"type": "input_image", "image_url": "data:image/jpeg;base64,AAA"}]),
            assistant_msg("受け取りました"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.turns[0].text, "[image image/jpeg]")

    def test_subagent_rollout_is_not_converted(self):
        path = write_jsonl([
            session_meta(id="01a05632-a0f6", source={"subagent": {"thread_spawn": {"depth": 1}}}),
            session_meta(),
            user_msg("サブエージェントへの指示"),
            assistant_msg("やりました"),
        ])
        self.assertIsNone(parse_codex.parse_codex_transcript(path))

    def test_returns_none_without_any_conversation(self):
        path = write_jsonl([session_meta(),
                            {"type": "event_msg", "payload": {"type": "token_count"}}])
        self.assertIsNone(parse_codex.parse_codex_transcript(path))

    def test_hook_metadata_fills_in_a_missing_session_meta(self):
        path = write_jsonl([user_msg("やあ"), assistant_msg("はい")])
        s = parse_codex.parse_codex_transcript(
            path, session_id_hint="hook-session", cwd_hint="/Users/x/work/hinted")
        self.assertEqual(s.session_id, "hook-session")
        self.assertEqual(s.project, "hinted")

    def test_without_any_identity_the_session_is_dropped(self):
        path = write_jsonl([user_msg("やあ"), assistant_msg("はい")])
        self.assertIsNone(parse_codex.parse_codex_transcript(path))


class ThinkingTest(unittest.TestCase):
    def test_reasoning_summary_becomes_thinking(self):
        path = write_jsonl([
            session_meta(),
            user_msg("お願い"),
            response_item({"type": "reasoning",
                           "summary": [{"type": "summary_text", "text": "**方針を決める**"}],
                           "content": None, "encrypted_content": "gAAAAABsecret"}),
            assistant_msg("やります"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.turns[1].thinking, "**方針を決める**")
        self.assertEqual(s.turns[1].text, "やります")

    def test_encrypted_reasoning_never_reaches_the_note(self):
        path = write_jsonl([
            session_meta(),
            user_msg("お願い"),
            response_item({"type": "reasoning", "summary": [],
                           "content": None, "encrypted_content": "gAAAAABsecret"}),
            assistant_msg("やります"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertNotIn("gAAAAAB", "".join(t.thinking + t.text for t in s.turns))

    def test_inter_agent_messages_are_not_conversation(self):
        path = write_jsonl([
            session_meta(),
            user_msg("お願い"),
            response_item({"type": "agent_message", "author": "/root/worker",
                           "recipient": "/root",
                           "content": [{"type": "input_text", "text": "Message Type: MESSAGE"},
                                       {"type": "encrypted_content",
                                        "encrypted_content": "gAAAAABsecret"}]}),
            assistant_msg("やります"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])
        self.assertNotIn("Message Type", "".join(t.text for t in s.turns))


class ToolCallTest(unittest.TestCase):
    def test_function_call_and_output_become_one_tool_call(self):
        path = write_jsonl([
            session_meta(),
            user_msg("状態を見て"),
            response_item({"type": "function_call", "name": "shell", "call_id": "c1",
                           "arguments": json.dumps({"command": ["bash", "-lc", "git status"],
                                                    "workdir": "/x"})}),
            response_item({"type": "function_call_output", "call_id": "c1",
                           "output": json.dumps({"output": "clean\n",
                                                 "metadata": {"exit_code": 0}})}),
            assistant_msg("きれいです"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        call = s.turns[1].tool_calls[0]
        self.assertEqual(call.tool_name, "shell")
        self.assertIn("git status", call.summary)
        self.assertEqual(call.result_text, "clean\n")
        self.assertFalse(call.is_error)
        self.assertEqual(s.tool_counts, {"shell": 1})

    def test_nonzero_exit_code_marks_the_call_as_an_error(self):
        path = write_jsonl([
            session_meta(),
            user_msg("試して"),
            response_item({"type": "function_call", "name": "shell", "call_id": "c1",
                           "arguments": json.dumps({"command": ["false"]})}),
            response_item({"type": "function_call_output", "call_id": "c1",
                           "output": json.dumps({"output": "boom",
                                                 "metadata": {"exit_code": 1}})}),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertTrue(s.turns[1].tool_calls[0].is_error)

    def test_custom_tool_call_keeps_its_raw_input(self):
        path = write_jsonl([
            session_meta(),
            user_msg("直して"),
            response_item({"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2",
                           "status": "completed",
                           "input": "*** Begin Patch\n*** Update File: a.py\n"}),
            response_item({"type": "custom_tool_call_output", "call_id": "c2",
                           "output": json.dumps({"output": "Success.",
                                                 "metadata": {"exit_code": 0}})}),
        ])
        call = parse_codex.parse_codex_transcript(path).turns[1].tool_calls[0]
        self.assertEqual(call.tool_name, "apply_patch")
        self.assertIn("*** Begin Patch", call.input_text)
        self.assertEqual(call.result_text, "Success.")

    def test_block_list_output_is_flattened(self):
        path = write_jsonl([
            session_meta(),
            user_msg("実行して"),
            response_item({"type": "custom_tool_call", "name": "exec", "call_id": "c3",
                           "status": "completed", "input": "tools.exec_command({cmd:'ls'})"}),
            response_item({"type": "custom_tool_call_output", "call_id": "c3", "output": [
                {"type": "input_text", "text": "Script completed"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAA"},
                {"type": "input_text", "text": "a.py"},
            ]}),
        ])
        call = parse_codex.parse_codex_transcript(path).turns[1].tool_calls[0]
        self.assertEqual(call.result_text, "Script completed\n[image image/png]\na.py")

    def test_plain_string_output_is_used_as_is(self):
        path = write_jsonl([
            session_meta(),
            user_msg("計画して"),
            response_item({"type": "function_call", "name": "update_plan", "call_id": "c4",
                           "arguments": json.dumps({"plan": [{"step": "調べる"}]})}),
            response_item({"type": "function_call_output", "call_id": "c4",
                           "output": "Plan updated"}),
        ])
        call = parse_codex.parse_codex_transcript(path).turns[1].tool_calls[0]
        self.assertEqual(call.result_text, "Plan updated")

    def test_encrypted_tool_arguments_are_redacted(self):
        blob = "gAAAAAB" + "x" * 60
        path = write_jsonl([
            session_meta(),
            user_msg("投げて"),
            response_item({"type": "function_call", "name": "send_message", "call_id": "c5",
                           "arguments": json.dumps({"target": "worker", "message": blob})}),
            response_item({"type": "function_call_output", "call_id": "c5", "output": "ok"}),
        ])
        call = parse_codex.parse_codex_transcript(path).turns[1].tool_calls[0]
        self.assertNotIn(blob, call.input_text)
        self.assertIn("[encrypted]", call.input_text)

    def test_web_search_is_recorded_with_its_query(self):
        path = write_jsonl([
            session_meta(),
            user_msg("調べて"),
            response_item({"type": "web_search_call", "status": "completed",
                           "action": {"type": "search", "query": "codex hooks"}}),
        ])
        s = parse_codex.parse_codex_transcript(path)
        call = s.turns[1].tool_calls[0]
        self.assertEqual(call.tool_name, "web_search")
        self.assertEqual(call.summary, "codex hooks")
        self.assertEqual(s.tool_counts, {"web_search": 1})

    def test_output_without_a_matching_call_is_ignored(self):
        path = write_jsonl([
            session_meta(),
            user_msg("やあ"),
            response_item({"type": "function_call_output", "call_id": "orphan",
                           "output": "迷子"}),
            assistant_msg("はい"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.tool_counts, {})
        self.assertNotIn("迷子", "".join(t.text for t in s.turns))


class TurnGroupingTest(unittest.TestCase):
    def test_reasoning_starts_a_new_assistant_turn_after_tool_calls(self):
        path = write_jsonl([
            session_meta(),
            user_msg("お願い", ts="2026-08-31T04:40:00.000Z"),
            response_item({"type": "reasoning",
                           "summary": [{"type": "summary_text", "text": "一段目"}]},
                          ts="2026-08-31T04:40:10.000Z"),
            assistant_msg("調べます", ts="2026-08-31T04:40:20.000Z", phase="commentary"),
            response_item({"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                           "status": "completed", "input": "ls"},
                          ts="2026-08-31T04:40:30.000Z"),
            response_item({"type": "custom_tool_call_output", "call_id": "c1", "output": "a.py"},
                          ts="2026-08-31T04:40:31.000Z"),
            response_item({"type": "reasoning",
                           "summary": [{"type": "summary_text", "text": "二段目"}]},
                          ts="2026-08-31T04:40:40.000Z"),
            response_item({"type": "custom_tool_call", "name": "exec", "call_id": "c2",
                           "status": "completed", "input": "cat a.py"},
                          ts="2026-08-31T04:40:50.000Z"),
            response_item({"type": "custom_tool_call_output", "call_id": "c2", "output": "x = 1"},
                          ts="2026-08-31T04:40:51.000Z"),
            assistant_msg("できました", ts="2026-08-31T04:41:00.000Z"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.role for t in s.turns],
                         ["user", "assistant", "assistant", "assistant"])
        self.assertEqual([t.thinking for t in s.turns[1:]], ["一段目", "二段目", ""])
        self.assertEqual([t.text for t in s.turns[1:]], ["調べます", "", "できました"])
        self.assertEqual([len(t.tool_calls) for t in s.turns[1:]], [1, 1, 0])
        self.assertEqual(s.tool_counts, {"exec": 2})

    def test_turn_timestamp_is_the_first_item_of_that_turn(self):
        path = write_jsonl([
            session_meta(),
            user_msg("お願い", ts="2026-08-31T04:40:00.000Z"),
            response_item({"type": "reasoning",
                           "summary": [{"type": "summary_text", "text": "考える"}]},
                          ts="2026-08-31T04:40:10.000Z"),
            assistant_msg("はい", ts="2026-08-31T04:45:00.000Z"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.turns[1].ts.strftime("%H:%M"), "13:40")


class AggregationTest(unittest.TestCase):
    def test_model_counts_come_from_the_turn_context(self):
        path = write_jsonl([
            session_meta(),
            {"timestamp": "2026-08-31T04:39:59.900Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol", "cwd": "/Users/x/work/demo"}},
            user_msg("やあ"),
            assistant_msg("はい"),
            {"timestamp": "2026-08-31T04:42:00.000Z", "type": "turn_context",
             "payload": {"model": "gpt-5-codex", "cwd": "/Users/x/work/demo"}},
            user_msg("もう一度", ts="2026-08-31T04:42:10.000Z"),
            assistant_msg("はい", ts="2026-08-31T04:42:20.000Z"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.model_counts, {"gpt-5.6-sol": 1, "gpt-5-codex": 1})

    def test_model_hint_is_used_when_the_rollout_has_no_turn_context(self):
        path = write_jsonl([session_meta(), user_msg("やあ"), assistant_msg("はい")])
        s = parse_codex.parse_codex_transcript(path, model_hint="gpt-5.6")
        self.assertEqual(s.model_counts, {"gpt-5.6": 1})

    def test_duplicate_response_items_are_counted_once(self):
        item = response_item({"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                              "id": "item-1", "status": "completed", "input": "ls"})
        path = write_jsonl([session_meta(), user_msg("やあ"), item, item,
                            response_item({"type": "custom_tool_call_output",
                                           "call_id": "c1", "output": "a.py"})])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual(s.tool_counts, {"exec": 1})

    def test_unknown_entry_types_do_not_drop_the_session(self):
        path = write_jsonl([
            session_meta(),
            {"timestamp": "2026-08-31T04:40:00.000Z", "type": "world_state",
             "payload": {"anything": "新形式"}},
            user_msg("やあ"),
            {"timestamp": "2026-08-31T04:40:30.000Z", "type": "brand_new_type"},
            assistant_msg("はい"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])

    def test_an_entry_without_a_timestamp_is_skipped(self):
        path = write_jsonl([
            session_meta(),
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text",
                                                               "text": "時刻なし"}]}},
            user_msg("時刻あり"),
            assistant_msg("はい"),
        ])
        s = parse_codex.parse_codex_transcript(path)
        self.assertEqual([t.text for t in s.turns], ["時刻あり", "はい"])


if __name__ == "__main__":
    unittest.main()
