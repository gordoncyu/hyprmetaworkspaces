from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .dispatchers import DispatchError, handle_dispatch
from .hyprland_ipc import HyprlandIPCError, iter_events
from .state import DaemonState


def _daemon_socket_path() -> Path:
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not his:
        raise RuntimeError("HYPRLAND_INSTANCE_SIGNATURE is not set")
    state_dir = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    sock_dir = state_dir / "hypr" / "meta_workspaces" / his
    sock_dir.mkdir(parents=True, exist_ok=True)
    return sock_dir / "socket.sock"


def _make_response(id: int | str | None, result: object) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "id": id, "result": result}) + "\n").encode()


def _make_error(id: int | str | None, code: int, message: str) -> bytes:
    return (
        json.dumps({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}) + "\n"
    ).encode()


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: DaemonState,
) -> None:
    try:
        data = await reader.read(65536)
        if not data:
            return
        try:
            req: dict[str, object] = json.loads(data)
        except json.JSONDecodeError as exc:
            writer.write(_make_error(None, -32700, f"Parse error: {exc}"))
            await writer.drain()
            return

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if not isinstance(method, str):
            writer.write(_make_error(req_id, -32600, "Invalid request: method must be a string"))
            await writer.drain()
            return

        if method == "dispatch":
            if not isinstance(params, dict):
                writer.write(_make_error(req_id, -32602, "params must be an object"))
                await writer.drain()
                return
            dispatcher = params.get("dispatcher")
            args = params.get("args", "")
            if not isinstance(dispatcher, str):
                writer.write(_make_error(req_id, -32602, "params.dispatcher must be a string"))
                await writer.drain()
                return
            if not isinstance(args, str):
                writer.write(_make_error(req_id, -32602, "params.args must be a string"))
                await writer.drain()
                return
            try:
                result = handle_dispatch(dispatcher, args, state)
                writer.write(_make_response(req_id, result))
            except DispatchError as exc:
                writer.write(_make_error(req_id, -32602, str(exc)))

        elif method == "get_state":
            writer.write(
                _make_response(
                    req_id,
                    {
                        "current_mw": state.current_mw,
                        "last_visited": {str(k): v for k, v in state.last_visited.items()},
                        "upper_bound_mw": state.config.upper_bound_mw(),
                    },
                )
            )

        else:
            writer.write(_make_error(req_id, -32601, f"Method not found: {method!r}"))

        await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _event_listener(state: DaemonState) -> None:
    while True:
        try:
            async for event, data in iter_events():
                if event == "workspace":
                    try:
                        ws = int(data.strip())
                    except ValueError:
                        continue
                    state.record_visit(ws)
        except Exception as exc:
            print(f"[hyprmetaworkspaced] Event listener error: {exc}", file=sys.stderr)
            await asyncio.sleep(1)


async def _run(sock_path: Path, state: DaemonState) -> None:
    if sock_path.exists():
        sock_path.unlink()

    async def client_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_client(reader, writer, state)

    server = await asyncio.start_unix_server(client_handler, path=str(sock_path))
    print(f"[hyprmetaworkspaced] Listening on {sock_path}", file=sys.stderr)

    async with server:
        await asyncio.gather(
            server.serve_forever(),
            _event_listener(state),
        )


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[hyprmetaworkspaced] Config error: {exc}", file=sys.stderr)
        sys.exit(1)
    except HyprlandIPCError as exc:
        print(f"[hyprmetaworkspaced] IPC error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        sock_path = _daemon_socket_path()
    except RuntimeError as exc:
        print(f"[hyprmetaworkspaced] {exc}", file=sys.stderr)
        sys.exit(1)

    state = DaemonState(config=config)

    try:
        asyncio.run(_run(sock_path, state))
    except KeyboardInterrupt:
        pass
    finally:
        if sock_path.exists():
            sock_path.unlink()
