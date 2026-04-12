from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path


class HyprlandIPCError(Exception):
    pass


def _socket_path(suffix: str) -> Path:
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not his:
        raise HyprlandIPCError("HYPRLAND_INSTANCE_SIGNATURE is not set")
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return Path(xdg_runtime) / "hypr" / his / suffix


def command_socket_path() -> Path:
    return _socket_path(".socket.sock")


def event_socket_path() -> Path:
    return _socket_path(".socket2.sock")


def send_command(cmd: str) -> str:
    """Send a single command to Hyprland's command socket and return the response."""
    path = command_socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
        sock.sendall(cmd.encode())
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")
    finally:
        sock.close()


def dispatch(dispatcher: str, args: str) -> str:
    """Send a hyprctl dispatch command."""
    return send_command(f"dispatch {dispatcher} {args}")


def keyword(key: str, value: str) -> str:
    """Send a hyprctl keyword command."""
    return send_command(f"keyword {key} {value}")


def query_clients() -> list[dict[str, object]]:
    """Query all open clients from Hyprland, returning parsed JSON."""
    raw = send_command("j/clients")
    result: list[dict[str, object]] = json.loads(raw)
    return result


def workspaces_with_windows() -> set[int]:
    """Return the set of workspace IDs that have at least one window."""
    clients = query_clients()
    result: set[int] = set()
    for client in clients:
        ws = client.get("workspace")
        if isinstance(ws, dict):
            ws_id = ws.get("id")
            if isinstance(ws_id, int):
                result.add(ws_id)
    return result


async def iter_events() -> AsyncIterator[tuple[str, str]]:
    """
    Async generator that yields (event_name, data) tuples from Hyprland's event socket.
    Maintains a persistent connection and streams events indefinitely.
    """
    path = event_socket_path()
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            decoded = line.decode(errors="replace").rstrip("\n")
            if ">>" in decoded:
                event, _, data = decoded.partition(">>")
                yield event, data
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
