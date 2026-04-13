from __future__ import annotations

from . import hyprland_ipc
from .config import Config
from .state import DaemonState


class DispatchError(Exception):
    pass


# Tracks which chord submaps have been created (per mw index)
_chord_submaps_created: set[tuple[int, str]] = set()


def _go_to_workspace(ws: int, state: DaemonState) -> None:
    hyprland_ipc.dispatch("workspace", str(ws))
    state.record_visit(ws)


def _ensure_chord_submap(mw: int, config: Config, variant: str = "workspace") -> str:
    """
    Lazily create a Hyprland submap for chord-selecting inner workspaces of mw.

    variant controls what action each digit performs:
      - "workspace": dispatch workspace <digit> (navigate)
      - "movetoworkspace": movetoworkspace <abs_ws> (move window + follow)
      - "movetoworkspacesilent": movetoworkspacesilent <abs_ws> (move window, stay)
    """
    key = (mw, variant)
    submap_name = f"hyprmetaworkspaces_chord_{variant}_mw{mw}"
    if key in _chord_submaps_created:
        return submap_name

    hyprland_ipc.keyword("submap", submap_name)
    for digit in range(0, 10):
        ws = config.digit_to_workspace(mw, digit)
        if variant == "workspace":
            hyprland_ipc.keyword("bind", f", {digit}, exec, hyprmwctl dispatch workspace {digit}")
        else:
            # movetoworkspace / movetoworkspacesilent use absolute ws numbers
            # via Hyprland's native dispatcher directly
            hyprland_ipc.keyword("bind", f", {digit}, exec, hyprctl dispatch {variant} {ws}")
        hyprland_ipc.keyword("bind", f", {digit}, submap, reset")
    hyprland_ipc.keyword("bind", ", catchall, submap, reset")
    hyprland_ipc.keyword("submap", "reset")

    _chord_submaps_created.add(key)
    return submap_name


def dispatch_workspace(args: str, state: DaemonState) -> str:
    """
    workspace <digit>
    Navigate to the inner workspace identified by digit (0-9) within the current mw.
    """
    parts = [p.strip() for p in args.split(",")]
    if len(parts) != 1:
        raise DispatchError(f"workspace expects 1 argument, got {len(parts)}")
    try:
        digit = int(parts[0])
    except ValueError:
        raise DispatchError(f"workspace argument must be a digit 0-9, got {parts[0]!r}")
    if digit < 0 or digit > 9:
        raise DispatchError(f"workspace digit must be 0-9, got {digit}")

    ws = state.config.digit_to_workspace(state.current_mw, digit)
    _go_to_workspace(ws, state)
    return "ok"


def _parse_workspace_digit(args: str, name: str) -> int:
    parts = [p.strip() for p in args.split(",")]
    if len(parts) != 1:
        raise DispatchError(f"{name} expects 1 argument, got {len(parts)}")
    try:
        digit = int(parts[0])
    except ValueError:
        raise DispatchError(f"{name} argument must be a digit 0-9, got {parts[0]!r}")
    if digit < 0 or digit > 9:
        raise DispatchError(f"{name} digit must be 0-9, got {digit}")
    return digit


def dispatch_movetoworkspace(args: str, state: DaemonState) -> str:
    """
    movetoworkspace <digit>
    Move the focused window to workspace digit (0-9) in the current mw and follow it.
    """
    digit = _parse_workspace_digit(args, "movetoworkspace")
    ws = state.config.digit_to_workspace(state.current_mw, digit)
    hyprland_ipc.dispatch("movetoworkspace", str(ws))
    state.record_visit(ws)
    return "ok"


def dispatch_movetoworkspacesilent(args: str, state: DaemonState) -> str:
    """
    movetoworkspacesilent <digit>
    Move the focused window to workspace digit (0-9) in the current mw without following.
    """
    digit = _parse_workspace_digit(args, "movetoworkspacesilent")
    ws = state.config.digit_to_workspace(state.current_mw, digit)
    hyprland_ipc.dispatch("movetoworkspacesilent", str(ws))
    return "ok"


