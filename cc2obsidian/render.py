"""Session 中間表現を Obsidian 向け Markdown へ描画する。"""
import json
import re

from .model import Session, ToolCall, Turn
from .slugs import customer_from_cwd

HEAD_LINES = 40
TAIL_LINES = 10
TRUNCATE_THRESHOLD = 60
# 行数だけでは足りない。実ログには 60 行以下で 1 万文字超の tool result や、
# 87,831 文字の tool input が実在する。1 行が極端に長い出力を素通しさせない。
MAX_CHARS = 20_000

_YAML_SPECIAL = set(':#[]{}&*!|>%@`"\'')
# 引用しないと文字列以外の型として読まれてしまう平文スカラー。
_FLOW_SPECIAL = set(",[]{}")
_YAML_TYPED = re.compile(
    r"\A(?:true|false|yes|no|on|off|null|~"
    r"|[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"|\d{4}-\d{2}-\d{2}.*)\Z",
    re.IGNORECASE,
)


def truncate_output(text: str, max_chars: int = MAX_CHARS) -> str:
    """長いツール出力を先頭と末尾だけ残して切り詰める。

    行数と文字数の両方で抑える。行数だけだと、1 行が数十万文字ある出力が
    そのままノートに載ってしまう。
    """
    lines = text.splitlines()
    if len(lines) > TRUNCATE_THRESHOLD:
        dropped = len(lines) - HEAD_LINES - TAIL_LINES
        text = "\n".join(
            lines[:HEAD_LINES] + [f"… {dropped} 行省略 …"] + lines[-TAIL_LINES:]
        )
    if len(text) <= max_chars:
        return text
    head = max_chars * 4 // 5
    tail = max_chars - head
    dropped = len(text) - head - tail
    return f"{text[:head]}\n… {dropped} 文字省略 …\n{text[-tail:]}"


def yaml_scalar(value: str) -> str:
    """YAML で誤読される値だけを引用する。

    引用形は json.dumps に任せる。改行・タブ・引用符・バックスラッシュを
    まとめて正しくエスケープでき、結果は YAML の double-quoted スカラーと
    しても妥当なので、外部依存なしで安全側に倒せる。
    """
    text = str(value)
    needs_quote = (
        not text
        or text != text.strip()              # 前後の空白は引用しないと消える
        or text[0] in _YAML_SPECIAL
        or text[0] == "-"                    # リスト項目として読まれる
        or any(ch in text for ch in (": ", " #"))
        or text.endswith(":")
        or any(ch in text for ch in "\n\r\t")
        or _YAML_TYPED.match(text)           # bool / 数値 / 日付に化ける
    )
    if not needs_quote:
        return text
    return json.dumps(text, ensure_ascii=False)


def yaml_flow_scalar(value: str) -> str:
    """フロー表記（tags: [a, b] や {k: v}）の中に置くスカラー。

    ',' '[' ']' '{' '}' はフローの中でだけ区切りとして効く。ブロック
    スカラーの規則（yaml_scalar）では引用されないので、ここで足す。
    """
    text = str(value)
    if any(ch in _FLOW_SPECIAL for ch in text):
        return json.dumps(text, ensure_ascii=False)
    return yaml_scalar(text)


def _sorted_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    """件数の降順、同数ならキー昇順。"""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _inline_map(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    body = ", ".join(f"{yaml_flow_scalar(k)}: {v}" for k, v in _sorted_counts(counts))
    return "{" + body + "}"


def render_frontmatter(session: Session) -> str:
    models = _sorted_counts(session.model_counts)
    primary = models[0][0] if models else "unknown"

    tags = [f"{session.source}/session", f"project/{session.project}"]
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
        f"source: {yaml_scalar(session.source)}",
        f"title: {yaml_scalar(session.title)}",
        f"duration_min: {session.duration_min}",
        f"user_turns: {session.user_turns}",
        f"model: {primary}",
    ]
    if session.source_version:
        lines.append(f"source_version: {yaml_scalar(session.source_version)}")
    if len(models) > 1:
        lines.append(f"models: {_inline_map(session.model_counts)}")
    lines.append(f"tool_counts: {_inline_map(session.tool_counts)}")
    lines.append(f"tags: [{', '.join(yaml_flow_scalar(t) for t in tags)}]")
    lines.append("---")
    return "\n".join(lines) + "\n"


_BACKTICKS = re.compile(r"`+")

ROLE_HEADINGS = {"user": "👤", "assistant": "🤖"}

# _render_turn が実際に出す見出し行の形式: "## <icon> HH:MM"。
# digest.py はターンの切れ目をこの形式そのものから判定する（"## " で始まる
# だけの緩い判定だと、ユーザーが貼り付けた本文中の Markdown 見出しを
# ターン境界と誤認してしまう）。
_HEADING_TIME = r"\d\d:\d\d"


def heading_regex(icon: str | None = None) -> str:
    """見出し行にマッチする正規表現文字列を返す。

    icon を渡すとその役割（例: ROLE_HEADINGS["user"]）の見出しだけに、
    省略すると _render_turn が出すどの役割の見出しにもマッチする。
    """
    icons = [icon] if icon is not None else list(ROLE_HEADINGS.values())
    alt = "|".join(re.escape(i) for i in icons)
    return rf"^## ({alt}) {_HEADING_TIME}\s*$"


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
        # 入力も切り詰める。実ログには 87,831 文字の tool input がある。
        parts.append(_code_block(truncate_output(call.input_text), lang))
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
