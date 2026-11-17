from copy import deepcopy
from dataclasses import dataclass

from my_agent.core.session import Session, SessionStatus


@dataclass(frozen=True)
class StatusUpdate:
    session_id: str
    turn: int
    status: SessionStatus


class InMemorySessionStore:
    """Record independent snapshots so tests can inspect completed turns."""

    def __init__(self) -> None:
        self.checkpoints: list[Session] = []
        self.status_updates: list[StatusUpdate] = []

    async def checkpoint(self, session: Session) -> None:
        self.checkpoints.append(deepcopy(session))

    async def save_status(self, session: Session) -> None:
        self.status_updates.append(StatusUpdate(session.id, session.turn, session.status))
