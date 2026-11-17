from typing import Any

from ..base import ToolEnv, ToolExecutionError
from ..paths import safe_path
from ..process import run_shell


async def run_bash(args: dict[str, Any], env: ToolEnv) -> str:
    cwd = safe_path(".", env.workdir)
    result = await run_shell(args["command"], cwd, merge_stderr=True)
    output = result.stdout.strip() or "(no output)"
    if result.returncode:
        raise ToolExecutionError(f"Command exited with status {result.returncode}\n{output}")
    return output
