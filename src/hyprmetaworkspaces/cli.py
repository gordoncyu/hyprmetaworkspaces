from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def _daemon_socket_path() -> Path:
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not his:
        print("Error: HYPRLAND_INSTANCE_SIGNATURE is not set", file=sys.stderr)
        sys.exit(1)
    state_dir = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return state_dir / "hypr" / "meta_workspaces" / his / "socket.sock"


def _send_request(req: dict[str, object]) -> dict[str, object]:
    path = _daemon_socket_path()
    if not path.exists():
        print(f"Error: daemon socket not found at {path}", file=sys.stderr)
        sys.exit(1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
        sock.sendall(json.dumps(req).encode())
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()

    raw = b"".join(chunks).decode(errors="replace").strip()
    response: dict[str, object] = json.loads(raw)
    return response


def _dispatch(dispatcher: str, args: str) -> None:
    req: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "dispatch",
        "params": {"dispatcher": dispatcher, "args": args},
    }
    resp = _send_request(req)
    if "error" in resp:
        err = resp["error"]
        if isinstance(err, dict):
            print(f"Error: {err.get('message', err)}", file=sys.stderr)
        else:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
    result = resp.get("result")
    if result and result != "ok":
        print(result)


def _get_state() -> None:
    req: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "get_state",
        "params": {},
    }
    resp = _send_request(req)
    if "error" in resp:
        err = resp["error"]
        if isinstance(err, dict):
            print(f"Error: {err.get('message', err)}", file=sys.stderr)
        else:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(resp.get("result"), indent=2))


USAGE = """\
Usage: hyprmwctl <command> [args]

Commands:
  dispatch <dispatcher> [args]
      Send a dispatch command to the daemon.

      Dispatchers:
        workspace <digit>
        movetoworkspace <digit>
        movetoworkspacesilent <digit>
        metaworkspace <n>[,chordworkspace]
        movetometaworkspace <n>[,chordworkspace]
        movetometaworkspacesilent <n>[,chordworkspace]
        workspacesequential <next|prev>[,skipempty][,skipsurrounding][,wrapin|wrapout]
        metaworkspacesequential <next|prev>[,skipempty][,wrap|nowrap]

      Arguments are comma-delimited or space-separated.

  get-state
      Print current daemon state as JSON.
"""


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    command = args[0]

    if command == "get-state":
        _get_state()
        return

    if command == "dispatch":
        if len(args) < 2:
            print("Error: dispatch requires a dispatcher name", file=sys.stderr)
            sys.exit(1)
        dispatcher = args[1]
        dispatch_args = ",".join(args[2:]) if len(args) > 2 else ""
        _dispatch(dispatcher, dispatch_args)
        return

    print(f"Error: unknown command {command!r}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    sys.exit(1)
