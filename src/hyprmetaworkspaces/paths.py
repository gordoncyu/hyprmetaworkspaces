from __future__ import annotations

import os
from pathlib import Path

HIS_PREFIX_LEN = 12


def resolve_runtime_dir() -> Path:
    return Path(
        os.environ.get("HYPRMETAWORKSPACES_RUNTIME_DIR")
        or os.environ.get("XDG_RUNTIME_DIR")
        or "/tmp"
    )


def daemon_socket_path(his: str, runtime_dir: Path) -> Path:
    return runtime_dir / "hypr" / "meta_workspaces" / his[:HIS_PREFIX_LEN] / "socket.sock"
