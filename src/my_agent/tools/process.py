import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        if process.returncode is not None:
            return
        # Kill the tree before terminating cmd.exe, while its children can still be found.
        taskkill = os.path.join(os.environ["SystemRoot"], "System32", "taskkill.exe")
        try:
            killer = await asyncio.create_subprocess_exec(
                taskkill, "/pid", str(process.pid), "/t", "/f",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), 2)
            except TimeoutError:
                killer.kill()
                await killer.wait()
        except OSError:
            pass
        if process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), 0.2)
            except TimeoutError:
                process.kill()
            except ProcessLookupError:
                pass
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
        await asyncio.sleep(0.1)
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def run_shell(
    command: str, cwd: Path, *, input_data: bytes | None = None, merge_stderr: bool = False
) -> ShellResult:
    if os.name == "nt":
        cmd = os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
        options = {"executable": cmd, "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        options = {"start_new_session": True}

    # A cancellation during spawn must still acquire the process handle for cleanup.
    spawn = asyncio.create_task(asyncio.create_subprocess_shell(
        command, cwd=cwd, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE, **options,
    ))
    process = None
    output_task = None
    try:
        process = await asyncio.shield(spawn)
        output_task = asyncio.create_task(process.communicate(input_data))
        stdout, stderr = await asyncio.shield(output_task)
    except asyncio.CancelledError:
        if process is None:
            process = await spawn
        await _terminate_process(process)
        if output_task is None:
            await process.communicate()
        else:
            await output_task
        raise

    assert process.returncode is not None
    return ShellResult(
        process.returncode, stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace") if stderr is not None else "",
    )
