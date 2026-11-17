from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .types import Message, Usage

SessionStatus = Literal["running", "done", "deferred", "cancelled", "failed", "max_turns"]


@dataclass
class Session:
    id: str
    parent_id: str | None = None
    messages: list[Message] = field(default_factory=list)
    ext: dict[str, Any] = field(default_factory=dict)
    turn: int = 0
    usage: Usage = field(default_factory=Usage)
    status: SessionStatus = "running"


class SessionStore(Protocol):
    async def checkpoint(self, session: Session) -> None: ...

    async def save_status(self, session: Session) -> None: ...