def dispatch_metaworkspace(args: str, state: DaemonState) -> str:
    """
    metaworkspace <n>[,chordworkspace]
    Switch to metaworkspace n. With chordworkspace, enter a submap for inner ws selection.
    """
    parts = [p.strip() for p in args.split(",")]
    if not parts or not parts[0]:
        raise DispatchError("metaworkspace requires at least one argument")

    try:
        mw = int(parts[0])
    except ValueError:
        raise DispatchError(f"metaworkspace first argument must be an integer, got {parts[0]!r}")

    chord = len(parts) >= 2 and parts[1] == "chordworkspace"

    if not state.config.is_valid_mw(mw):
        return "noop: invalid metaworkspace"

    if chord:
        submap = _ensure_chord_submap(mw, state.config, "workspace")
        # Switch to mw first (to last visited), then enter submap
        ws = state.get_last_visited(mw)
        _go_to_workspace(ws, state)
        hyprland_ipc.dispatch("submap", submap)
    else:
        ws = state.get_last_visited(mw)
        _go_to_workspace(ws, state)

    return "ok"


def dispatch_workspacesequential(args: str, state: DaemonState) -> str:
    """
    workspacesequential <next|prev>[,skipempty][,skipsurrounding][,wrapin|wrapout]
    Step through workspaces in the conceptual band order.
    """
    parts = [p.strip() for p in args.split(",")]
    if not parts or parts[0] not in ("next", "prev"):
        raise DispatchError("workspacesequential first argument must be 'next' or 'prev'")

    direction = parts[0]
    flags = set(parts[1:])

    skip_empty = "skipempty" in flags
    skip_surrounding = "skipsurrounding" in flags
    wrap_in = "wrapin" in flags
    # wrapout is default; wrapin overrides

    config = state.config
    mw = state.current_mw

    # Current position in the global sequence
    current_ws_int = _get_current_hyprland_workspace()

    occupied = hyprland_ipc.workspaces_with_windows() if skip_empty else None

    def is_candidate(ws: int) -> bool:
        if skip_empty and occupied is not None and ws not in occupied:
            return False
        return True

    if wrap_in:
        result = _sequential_wrapin(direction, mw, current_ws_int, config, skip_surrounding, is_candidate)
    else:
        result = _sequential_wrapout(direction, mw, current_ws_int, config, skip_surrounding, is_candidate, state)

    if result is None:
        return "noop"

    target_ws, target_mw = result
    state.current_mw = target_mw
    if not config.is_surrounding(target_ws):
        mw_of_target = config.workspace_to_mw(target_ws)
        if mw_of_target is not None:
            state.last_visited[mw_of_target] = target_ws
    hyprland_ipc.dispatch("workspace", str(target_ws))
    return "ok"


def dispatch_metaworkspacesequential(args: str, state: DaemonState) -> str:
    """
    metaworkspacesequential <next|prev>[,skipempty][,wrap|nowrap]
    Step through metaworkspaces.
    """
    parts = [p.strip() for p in args.split(",")]
    if not parts or parts[0] not in ("next", "prev"):
        raise DispatchError("metaworkspacesequential first argument must be 'next' or 'prev'")

    direction = parts[0]
    flags = set(parts[1:])

    skip_empty = "skipempty" in flags
    no_wrap = "nowrap" in flags

    config = state.config
    upper = config.upper_bound_mw()
    # When unbounded, cap at current_mw+100 so the wrap cycle terminates.
    effective_upper = upper if upper is not None else (state.current_mw + 100)

    occupied = hyprland_ipc.workspaces_with_windows() if skip_empty else None

    def mw_has_windows(mw: int) -> bool:
        if occupied is None:
            return True
        return any(ws in occupied for ws in config.inner_workspaces(mw))

    step = 1 if direction == "next" else -1
    candidate = state.current_mw + step

    while True:
        if candidate < 0 or candidate > effective_upper:
            if no_wrap:
                return "noop"
            # wrap
            candidate = 0 if direction == "next" else effective_upper
            if not config.is_valid_mw(candidate):
                return "noop"

        if not config.is_valid_mw(candidate):
            return "noop"

        if skip_empty and not mw_has_windows(candidate):
            if candidate == state.current_mw:
                return "noop"
            candidate += step
            continue

        break

    ws = state.get_last_visited(candidate)
    state.current_mw = candidate
    state.last_visited[candidate] = ws
    hyprland_ipc.dispatch("workspace", str(ws))
    return "ok"


def _get_current_hyprland_workspace() -> int:
    """Query Hyprland for the currently active workspace ID."""
    import json
    raw = hyprland_ipc.send_command("j/activeworkspace")
    data: dict[str, object] = json.loads(raw)
    ws_id = data.get("id")
    if isinstance(ws_id, int):
        return ws_id
    return 1


def _build_full_sequence(config: Config, start_mw: int, direction: str, max_steps: int) -> list[tuple[int, int]]:
    """
    Build an ordered list of (workspace, metaworkspace) tuples for sequential navigation.
    Generates the band for start_mw, then neighboring mws up to max_steps away.
    Returns list in navigation direction order.
    """
    step = 1 if direction == "next" else -1
    seq: list[tuple[int, int]] = []

    mw = start_mw
    for _ in range(max_steps + 1):
        if not config.is_valid_mw(mw):
            break
        band = config.band(mw)
        for ws in band:
            seq.append((ws, mw))
        mw += step

    return seq


def _sequential_wrapin(
    direction: str,
    mw: int,
    current_ws: int,
    config: Config,
    skip_surrounding: bool,
    is_candidate: "callable[[int], bool]",
) -> tuple[int, int] | None:
    """Wrap within the current mw's band."""
    band = config.band(mw)
    if skip_surrounding:
        band = [ws for ws in band if not config.is_surrounding(ws)]

    candidates = [ws for ws in band if is_candidate(ws)]
    if not candidates:
        return None

    # Use band position for ordering, not numeric value, so exotic surroundings
    # (e.g. left=1011 with inner=51-60) navigate in the correct conceptual order.
    band_pos: dict[int, int] = {ws: i for i, ws in enumerate(band)}
    current_pos: int = band_pos.get(current_ws, -1)

    if direction == "next":
        after = [ws for ws in candidates if band_pos[ws] > current_pos]
        target = after[0] if after else candidates[0]
    else:
        before = [ws for ws in candidates if band_pos[ws] < current_pos]
        target = before[-1] if before else candidates[-1]

    return (target, mw)


def _sequential_wrapout(
    direction: str,
    mw: int,
    current_ws: int,
    config: Config,
    skip_surrounding: bool,
    is_candidate: "callable[[int], bool]",
    state: DaemonState,
) -> tuple[int, int] | None:
    """Navigate forward/backward through mws, wrapping to the opposite end."""
    upper = config.upper_bound_mw()
    # When unbounded, cap at current_mw+100 so the wrap cycle terminates.
    effective_upper = upper if upper is not None else (mw + 100)

    def mw_band(m: int) -> list[int]:
        band = config.band(m)
        if skip_surrounding:
            band = [ws for ws in band if not config.is_surrounding(ws)]
        return band

    current_band = mw_band(mw)

    try:
        idx = current_band.index(current_ws)
    except ValueError:
        # current_ws not in band (e.g. on a surrounding filtered out by skipsurrounding)
        idx = len(current_band) if direction == "next" else -1

    # Scan the remaining portion of the current band before crossing to another mw.
    if direction == "next":
        remaining = current_band[idx + 1:]
        for ws in remaining:
            if is_candidate(ws):
                return (ws, _check_mw_cross(current_ws, ws, mw, config, direction))
    else:
        remaining = current_band[:idx]
        for ws in reversed(remaining):
            if is_candidate(ws):
                return (ws, _check_mw_cross(current_ws, ws, mw, config, direction))

    # Nothing left in current band — cross into neighboring mws, wrapping as needed.
    step = 1 if direction == "next" else -1
    next_mw = mw + step

    visited_mws: set[int] = set()
    while True:
        if next_mw < 0 or next_mw > effective_upper:
            next_mw = 0 if direction == "next" else effective_upper

        if next_mw in visited_mws:
            return None  # full cycle with no candidates
        visited_mws.add(next_mw)

        if not config.is_valid_mw(next_mw):
            return None

        band = mw_band(next_mw)
        candidates = [ws for ws in band if is_candidate(ws)]
        if candidates:
            target = candidates[0] if direction == "next" else candidates[-1]
            return (target, next_mw)

        next_mw += step


def _check_mw_cross(from_ws: int, to_ws: int, current_mw: int, config: Config, direction: str) -> int:
    """
    Determine the new metaworkspace when moving from from_ws to to_ws.
    Crossing from the last right surrounding to the first left surrounding increments mw.
    Crossing from the first left surrounding to the last right surrounding decrements mw.
    """
    surr_left = config.surrounding_left
    surr_right = config.surrounding_right

    if not surr_left or not surr_right:
        return current_mw

    if direction == "next":
        # Crossing: last right surr → first left surr means increment mw
        if from_ws == surr_right[-1] and to_ws == surr_left[0]:
            upper = config.upper_bound_mw()
            new_mw = current_mw + 1
            if upper is not None and new_mw > upper:
                new_mw = 0
            return new_mw
    else:
        # Crossing: first left surr → last right surr means decrement mw
        if from_ws == surr_left[0] and to_ws == surr_right[-1]:
            new_mw = current_mw - 1
            if new_mw < 0:
                upper = config.upper_bound_mw()
                new_mw = upper if upper is not None else 0
            return new_mw

    return current_mw


def _dispatch_movetometaworkspace(args: str, state: DaemonState, silent: bool) -> str:
    """
    movetometaworkspace[silent] <n>[,chordworkspace]
    Move the focused window to metaworkspace n.
    Non-silent follows the window; silent stays on current workspace.
    With chordworkspace, enter a submap for digit selection (stay put until chord resolves).
    """
    parts = [p.strip() for p in args.split(",")]
    if not parts or not parts[0]:
        raise DispatchError("movetometaworkspace requires at least one argument")

    try:
        mw = int(parts[0])
    except ValueError:
        raise DispatchError(f"movetometaworkspace first argument must be an integer, got {parts[0]!r}")

    chord = len(parts) >= 2 and parts[1] == "chordworkspace"
    hypr_dispatcher = "movetoworkspacesilent" if silent else "movetoworkspace"

    if not state.config.is_valid_mw(mw):
        return "noop: invalid metaworkspace"

    if chord:
        submap = _ensure_chord_submap(mw, state.config, hypr_dispatcher)
        # Stay where we are — don't switch mw until chord resolves
        hyprland_ipc.dispatch("submap", submap)
    else:
        ws = state.get_last_visited(mw)
        hyprland_ipc.dispatch(hypr_dispatcher, str(ws))
        if not silent:
            state.record_visit(ws)

    return "ok"


def dispatch_movetometaworkspace(args: str, state: DaemonState) -> str:
    return _dispatch_movetometaworkspace(args, state, silent=False)


def dispatch_movetometaworkspacesilent(args: str, state: DaemonState) -> str:
    return _dispatch_movetometaworkspace(args, state, silent=True)


DISPATCHER_MAP: dict[str, "callable[[str, DaemonState], str]"] = {
    "workspace": dispatch_workspace,
    "movetoworkspace": dispatch_movetoworkspace,
    "movetoworkspacesilent": dispatch_movetoworkspacesilent,
    "metaworkspace": dispatch_metaworkspace,
    "movetometaworkspace": dispatch_movetometaworkspace,
    "movetometaworkspacesilent": dispatch_movetometaworkspacesilent,
    "workspacesequential": dispatch_workspacesequential,
    "metaworkspacesequential": dispatch_metaworkspacesequential,
}


def handle_dispatch(name: str, args: str, state: DaemonState) -> str:
    fn = DISPATCHER_MAP.get(name)
    if fn is None:
        raise DispatchError(f"Unknown dispatcher: {name!r}")
    return fn(args, state)
