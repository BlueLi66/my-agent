from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from my_agent.core.cancel import CancelToken


@dataclass
class ToolEnv:
    workdir: Path
    session_id: str
    ext: dict[str, Any]
    cancel: CancelToken
    is_interactive: bool


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolEnv], Awaitable[str]]
    side_effect: Literal["read", "write", "exec"] = "read"
    timeout_s: float = 120.0
    max_output_chars: int = 20_000
