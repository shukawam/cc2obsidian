"""Session 中間表現を Obsidian 向け Markdown へ描画する。"""
import re

from .model import Session, ToolCall, Turn
from .slugs import customer_from_cwd

HEAD_LINES = 40
TAIL_LINES = 10
TRUNCATE_THRESHOLD = 60

_YAML_SPECIAL = set(':#[]{}&*!|>%@`"\'')


def truncate_output(text: str) -> str:
    """長いツール出力を先頭と末尾だけ残して切り詰める。"""
    lines = text.splitlines()
    if len(lines) <= TRUNCATE_THRESHOLD:
        return text
    dropped = len(lines) - HEAD_LINES - TAIL_LINES
    return "\n".join(
        lines[:HEAD_LINES] + [f"… {dropped} 行省略 …"] + lines[-TAIL_LINES:]
    )


def yaml_scalar(value: str) -> str:
    """YAML で誤読される文字を含むときだけ引用する。"""
    text = str(value)
    needs_quote = (
        not text
        or text[0] in _YAML_SPECIAL
        or any(ch in text for ch in (": ", " #"))
        or text.endswith(":")
    )
    if not needs_quote:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _sorted_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    """件数の降順、同数ならキー昇順。"""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _inline_map(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    body = ", ".join(f"{k}: {v}" for k, v in _sorted_counts(counts))
    return "{" + body + "}"


def render_frontmatter(session: Session) -> str:
    models = _sorted_counts(session.model_counts)
    primary = models[0][0] if models else "unknown"

    tags = ["claude-code/session", f"project/{session.project}"]
    customer = customer_from_cwd(session.cwd)
    if customer:
        tags.append(f"customer/{customer}")

    lines = [
        "---",
        f"date: {session.started_at:%Y-%m-%d}",
        f'time: "{session.started_at:%H:%M}"',
        f"project: {yaml_scalar(session.project)}",
        f"cwd: {yaml_scalar(session.cwd)}",
        f"session_id: {session.session_id}",
        f"title: {yaml_scalar(session.title)}",
        f"duration_min: {session.duration_min}",
        f"user_turns: {session.user_turns}",
        f"model: {primary}",
    ]
    if len(models) > 1:
        lines.append(f"models: {_inline_map(session.model_counts)}")
    lines.append(f"tool_counts: {_inline_map(session.tool_counts)}")
    lines.append(f"tags: [{', '.join(tags)}]")
    lines.append("---")
    return "\n".join(lines) + "\n"


_BACKTICKS = re.compile(r"`+")

ROLE_HEADINGS = {"user": "👤", "assistant": "🤖"}


def _fence_for(text: str) -> str:
    """本文に含まれるバッククォート連長より長いフェンスを返す。"""
    longest = max((len(m.group()) for m in _BACKTICKS.finditer(text)), default=0)
    return "`" * max(3, longest + 1)


def _code_block(text: str, lang: str = "") -> str:
    fence = _fence_for(text)
    return f"{fence}{lang}\n{text}\n{fence}"


def _render_tool_call(call: ToolCall) -> str:
    mark = "⚠️ " if call.is_error else ""
    summary = f"{mark}🔧 {call.tool_name} — {call.summary}"
    parts = [f"<details><summary>{summary}</summary>", ""]
    if call.input_text:
        lang = "bash" if call.tool_name == "Bash" else ""
        parts.append(_code_block(call.input_text, lang))
        parts.append("")
    result = truncate_output(call.result_text) if call.result_text else "(出力なし)"
    parts.append(_code_block(result))
    parts.append("")
    parts.append("</details>")
    return "\n".join(parts)


def _render_turn(turn: Turn) -> str | None:
    if not (turn.text.strip() or turn.thinking.strip() or turn.tool_calls):
        return None
    icon = ROLE_HEADINGS.get(turn.role, "•")
    parts = [f"## {icon} {turn.ts:%H:%M}", ""]
    if turn.text.strip():
        parts.append(turn.text.strip())
        parts.append("")
    if turn.thinking.strip():
        parts.append("<details><summary>💭 thinking</summary>")
        parts.append("")
        parts.append(turn.thinking.strip())
        parts.append("")
        parts.append("</details>")
        parts.append("")
    for call in turn.tool_calls:
        parts.append(_render_tool_call(call))
        parts.append("")
    rendered = "\n".join(parts).rstrip() + "\n"
    if turn.is_sidechain:
        return (f"<details><summary>🧵 サブエージェント {turn.ts:%H:%M}</summary>\n\n"
                f"{rendered}\n</details>\n")
    return rendered


def render_body(session: Session) -> str:
    blocks = [f"# {session.title}\n"]
    for turn in session.turns:
        rendered = _render_turn(turn)
        if rendered:
            blocks.append(rendered)
    return "\n".join(blocks)


def render_note(session: Session) -> str:
    note = render_frontmatter(session) + "\n" + render_body(session)
    return note if note.endswith("\n") else note + "\n"
