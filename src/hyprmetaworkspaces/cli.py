from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from .paths import daemon_socket_path, resolve_runtime_dir


def _daemon_socket_path() -> Path:
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not his:
        print("Error: HYPRLAND_INSTANCE_SIGNATURE is not set", file=sys.stderr)
        sys.exit(1)
    return daemon_socket_path(his, resolve_runtime_dir())


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
  dispatch    Send a dispatch command to the daemon.
  get-state   Print current daemon state as JSON.

Run 'hyprmwctl <command> --help' for details.
"""

DISPATCH_USAGE = """\
Usage: hyprmwctl dispatch <dispatcher> [args]

Send a dispatch command to the daemon.
Arguments are comma-delimited or space-separated.

Dispatchers:
  workspace <digit>
      Navigate to workspace 0-9 in the current metaworkspace.
  movetoworkspace <digit>
      Move focused window to workspace 0-9 in current mw (follows).
  movetoworkspacesilent <digit>
      Move focused window to workspace 0-9 in current mw (stays).
  metaworkspace <n>[,chordworkspace]
      Switch to metaworkspace n (lands on last-visited workspace).
      With chordworkspace, enter a submap for digit selection.
  movetometaworkspace <n>[,chordworkspace]
      Move focused window to metaworkspace n (follows).
      With chordworkspace, enter a submap (stays until digit pressed).
  movetometaworkspacesilent <n>[,chordworkspace]
      Move focused window to metaworkspace n (stays).
      With chordworkspace, enter a submap for digit selection.
  workspacesequential <next|prev>[,flags...]
      Step to next/prev workspace in band order.
      Flags: skipempty, skipsurrounding, wrapin, wrapout (default).
  metaworkspacesequential <next|prev>[,flags...]
      Step to next/prev metaworkspace.
      Flags: skipempty, wrap (default), nowrap.

Run 'hyprmwctl dispatch <dispatcher> --help' for details.
"""

DISPATCHER_HELP: dict[str, str] = {
    "workspace": """\
Usage: hyprmwctl dispatch workspace <digit>

Navigate to workspace 0-9 within the current metaworkspace.
Digit is mapped to an absolute workspace number based on the current
metaworkspace and workspace_zero_last setting.

  digit   0-9, where 0 maps to the 10th workspace (workspace_zero_last=true)
          or the 0th workspace (workspace_zero_last=false).
""",
    "movetoworkspace": """\
Usage: hyprmwctl dispatch movetoworkspace <digit>

Move the focused window to workspace 0-9 in the current metaworkspace
and follow it. Digit mapping is the same as 'workspace'.
""",
    "movetoworkspacesilent": """\
Usage: hyprmwctl dispatch movetoworkspacesilent <digit>

Move the focused window to workspace 0-9 in the current metaworkspace
without following. Focus stays on the current workspace.
""",
    "metaworkspace": """\
Usage: hyprmwctl dispatch metaworkspace <n>[,chordworkspace]

Switch to metaworkspace n. Lands on the last-visited inner workspace,
or the default (digit 1) if never visited.

  n                Integer metaworkspace index (0, 1, 2, ...).
  chordworkspace   Optional. Enter a Hyprland submap where pressing a
                   digit 0-9 navigates to that workspace within mw n.
                   Switches to mw n first, then enters the submap.
""",
    "movetometaworkspace": """\
Usage: hyprmwctl dispatch movetometaworkspace <n>[,chordworkspace]

Move the focused window to metaworkspace n and follow it. Lands on the
last-visited workspace, or the default if never visited.

  n                Integer metaworkspace index (0, 1, 2, ...).
  chordworkspace   Optional. Enter a submap where pressing a digit 0-9
                   moves the window to that workspace within mw n.
                   Stays on the current workspace until a digit is pressed.
""",
    "movetometaworkspacesilent": """\
Usage: hyprmwctl dispatch movetometaworkspacesilent <n>[,chordworkspace]

Move the focused window to metaworkspace n without following.

  n                Integer metaworkspace index (0, 1, 2, ...).
  chordworkspace   Optional. Enter a submap where pressing a digit 0-9
                   silently moves the window to that workspace within mw n.
""",
    "workspacesequential": """\
Usage: hyprmwctl dispatch workspacesequential <next|prev>[,flags...]

Step to the next or previous workspace in band order.

Flags (comma-delimited):
  skipempty        Skip workspaces with no windows.
  skipsurrounding  Skip surrounding workspaces, only visit inner workspaces.
  wrapin           Wrap within the current metaworkspace's band.
  wrapout          Cross into adjacent metaworkspaces at band edges (default).
""",
    "metaworkspacesequential": """\
Usage: hyprmwctl dispatch metaworkspacesequential <next|prev>[,flags...]

Step to the next or previous metaworkspace, landing on the last-visited
workspace within the target metaworkspace.

Flags (comma-delimited):
  skipempty   Skip metaworkspaces whose inner workspaces have no windows.
  wrap        Wrap around at boundaries (default).
  nowrap      Stop at the first/last metaworkspace.
""",
}

GET_STATE_USAGE = """\
Usage: hyprmwctl get-state

Print current daemon state as JSON.

Output includes:
  current_mw      The active metaworkspace index.
  last_visited    Map of metaworkspace index to last-visited workspace number.
  upper_bound_mw  Upper metaworkspace limit (null if unbounded).
"""


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    command = args[0]

    if command == "get-state":
        if len(args) >= 2 and args[1] in ("-h", "--help"):
            print(GET_STATE_USAGE)
            sys.exit(0)
        _get_state()
        return

    if command == "dispatch":
        if len(args) < 2 or args[1] in ("-h", "--help"):
            print(DISPATCH_USAGE)
            sys.exit(0)
        dispatcher = args[1]
        if len(args) >= 3 and args[2] in ("-h", "--help"):
            help_text = DISPATCHER_HELP.get(dispatcher)
            if help_text:
                print(help_text)
            else:
                print(f"Error: unknown dispatcher {dispatcher!r}", file=sys.stderr)
                print(DISPATCH_USAGE, file=sys.stderr)
                sys.exit(1)
            sys.exit(0)
        dispatch_args = ",".join(args[2:]) if len(args) > 2 else ""
        _dispatch(dispatcher, dispatch_args)
        return

    print(f"Error: unknown command {command!r}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    sys.exit(1)
