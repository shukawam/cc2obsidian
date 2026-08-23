"""JSONL とレンダリングの間に挟む中間表現。"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ToolCall:
    tool_name: str
    summary: str          # details の summary 行に出す短い説明
    input_text: str       # 整形済みの入力パラメータ
    result_text: str      # 切り詰め前の結果テキスト
    is_error: bool = False


@dataclass
class Turn:
    role: str             # "user" | "assistant"
    ts: datetime          # JST
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    is_sidechain: bool = False


@dataclass
class Session:
    session_id: str
    cwd: str
    project: str
    title: str
    started_at: datetime
    ended_at: datetime
    turns: list[Turn] = field(default_factory=list)
    model_counts: dict[str, int] = field(default_factory=dict)
    tool_counts: dict[str, int] = field(default_factory=dict)
    user_turns: int = 0

    @property
    def duration_min(self) -> int:
        return round((self.ended_at - self.started_at).total_seconds() / 60)
