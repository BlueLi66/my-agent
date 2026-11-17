import os
from pathlib import Path


def workspace_glob_pattern(pattern: str) -> str:
    path = Path(pattern)
    if path.anchor or ".." in path.parts:
        raise ValueError("Glob patterns must stay within the workspace")
    return path.as_posix()


def safe_path(path: str | Path, workdir: Path) -> Path:
    """Resolve a workspace path, including symlinks and junctions."""
    if os.name == "nt" and os.path.isreserved(path):
        raise ValueError(f"Reserved Windows path: {path}")
    root = workdir.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved
