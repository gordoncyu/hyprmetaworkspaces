"""
Integration tests using a real nested Hyprland compositor.

These tests start a nested Hyprland inside the live Wayland session,
detect its instance signature, start hyprmetaworkspaced against it,
then send JSON-RPC commands and assert that the correct workspace events
arrive on the Hyprland event socket.

Skip automatically when no Wayland session is available.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers for communicating with Hyprland and the daemon
# ---------------------------------------------------------------------------

_SRCDIR = str(Path(__file__).parent.parent / "src")


def _hypr_runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return Path(xdg) / "hypr"


def _existing_instances() -> set[str]:
    hypr_dir = _hypr_runtime_dir()
    if not hypr_dir.exists():
        return set()
    return {p.name for p in hypr_dir.iterdir() if p.is_dir()}


def _detect_new_instance(before: set[str], timeout: float = 15.0) -> str | None:
    """Poll until a new HIS directory with .socket.sock appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        after = _existing_instances()
        new = after - before
        for his in new:
            sock = _hypr_runtime_dir() / his / ".socket.sock"
            if sock.exists():
                return his
        time.sleep(0.25)
    return None


def _send_hypr_command(his: str, cmd: str) -> str:
    xdg = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    sock_path = Path(xdg) / "hypr" / his / ".socket.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(sock_path))
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


def _collect_events(his: str, q: queue.Queue[tuple[str, str]], stop: threading.Event) -> None:
    """Background thread: read lines from .socket2.sock and push (event, data) into q."""
    xdg = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    sock_path = Path(xdg) / "hypr" / his / ".socket2.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(sock_path))
        sock.settimeout(0.5)
        buf = b""
        while not stop.is_set():
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    decoded = line.decode(errors="replace")
                    if ">>" in decoded:
                        event, _, data = decoded.partition(">>")
                        q.put((event, data))
            except TimeoutError:
                continue
    finally:
        sock.close()


def _send_daemon_request(daemon_sock: Path, req: dict[str, object]) -> dict[str, object]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(daemon_sock))
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
    return json.loads(raw)


def _drain_events(q: queue.Queue[tuple[str, str]], timeout: float = 0.5) -> list[tuple[str, str]]:
    """Drain all events currently in the queue, waiting up to timeout for the first."""
    events: list[tuple[str, str]] = []
    try:
        first = q.get(timeout=timeout)
        events.append(first)
    except queue.Empty:
        return events
    while True:
        try:
            events.append(q.get_nowait())
        except queue.Empty:
            break
    return events


def _wait_for_workspace_event(
    q: queue.Queue[tuple[str, str]], expected_ws: int, timeout: float = 5.0
) -> bool:
    """Wait until a workspace>><expected_ws> event arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            event, data = q.get(timeout=min(remaining, 0.5))
            if event == "workspace" and data.strip() == str(expected_ws):
                return True
        except queue.Empty:
            continue
    return False


# ---------------------------------------------------------------------------
# Minimal Hyprland config for the nested compositor
# ---------------------------------------------------------------------------

_HYPR_CONF = """\
monitor = ,preferred,auto,1
animations { enabled = false }
misc { disable_hyprland_logo = true; disable_splash_rendering = true }
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nested_hyprland():
    """
    Start a nested Hyprland and yield (his, env_dict).
    Skips the entire module if Hyprland cannot start.
    """
    if not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("No Wayland session — nested Hyprland requires a live compositor")

    hyprland_bin = subprocess.run(
        ["which", "hyprland"], capture_output=True, text=True
    ).stdout.strip()
    if not hyprland_bin:
        pytest.skip("hyprland binary not found in PATH")

    with tempfile.TemporaryDirectory(prefix="hypr_integration_") as tmpdir:
        conf_path = Path(tmpdir) / "hyprland.conf"
        conf_path.write_text(_HYPR_CONF)

        before = _existing_instances()
        env = dict(os.environ)

        proc = subprocess.Popen(
            ["hyprland", "--config", str(conf_path)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            his = _detect_new_instance(before, timeout=20.0)
            if his is None:
                proc.terminate()
                proc.wait(timeout=5)
                pytest.skip("Nested Hyprland did not start within 20s")

            yield his, env
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


@pytest.fixture(scope="module")
def daemon(nested_hyprland, tmp_path_factory):
    """
    Start hyprmetaworkspaced against the nested Hyprland and yield the daemon socket path.
    """
    his, base_env = nested_hyprland

    state_dir = tmp_path_factory.mktemp("hmw_state")
    config_dir = tmp_path_factory.mktemp("hmw_config")

    # Write a config with no surrounding workspaces so inner range is unbounded
    (config_dir / "hypr").mkdir(parents=True, exist_ok=True)
    (config_dir / "hypr" / "hyprmetaworkspaces.conf").write_text(
        "workspace_zero_last=true\n"
        "surrounding_left_workspaces=\n"
        "surrounding_right_workspaces=\n"
    )

    env = dict(base_env)
    env["HYPRLAND_INSTANCE_SIGNATURE"] = his
    env["XDG_STATE_HOME"] = str(state_dir)
    env["XDG_CONFIG_DIR"] = str(config_dir)
    env["PYTHONPATH"] = _SRCDIR

    daemon_sock = state_dir / "hypr" / "meta_workspaces" / his / "socket.sock"

    proc = subprocess.Popen(
        [sys.executable, "-m", "hyprmetaworkspaces.daemon"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait for socket to appear
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if daemon_sock.exists():
                break
            time.sleep(0.1)
        else:
            proc.terminate()
            proc.wait(timeout=5)
            pytest.skip("Daemon socket did not appear within 10s")

        yield daemon_sock
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture()
def event_queue(nested_hyprland):
    """Background thread collecting Hyprland events into a queue."""
    his, _ = nested_hyprland
    q: queue.Queue[tuple[str, str]] = queue.Queue()
    stop = threading.Event()
    t = threading.Thread(target=_collect_events, args=(his, q, stop), daemon=True)
    t.start()
    yield q
    stop.set()
    t.join(timeout=2)


# ---------------------------------------------------------------------------
# Helper to dispatch through the daemon
# ---------------------------------------------------------------------------


def _dispatch(daemon_sock: Path, dispatcher: str, args: str) -> dict[str, object]:
    req: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "dispatch",
        "params": {"dispatcher": dispatcher, "args": args},
    }
    return _send_daemon_request(daemon_sock, req)


def _get_state(daemon_sock: Path) -> dict[str, object]:
    req: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "get_state",
        "params": {},
    }
    resp = _send_daemon_request(daemon_sock, req)
    result = resp.get("result")
    assert isinstance(result, dict)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkspaceDispatcher:
    def test_workspace_digit_navigates_to_correct_ws(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        """workspace 3 from mw0 with zero_last=true → ws 3."""
        his, _ = nested_hyprland
        # Ensure we start at mw0 ws1
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "workspace", "3")
        assert resp.get("result") == "ok"
        assert _wait_for_workspace_event(event_queue, 3)

    def test_workspace_digit_zero_maps_to_10(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        """workspace 0 with zero_last=true maps to ws 10 for mw0."""
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "workspace", "0")
        assert resp.get("result") == "ok"
        assert _wait_for_workspace_event(event_queue, 10)

    def test_workspace_digit_invalid_returns_error(self, daemon) -> None:
        resp = _dispatch(daemon, "workspace", "99")
        assert "error" in resp


class TestMetaworkspaceDispatcher:
    def test_metaworkspace_switches_to_correct_band(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        """metaworkspace 1 → lands on ws 11 (default for mw1 with zero_last=true)."""
        his, _ = nested_hyprland
        _drain_events(event_queue)

        resp = _dispatch(daemon, "metaworkspace", "1")
        assert resp.get("result") == "ok"
        # Default for mw1 is ws 11 (digit 1 → n*10+1 = 11)
        assert _wait_for_workspace_event(event_queue, 11)

    def test_metaworkspace_remembers_last_visited(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        """After visiting mw1 ws14, switching away and back lands on 14."""
        his, _ = nested_hyprland
        # Go to mw1 ws14
        _dispatch(daemon, "metaworkspace", "1")
        time.sleep(0.2)
        _dispatch(daemon, "workspace", "4")
        time.sleep(0.2)
        _drain_events(event_queue)

        # Switch away to mw0
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.2)
        _drain_events(event_queue)

        # Switch back to mw1 — should land on ws14
        resp = _dispatch(daemon, "metaworkspace", "1")
        assert resp.get("result") == "ok"
        assert _wait_for_workspace_event(event_queue, 14)

    def test_metaworkspace_invalid_returns_noop(self, daemon) -> None:
        resp = _dispatch(daemon, "metaworkspace", "9999")
        result = resp.get("result")
        assert isinstance(result, str) and "noop" in result


class TestWorkspaceSequential:
    def test_next_from_ws1_goes_to_ws2(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        his, _ = nested_hyprland
        # Start at mw0 ws1
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.1)
        _dispatch(daemon, "workspace", "1")
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "workspacesequential", "next")
        assert resp.get("result") == "ok"
        assert _wait_for_workspace_event(event_queue, 2)

    def test_next_wraps_from_mw0_to_mw1(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        """Sequential next from last ws of mw0 (ws10) crosses into mw1 (ws11)."""
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.1)
        _dispatch(daemon, "workspace", "0")  # ws10
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "workspacesequential", "next")
        assert resp.get("result") == "ok"
        assert _wait_for_workspace_event(event_queue, 11)

    def test_prev_from_ws11_goes_to_ws10(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "1")
        time.sleep(0.1)
        _dispatch(daemon, "workspace", "1")  # ws11
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "workspacesequential", "prev")
        assert resp.get("result") == "ok"
        assert _wait_for_workspace_event(event_queue, 10)

    def test_wrapin_wraps_within_band(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        """Sequential next with wrapin from ws10 stays in mw0 → ws1."""
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.1)
        _dispatch(daemon, "workspace", "0")  # ws10
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "workspacesequential", "next,wrapin")
        assert resp.get("result") == "ok"
        assert _wait_for_workspace_event(event_queue, 1)


class TestMetaworkspaceSequential:
    def test_next_mw_from_mw0_goes_to_mw1(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "metaworkspacesequential", "next")
        assert resp.get("result") == "ok"
        # Should land on mw1's default or last-visited — ws11
        state = _get_state(daemon)
        assert state["current_mw"] == 1

    def test_prev_mw_from_mw1_goes_to_mw0(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "1")
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "metaworkspacesequential", "prev")
        assert resp.get("result") == "ok"
        state = _get_state(daemon)
        assert state["current_mw"] == 0

    def test_nowrap_at_mw0_is_noop(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.2)
        _drain_events(event_queue)

        resp = _dispatch(daemon, "metaworkspacesequential", "prev,nowrap")
        result = resp.get("result")
        assert isinstance(result, str) and "noop" in result


class TestExternalNavigation:
    def test_raw_hyprctl_workspace_updates_daemon_state(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        """
        Switching workspace directly via Hyprland IPC (bypassing the daemon)
        should still update the daemon's state via the event listener.
        """
        his, _ = nested_hyprland
        # Navigate to a known position first
        _dispatch(daemon, "metaworkspace", "0")
        time.sleep(0.2)
        _drain_events(event_queue)

        # Directly tell Hyprland to switch to ws 7 (mw0 digit 7)
        _send_hypr_command(his, "dispatch workspace 7")
        # Wait for the workspace event
        assert _wait_for_workspace_event(event_queue, 7)

        # Allow event listener to process it
        time.sleep(0.3)

        state = _get_state(daemon)
        # Daemon should have recorded ws7 as last_visited for mw0
        assert state.get("last_visited", {}).get("0") == 7

    def test_get_state_reflects_current_mw(
        self, daemon, event_queue, nested_hyprland
    ) -> None:
        his, _ = nested_hyprland
        _dispatch(daemon, "metaworkspace", "2")
        time.sleep(0.2)

        state = _get_state(daemon)
        assert state["current_mw"] == 2
