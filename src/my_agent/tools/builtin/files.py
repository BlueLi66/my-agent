import asyncio
import glob as globlib
import os
import re
from collections.abc import AsyncIterator
from itertools import islice
from pathlib import Path
from typing import Any

from ..base import ToolEnv, ToolExecutionError
from ..paths import safe_path, workspace_glob_pattern


async def read_file(args: dict[str, Any], env: ToolEnv) -> str:
    path = safe_path(args["path"], env.workdir)
    offset = args.get("offset", 0)
    limit = args.get("limit")
    end = offset + limit + 1 if limit is not None else None
    with path.open(encoding="utf-8") as file:
        lines = list(islice(file, offset, end))
    if limit is not None and len(lines) > limit:
        lines[-1] = "... (more lines omitted)"
    return "\n".join(line.rstrip("\n") for line in lines)


async def write_file(args: dict[str, Any], env: ToolEnv) -> str:
    path = safe_path(args["path"], env.workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    size = path.write_bytes(args["content"].encode("utf-8"))
    return f"Wrote {size} bytes to {args['path']}"


async def edit_file(args: dict[str, Any], env: ToolEnv) -> str:
    path = safe_path(args["path"], env.workdir)
    source = path.read_bytes().decode("utf-8")
    old, new = args["old_text"], args["new_text"]
    if old not in source and "\r\n" in source:
        old = old.replace("\r\n", "\n").replace("\n", "\r\n")
    if old not in source:
        raise ToolExecutionError(f"Text not found in {args['path']}")
    if "\r\n" in source:
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(source.replace(old, new, 1).encode("utf-8"))
    return f"Edited {args['path']}"


def _glob_matcher(pattern: str) -> re.Pattern[str]:
    normalized = workspace_glob_pattern(pattern)
    regex = globlib.translate(normalized, recursive=True, include_hidden=True, seps="/")
    return re.compile(regex, re.IGNORECASE if os.name == "nt" else 0)


async def _workspace_files(directory: Path, env: ToolEnv) -> AsyncIterator[tuple[Path, Path]]:
    """Yield relative names with their checked paths, skipping directory links."""
    root = safe_path(".", env.workdir)
    for current, dirs, files in directory.walk(follow_symlinks=False):
        # Windows junctions may be listed as directories even with follow_symlinks disabled.
        dirs[:] = sorted(name for name in dirs if not (current / name).is_junction())
        await asyncio.sleep(0)
        for name in sorted(files):
            candidate = current / name
            try:
                resolved = safe_path(candidate, root)
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                yield candidate.relative_to(root), resolved


async def glob_files(args: dict[str, Any], env: ToolEnv) -> str:
    matcher = _glob_matcher(args["pattern"])
    root = safe_path(".", env.workdir)
    matches = [
        relative.as_posix()
        async for relative, _ in _workspace_files(root, env)
        if matcher.fullmatch(relative.as_posix())
    ]
    return "\n".join(sorted(matches)) if matches else "(no matches)"


async def grep_files(args: dict[str, Any], env: ToolEnv) -> str:
    regex = re.compile(args["pattern"])
    matcher = _glob_matcher(args["glob"]) if "glob" in args else None
    root = safe_path(".", env.workdir)
    path = safe_path(args.get("path", "."), root)
    matches: list[str] = []

    async def scan(relative: Path, file_path: Path) -> None:
        name = relative.as_posix()
        if matcher is not None and not matcher.fullmatch(name):
            return
        with file_path.open(encoding="utf-8", errors="replace") as file:
            for number, line in enumerate(file, 1):
                if regex.search(line):
                    matches.append(f"{name}:{number}: {line.rstrip()}")
                if number % 256 == 0:
                    await asyncio.sleep(0)

    if path.is_dir():
        async for relative, file_path in _workspace_files(path, env):
            await scan(relative, file_path)
    else:
        await scan(path.relative_to(root), path)
    return "\n".join(matches) if matches else "(no matches)"
