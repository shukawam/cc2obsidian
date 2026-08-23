"""セッション JSONL を Session 中間表現へ変換する。"""
import json
from pathlib import Path

from .model import Session, ToolCall, Turn
from .slugs import project_from_cwd, to_jst

SYNTHETIC_MODEL = "<synthetic>"
TITLE_FALLBACK_LEN = 30

# 会話ではない運用系エントリ
SKIP_TYPES = frozenset({
    "attachment", "file-history-delta", "file-history-snapshot",
    "mode", "permission-mode", "queue-operation", "last-prompt", "system",
})


def _read_entries(path: Path) -> list[dict]:
    entries = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 書き込み途中の行などは読み飛ばす
    return entries


def _flatten_result(content) -> str:
    """tool_result.content は文字列にも配列にもなる。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "image":
            parts.append("[image]")
        elif btype == "tool_reference":
            parts.append("[tool_reference]")
    return "\n".join(parts)


def _format_input(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    if "command" in tool_input:
        return str(tool_input["command"])
    return json.dumps(tool_input, ensure_ascii=False, indent=2)


def _summary_for(name: str, tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return name
    for key in ("description", "file_path", "pattern", "skill", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return name


def _collect_results(entries: list[dict]) -> dict[str, tuple[str, bool]]:
    """tool_use_id -> (結果テキスト, エラーか) の対応表を作る。"""
    results = {}
    for e in entries:
        if e.get("type") != "user":
            continue
        content = e.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = (
                    _flatten_result(block.get("content")),
                    bool(block.get("is_error")),
                )
    return results


def _user_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def parse_transcript(path: Path) -> Session | None:
    entries = _read_entries(path)
    if not entries:
        return None

    results = _collect_results(entries)
    turns: list[Turn] = []
    model_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    user_turns = 0
    title = ""
    session_id = ""
    cwd = ""

    for e in entries:
        etype = e.get("type")

        if etype == "ai-title":
            title = e.get("aiTitle") or title
            continue
        if etype in SKIP_TYPES:
            continue
        if etype not in ("user", "assistant"):
            continue

        session_id = session_id or e.get("sessionId", "")
        cwd = cwd or e.get("cwd", "")
        # サブエージェントの発言は会話としては残すが、集計からは外す
        is_side = bool(e.get("isSidechain"))

        ts = to_jst(e["timestamp"])
        message = e.get("message", {})

        if etype == "user":
            if e.get("isMeta"):
                continue
            text = _user_text(message.get("content"))
            if not text.strip():
                continue  # tool_result だけの user エントリ
            if not is_side:
                user_turns += 1
            turns.append(Turn(role="user", ts=ts, text=text, is_sidechain=is_side))
            continue

        model = message.get("model")
        if model and model != SYNTHETIC_MODEL and not is_side:
            model_counts[model] = model_counts.get(model, 0) + 1

        texts, thoughts, calls = [], [], []
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text", ""))
            elif btype == "thinking":
                thoughts.append(block.get("thinking", ""))
            elif btype == "tool_use":
                name = block.get("name", "unknown")
                if not is_side:
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                tool_input = block.get("input", {})
                result_text, is_error = results.get(block.get("id"), ("", False))
                calls.append(ToolCall(
                    tool_name=name,
                    summary=_summary_for(name, tool_input),
                    input_text=_format_input(tool_input),
                    result_text=result_text,
                    is_error=is_error,
                ))

        turns.append(Turn(
            role="assistant", ts=ts,
            text="\n".join(t for t in texts if t),
            thinking="\n".join(t for t in thoughts if t),
            tool_calls=calls,
            is_sidechain=is_side,
        ))

    if not turns:
        return None

    if not title:
        first_user = next((t.text for t in turns if t.role == "user"), "")
        title = first_user.strip()[:TITLE_FALLBACK_LEN] or "untitled"

    return Session(
        session_id=session_id,
        cwd=cwd,
        project=project_from_cwd(cwd) if cwd else "unknown",
        title=title,
        started_at=turns[0].ts,
        ended_at=turns[-1].ts,
        turns=turns,
        model_counts=model_counts,
        tool_counts=tool_counts,
        user_turns=user_turns,
    )
