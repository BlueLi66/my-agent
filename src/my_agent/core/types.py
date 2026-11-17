from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


Block = TextBlock | ToolCall | ToolResult
StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    blocks: list[Block]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __iadd__(self, other: "Usage") -> "Usage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        return self


@dataclass(frozen=True)
class ModelResponse:
    message: Message
    usage: Usage
    stop_reason: StopReason

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [block for block in self.message.blocks if isinstance(block, ToolCall)]

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.message.blocks if isinstance(block, TextBlock))


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
