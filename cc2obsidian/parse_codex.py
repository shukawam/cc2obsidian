"""Codex の rollout JSONL を Session 中間表現へ変換する。

Codex は rollout の形式を安定インターフェースとして保証していない。そのため
このモジュールは Claude 用の parse.py と混ぜず、「会話として明示的に許可した
ものだけを採る」ホワイトリスト方式で書く。未知の entry は読み飛ばし、
developer/system 指示や自動注入されたコンテキストはノートへ出さない。
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .model import Session, ToolCall, Turn
from .slugs import project_from_cwd, to_jst

SOURCE = "codex"
TITLE_FALLBACK_LEN = 30

# 会話として本文に採るのは user と assistant だけ。developer には skills /
# permissions / collaboration mode などの内部指示が入るため落とす。
CONVERSATION_ROLES = frozenset({"user", "assistant"})

# ユーザーの発話に見えるが、実際はハーネスが自動で差し込むブロック。
# 発話単位ではなく content ブロック単位で落とす（本物の依頼と同じ message に
# 同居するため、message ごと捨てると発話そのものが消える）。
INJECTED_PREFIXES = (
    "<environment_context>",
    "<recommended_plugins>",
    "<user_instructions>",
    "# AGENTS.md instructions for ",
)

# 添付画像を囲む <image name=...> / </image> のラッパー。中身は input_image
# として別ブロックに来るので、ラッパー自体は本文に残さない。
_IMAGE_WRAPPER = re.compile(r"\A</?image\b[^>]*>\Z")
_DATA_URL_MEDIA = re.compile(r"\Adata:([\w.+-]+/[\w.+-]+)[;,]")

# サブエージェント間のメッセージなどに現れる暗号化ペイロード。復号できない
# 巨大な文字列をノートに残しても読めないので、印だけ残して落とす。
_ENCRYPTED = re.compile(r"gAAAAA[A-Za-z0-9_=-]{20,}")

SUMMARY_LEN = 80

# details の summary 行に出したい、ツール引数の代表的なキー。
_SUMMARY_KEYS = ("command", "cmd", "query", "file_path", "path", "target",
                 "task_name", "name", "message")


@dataclass(frozen=True)
class CodexMetadata:
    """rollout を開かずに済ませたいときに使う、先頭の session_meta の要約。"""

    session_id: str
    cwd: str
    cli_version: str | None
    is_subagent: bool


def _iter_entries(path: Path):
    """1 行ずつ読む。壊れた行はそこで打ち切らず読み飛ばす。"""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # 書き込み途中の行などは読み飛ばす
            if isinstance(entry, dict):
                yield entry


def _is_subagent_source(source) -> bool:
    """source は "cli" のような文字列か、subagent を含む dict になる。"""
    return isinstance(source, dict) and "subagent" in source


def _metadata_from(payload: dict) -> CodexMetadata:
    # rollout ファイルに固有なのは thread の id。0.144 より前は session_id が
    # 無く、subagent の rollout では session_id が親を指すため id を優先する。
    session_id = payload.get("id") or payload.get("session_id") or ""
    return CodexMetadata(
        session_id=str(session_id),
        cwd=str(payload.get("cwd") or ""),
        cli_version=payload.get("cli_version"),
        is_subagent=_is_subagent_source(payload.get("source")),
    )


def peek_codex_metadata(path) -> CodexMetadata | None:
    """先頭の session_meta だけを読む。無ければ None。

    subagent の rollout は「自分の meta」「親の meta」の順に 2 本持つので、
    最初の 1 本だけを見る。
    """
    for entry in _iter_entries(Path(path)):
        if entry.get("type") == "session_meta":
            payload = entry.get("payload")
            if isinstance(payload, dict):
                return _metadata_from(payload)
    return None


def _redact(text: str) -> str:
    return _ENCRYPTED.sub("[encrypted]", text)


def _flatten_output(output) -> tuple[str, bool]:
    """ツール出力を (本文, エラーか) にほぐす。

    Codex の出力は文字列・JSON 文字列・content ブロックの配列のいずれにもなる。
    どれか一つを仮定すると、形が変わったときに本文が丸ごと消える。
    """
    if isinstance(output, list):
        parts = []
        for block in output:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("input_text", "output_text", "text"):
                parts.append(block.get("text") or "")
            elif block.get("type") in ("input_image", "image"):
                media = _media_type(str(block.get("image_url") or ""))
                parts.append(f"[image {media}]" if media else "[image]")
        return "\n".join(p for p in parts if p), False

    if not isinstance(output, str):
        return "", False

    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return output, False  # "Plan updated" のような素の文字列
    if not isinstance(parsed, dict):
        return output, False

    metadata = parsed.get("metadata")
    exit_code = metadata.get("exit_code") if isinstance(metadata, dict) else None
    is_error = bool(exit_code) or bool(parsed.get("timed_out"))
    if "output" in parsed:
        return str(parsed["output"]), is_error
    return output, is_error


def _format_arguments(arguments) -> tuple[dict | None, str]:
    """function_call の arguments を (dict, 表示用テキスト) にする。"""
    if isinstance(arguments, dict):
        parsed = arguments
    elif isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            return None, arguments
    else:
        return None, ""
    if not isinstance(parsed, dict):
        return None, json.dumps(parsed, ensure_ascii=False, indent=2)
    command = parsed.get("command")
    if isinstance(command, list):
        return parsed, " ".join(str(part) for part in command)
    if isinstance(command, str):
        return parsed, command
    return parsed, json.dumps(parsed, ensure_ascii=False, indent=2)


def _summary_for(name: str, arguments: dict | None, input_text: str) -> str:
    if isinstance(arguments, dict):
        for key in _SUMMARY_KEYS:
            value = arguments.get(key)
            if isinstance(value, list):
                value = " ".join(str(part) for part in value)
            if isinstance(value, str) and value.strip():
                return value.strip().splitlines()[0][:SUMMARY_LEN]
    first = next((line for line in input_text.splitlines() if line.strip()), "")
    return first.strip()[:SUMMARY_LEN] or name


def _reasoning_text(payload: dict) -> str:
    """公開された summary だけを採る。encrypted_content には触れない。"""
    summary = payload.get("summary")
    if not isinstance(summary, list):
        return ""
    parts = [b.get("text") or "" for b in summary
             if isinstance(b, dict) and b.get("type") in ("summary_text", "text")]
    return "\n".join(p for p in parts if p.strip())


def _web_search_call(payload: dict) -> ToolCall:
    action = payload.get("action")
    action = action if isinstance(action, dict) else {}
    query = str(action.get("query") or action.get("type") or "web_search")
    return ToolCall(tool_name="web_search", summary=query[:SUMMARY_LEN],
                    input_text=query, result_text="")


class _Builder:
    """assistant 側の item を、Claude のターンと同じ粒度へまとめる。

    Codex は「reasoning → 短い commentary → ツール呼び出し …」を 1 ターンの
    中で繰り返す。全部を 1 ターンに畳むと時刻が失われ、item ごとに分けると
    見出しだらけになるので、reasoning が出るたびに区切る。
    """

    def __init__(self):
        self.turns: list[Turn] = []
        self.model_counts: dict[str, int] = {}
        self._pending: Turn | None = None
        self._pending_model: str = ""

    def flush(self) -> None:
        if self._pending is None:
            return
        self.turns.append(self._pending)
        if self._pending_model:
            self.model_counts[self._pending_model] = (
                self.model_counts.get(self._pending_model, 0) + 1)
        self._pending = None

    def add_user(self, turn: Turn) -> None:
        self.flush()
        self.turns.append(turn)

    def _open(self, ts, model: str) -> Turn:
        if self._pending is None:
            self._pending = Turn(role="assistant", ts=ts)
            self._pending_model = model
        return self._pending

    def add_thinking(self, ts, model: str, text: str) -> None:
        pending = self._pending
        if pending is not None and (pending.text or pending.tool_calls):
            self.flush()
        pending = self._open(ts, model)
        pending.thinking = "\n".join(p for p in (pending.thinking, text) if p)

    def add_message(self, ts, model: str, text: str, phase) -> None:
        pending = self._pending
        # ツール呼び出しを積んだターンには合流させない。Codex では発話が
        # ツール実行の「後」に来るので、合流させると本文とツールの順序が
        # 逆になる（Claude はツールが発話の後なので合流してよい）。
        if pending is not None and (pending.text or pending.tool_calls):
            self.flush()
        pending = self._open(ts, model)
        pending.text = text
        pending.phase = phase

    def add_tool_call(self, ts, model: str, call: ToolCall) -> None:
        self._open(ts, model).tool_calls.append(call)


def _media_type(image_url: str) -> str | None:
    match = _DATA_URL_MEDIA.match(image_url or "")
    return match.group(1) if match else None


def _message_text(content) -> str:
    """会話として明示的に許可したブロックだけを本文に落とす。"""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in ("input_text", "output_text", "text"):
            text = block.get("text") or ""
            stripped = text.lstrip()
            if stripped.startswith(INJECTED_PREFIXES):
                continue
            if _IMAGE_WRAPPER.match(text.strip()):
                continue
            parts.append(text)
        elif btype in ("input_image", "image"):
            # 添付だけの発話も残す。base64 本体はノートへ入れない。
            media = _media_type(str(block.get("image_url") or ""))
            parts.append(f"[image {media}]" if media else "[image]")
    return "\n".join(p for p in parts if p.strip())


def parse_codex_transcript(
    path,
    *,
    session_id_hint: str = "",
    cwd_hint: str = "",
    model_hint: str = "",
) -> Session | None:
    """Codex rollout を Session へ変換する。会話が無ければ None。"""
    path = Path(path)
    metadata: CodexMetadata | None = None
    builder = _Builder()
    tool_counts: dict[str, int] = {}
    calls_by_id: dict[str, ToolCall] = {}
    seen_items: set[str] = set()
    user_turns = 0
    model = model_hint

    for entry in _iter_entries(path):
        # 壊れた 1 エントリでセッション全体を失わない。
        try:
            etype = entry.get("type")
            payload = entry.get("payload")

            if etype == "session_meta":
                if metadata is None and isinstance(payload, dict):
                    metadata = _metadata_from(payload)
                    if metadata.is_subagent:
                        # subagent の rollout は親セッションと重複するうえ、
                        # SessionEnd も発火しない。ノートにはしない。
                        return None
                continue

            if etype == "turn_context":
                if isinstance(payload, dict) and payload.get("model"):
                    model = str(payload["model"])
                continue

            if etype != "response_item" or not isinstance(payload, dict):
                continue  # event_msg / world_state などは会話ではない

            item_id = payload.get("id")
            if isinstance(item_id, str) and item_id:
                if item_id in seen_items:
                    continue  # 同じ item が二度書かれても二重に数えない
                seen_items.add(item_id)

            ptype = payload.get("type")

            if ptype == "message":
                role = payload.get("role")
                if role not in CONVERSATION_ROLES:
                    continue  # developer には内部指示が入る
                text = _message_text(payload.get("content"))
                if not text.strip():
                    continue  # 自動注入だけで構成された message
                ts = to_jst(entry["timestamp"])
                if role == "user":
                    user_turns += 1
                    builder.add_user(Turn(role="user", ts=ts, text=text))
                else:
                    builder.add_message(ts, model, text, payload.get("phase"))
                continue

            if ptype == "reasoning":
                text = _reasoning_text(payload)
                if not text.strip():
                    continue
                builder.add_thinking(to_jst(entry["timestamp"]), model, text)
                continue

            if ptype in ("function_call", "custom_tool_call"):
                name = str(payload.get("name") or "unknown")
                if ptype == "function_call":
                    arguments, input_text = _format_arguments(payload.get("arguments"))
                else:
                    arguments, input_text = None, str(payload.get("input") or "")
                call = ToolCall(
                    tool_name=name,
                    summary=_redact(_summary_for(name, arguments, input_text)),
                    input_text=_redact(input_text),
                    result_text="",
                )
                call_id = payload.get("call_id")
                if isinstance(call_id, str) and call_id:
                    calls_by_id[call_id] = call
                tool_counts[name] = tool_counts.get(name, 0) + 1
                builder.add_tool_call(to_jst(entry["timestamp"]), model, call)
                continue

            if ptype in ("function_call_output", "custom_tool_call_output"):
                call = calls_by_id.get(payload.get("call_id"))
                if call is None:
                    continue  # 対応する呼び出しが読めなかったもの
                result_text, is_error = _flatten_output(payload.get("output"))
                call.result_text = _redact(result_text)
                call.is_error = is_error
                continue

            if ptype == "web_search_call":
                call = _web_search_call(payload)
                tool_counts[call.tool_name] = tool_counts.get(call.tool_name, 0) + 1
                builder.add_tool_call(to_jst(entry["timestamp"]), model, call)
                continue

        except Exception:
            continue

    builder.flush()
    turns = builder.turns
    if not turns:
        return None

    session_id = (metadata.session_id if metadata else "") or session_id_hint
    if not session_id:
        return None  # 由来を特定できないものは state の主キーを作れない

    cwd = (metadata.cwd if metadata else "") or cwd_hint
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
        model_counts=builder.model_counts,
        tool_counts=tool_counts,
        user_turns=user_turns,
        source=SOURCE,
        source_version=metadata.cli_version if metadata else None,
    )
