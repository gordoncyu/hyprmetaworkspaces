from __future__ import annotations

from collections.abc import Generator
from typing import NamedTuple
from unittest.mock import MagicMock, patch

import pytest

from hyprmetaworkspaces.config import Config
from hyprmetaworkspaces.dispatchers import (
    DispatchError,
    _check_mw_cross,
    _sequential_wrapin,
    _sequential_wrapout,
    dispatch_metaworkspace,
    dispatch_metaworkspacesequential,
    dispatch_workspace,
    dispatch_workspacesequential,
    handle_dispatch,
)
from hyprmetaworkspaces.state import DaemonState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(
    zero_last: bool = True,
    left: list[int] | None = None,
    right: list[int] | None = None,
) -> Config:
    return Config(
        workspace_zero_last=zero_last,
        surrounding_left=left or [],
        surrounding_right=right or [],
    )


def _state(
    config: Config | None = None,
    current_mw: int = 0,
    last_visited: dict[int, int] | None = None,
) -> DaemonState:
    c = config or _cfg()
    s = DaemonState(config=c, current_mw=current_mw)
    if last_visited:
        s.last_visited.update(last_visited)
    return s


class _DispatchCall(NamedTuple):
    dispatcher: str
    args: str


@pytest.fixture(autouse=True)
def clear_chord_submaps() -> Generator[None, None, None]:
    """Reset the module-level chord submap cache between tests."""
    import hyprmetaworkspaces.dispatchers as d
    d._chord_submaps_created.clear()
    yield
    d._chord_submaps_created.clear()


@pytest.fixture
def mock_ipc() -> Generator[MagicMock, None, None]:
    """Patch all hyprland_ipc calls used by dispatchers. Returns the mock module."""
    with patch("hyprmetaworkspaces.dispatchers.hyprland_ipc") as m:
        m.dispatch.return_value = "ok"
        m.keyword.return_value = "ok"
        m.workspaces_with_windows.return_value = set()
        yield m


@pytest.fixture
def mock_current_ws() -> Generator[MagicMock, None, None]:
    with patch("hyprmetaworkspaces.dispatchers._get_current_hyprland_workspace") as m:
        m.return_value = 1
        yield m


# ---------------------------------------------------------------------------
# _sequential_wrapin
# ---------------------------------------------------------------------------


class TestSequentialWrapin:
    def _all(self, ws: int) -> bool:
        return True

    def _none(self, ws: int) -> bool:
        return False

    def test_next_advances_within_inner(self) -> None:
        c = _cfg()  # band = [1..10]
        result = _sequential_wrapin("next", 0, 3, c, False, self._all)
        assert result == (4, 0)

    def test_prev_retreats_within_inner(self) -> None:
        c = _cfg()
        result = _sequential_wrapin("prev", 0, 5, c, False, self._all)
        assert result == (4, 0)

    def test_next_wraps_from_last_to_first(self) -> None:
        c = _cfg()  # last inner = 10
        result = _sequential_wrapin("next", 0, 10, c, False, self._all)
        assert result == (1, 0)

    def test_prev_wraps_from_first_to_last(self) -> None:
        c = _cfg()  # first inner = 1
        result = _sequential_wrapin("prev", 0, 1, c, False, self._all)
        assert result == (10, 0)

    def test_next_crosses_into_right_surrounding(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        result = _sequential_wrapin("next", 0, 10, c, False, self._all)
        assert result == (21, 0)

    def test_prev_crosses_into_left_surrounding(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        result = _sequential_wrapin("prev", 0, 1, c, False, self._all)
        assert result == (0, 0)

    def test_next_wraps_from_right_surr_to_left_surr(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        result = _sequential_wrapin("next", 0, 22, c, False, self._all)
        assert result == (0, 0)

    def test_skipsurrounding_excludes_surr_workspaces(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        result = _sequential_wrapin("next", 0, 10, c, True, self._all)
        # With skipsurrounding, after 10 the band wraps back to 1
        assert result == (1, 0)

    def test_skipempty_skips_unoccupied(self) -> None:
        c = _cfg()
        occupied = {5}
        result = _sequential_wrapin("next", 0, 1, c, False, lambda ws: ws in occupied)
        assert result == (5, 0)

    def test_skipempty_all_empty_returns_none(self) -> None:
        c = _cfg()
        result = _sequential_wrapin("next", 0, 5, c, False, self._none)
        assert result is None

    def test_wrapin_skipempty_inner_empty_surr_occupied(self) -> None:
        # All inner workspaces empty, but surroundings have windows → land on surrounding
        c = _cfg(left=[0], right=[21, 22])
        occupied = {0, 21}
        result = _sequential_wrapin("next", 0, 10, c, False, lambda ws: ws in occupied)
        assert result == (21, 0)

    def test_exotic_surroundings_next_after_last_inner(self) -> None:
        # left=1011,1013 right=1012,1014, inner mw5=51-60
        # From ws 60 (last inner), next should go to 1012 (first right surr), not 1011 (left surr)
        c = Config(workspace_zero_last=True, surrounding_left=[1011, 1013], surrounding_right=[1012, 1014])
        result = _sequential_wrapin("next", 5, 60, c, False, self._all)
        assert result == (1012, 5)

    def test_exotic_surroundings_prev_before_first_inner(self) -> None:
        # From ws 51 (first inner), prev should go to 1013 (last left surr)
        c = Config(workspace_zero_last=True, surrounding_left=[1011, 1013], surrounding_right=[1012, 1014])
        result = _sequential_wrapin("prev", 5, 51, c, False, self._all)
        assert result == (1013, 5)

    def test_current_ws_not_in_band_next_goes_to_first(self) -> None:
        # current_ws not in band → treated as before the band
        c = _cfg()
        result = _sequential_wrapin("next", 0, 999, c, False, self._all)
        assert result == (1, 0)


# ---------------------------------------------------------------------------
# _sequential_wrapout
# ---------------------------------------------------------------------------


class TestSequentialWrapout:
    def _all(self, ws: int) -> bool:
        return True

    def test_next_within_band(self) -> None:
        c = _cfg()
        s = _state(config=c)
        result = _sequential_wrapout("next", 0, 3, c, False, self._all, s)
        assert result == (4, 0)

    def test_prev_within_band(self) -> None:
        c = _cfg()
        s = _state(config=c)
        result = _sequential_wrapout("prev", 0, 5, c, False, self._all, s)
        assert result == (4, 0)

    def test_next_crosses_mw_boundary(self) -> None:
        c = _cfg(right=[21, 22])  # upper = mw1
        s = _state(config=c)
        # From ws 10 (last inner of mw0), next enters the surrounding then crosses mw
        result = _sequential_wrapout("next", 0, 10, c, False, self._all, s)
        assert result == (21, 0)

    def test_next_from_last_surr_crosses_to_mw1(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        s = _state(config=c)
        # From ws 22 (last right surr of mw0), next enters left surr of mw1 → mw changes to 1
        result = _sequential_wrapout("next", 0, 22, c, False, self._all, s)
        assert result == (0, 1)

    def test_next_wraps_from_last_mw_to_mw0(self) -> None:
        c = _cfg(right=[21, 22])  # valid mws = 0,1; mw1 band ends at 22
        s = _state(config=c, current_mw=1)
        # From ws 22 (last in mw1's band), next wraps to first of mw0
        result = _sequential_wrapout("next", 1, 22, c, False, self._all, s)
        assert result == (1, 0)

    def test_prev_wraps_from_mw0_to_last_mw(self) -> None:
        c = _cfg(right=[21, 22])  # mw1 band ends at 22
        s = _state(config=c, current_mw=0)
        # From ws 1 (first in mw0's band), prev wraps to last of mw1 (which is 22)
        result = _sequential_wrapout("prev", 0, 1, c, False, self._all, s)
        assert result == (22, 1)

    def test_skipsurrounding_skips_to_next_mw_inner(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        s = _state(config=c)
        # From ws 10 (last inner of mw0) with skipsurrounding, next jumps straight to mw1's first inner
        result = _sequential_wrapout("next", 0, 10, c, True, self._all, s)
        assert result == (11, 1)

    def test_skipempty_finds_candidate_later_in_same_band(self) -> None:
        # From ws 1, only ws 5 has windows — must scan the full remaining band, not just ws 2.
        c = _cfg()
        s = _state(config=c)
        occupied = {5}
        result = _sequential_wrapout("next", 0, 1, c, False, lambda ws: ws in occupied, s)
        assert result == (5, 0)

    def test_skipempty_skips_entire_empty_mw(self) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c)
        occupied = set(range(11, 21))  # only mw1 has windows
        result = _sequential_wrapout("next", 0, 10, c, False, lambda ws: ws in occupied, s)
        assert result is not None
        assert result[1] == 1  # ended up in mw1

    def test_all_empty_bounded_returns_none(self) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c)
        result = _sequential_wrapout("next", 0, 5, c, False, lambda ws: False, s)
        assert result is None

    def test_all_empty_unbounded_returns_none(self) -> None:
        # Without a right surrounding (unbounded), must still terminate.
        c = _cfg()
        s = _state(config=c)
        result = _sequential_wrapout("next", 0, 5, c, False, lambda ws: False, s)
        assert result is None


# ---------------------------------------------------------------------------
# _check_mw_cross
# ---------------------------------------------------------------------------


class TestCheckMwCross:
    def test_no_cross_mid_band(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        assert _check_mw_cross(5, 6, 0, c, "next") == 0

    def test_cross_right_to_left_increments_mw(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        # last right surr (22) → first left surr (0) when going next → mw+1
        assert _check_mw_cross(22, 0, 0, c, "next") == 1

    def test_cross_left_to_right_decrements_mw(self) -> None:
        c = _cfg(left=[0], right=[21, 22])
        # first left surr (0) → last right surr (22) when going prev → mw-1
        assert _check_mw_cross(0, 22, 1, c, "prev") == 0

    def test_cross_next_wraps_at_upper_bound(self) -> None:
        c = _cfg(left=[0], right=[21, 22])  # upper = mw1
        # from last mw (1), crossing next wraps to mw0
        assert _check_mw_cross(22, 0, 1, c, "next") == 0

    def test_cross_prev_wraps_from_mw0(self) -> None:
        c = _cfg(left=[0], right=[21, 22])  # upper = mw1
        assert _check_mw_cross(0, 22, 0, c, "prev") == 1

    def test_no_surrounding_never_crosses(self) -> None:
        c = _cfg()
        assert _check_mw_cross(10, 11, 0, c, "next") == 0

    def test_only_left_surrounding_no_cross(self) -> None:
        c = _cfg(left=[0])
        assert _check_mw_cross(0, 1, 0, c, "next") == 0


# ---------------------------------------------------------------------------
# dispatch_workspace
# ---------------------------------------------------------------------------


class TestDispatchWorkspace:
    def test_digit_1_in_mw0(self, mock_ipc: MagicMock) -> None:
        s = _state()
        dispatch_workspace("1", s)
        mock_ipc.dispatch.assert_called_once_with("workspace", "1")

    def test_digit_0_maps_to_n10_zero_last(self, mock_ipc: MagicMock) -> None:
        s = _state()
        dispatch_workspace("0", s)
        mock_ipc.dispatch.assert_called_once_with("workspace", "10")

    def test_digit_5_in_mw2(self, mock_ipc: MagicMock) -> None:
        s = _state(current_mw=2)
        dispatch_workspace("5", s)
        mock_ipc.dispatch.assert_called_once_with("workspace", "25")

    def test_updates_last_visited(self, mock_ipc: MagicMock) -> None:
        s = _state()
        dispatch_workspace("3", s)
        assert s.last_visited.get(0) == 3

    def test_invalid_digit_too_large(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_workspace("10", s)

    def test_invalid_digit_negative(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_workspace("-1", s)

    def test_non_integer_raises(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_workspace("abc", s)

    def test_too_many_args_raises(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_workspace("1,2", s)


# ---------------------------------------------------------------------------
# dispatch_metaworkspace
# ---------------------------------------------------------------------------


class TestDispatchMetaworkspace:
    def test_switches_to_default_if_never_visited(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])  # upper = mw1
        s = _state(config=c)
        dispatch_metaworkspace("1", s)
        mock_ipc.dispatch.assert_called_with("workspace", "11")
        assert s.current_mw == 1

    def test_switches_to_last_visited(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, last_visited={1: 17})
        dispatch_metaworkspace("1", s)
        mock_ipc.dispatch.assert_called_with("workspace", "17")

    def test_invalid_mw_is_noop(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])  # upper = mw1
        s = _state(config=c)
        result = dispatch_metaworkspace("2", s)
        assert result == "noop: invalid metaworkspace"
        mock_ipc.dispatch.assert_not_called()

    def test_chordworkspace_dispatches_submap(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c)
        dispatch_metaworkspace("1,chordworkspace", s)
        # Should dispatch to the named submap
        submap_calls = [call for call in mock_ipc.dispatch.call_args_list if call[0][0] == "submap"]
        assert len(submap_calls) == 1
        assert "hyprmetaworkspaces_chord_mw1" in submap_calls[0][0][1]

    def test_chord_submap_created_only_once(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c)
        dispatch_metaworkspace("1,chordworkspace", s)
        keyword_count_first = mock_ipc.keyword.call_count
        dispatch_metaworkspace("1,chordworkspace", s)
        # Second call should not add more keyword calls
        assert mock_ipc.keyword.call_count == keyword_count_first

    def test_non_integer_raises(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_metaworkspace("abc", s)

    def test_empty_args_raises(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_metaworkspace("", s)


# ---------------------------------------------------------------------------
# dispatch_workspacesequential
# ---------------------------------------------------------------------------


class TestDispatchWorkspaceSequential:
    def test_next_advances_to_next_ws(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 3
        c = _cfg()
        s = _state(config=c)
        dispatch_workspacesequential("next", s)
        mock_ipc.dispatch.assert_called_with("workspace", "4")

    def test_prev_retreats_to_prev_ws(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 5
        c = _cfg()
        s = _state(config=c)
        dispatch_workspacesequential("prev", s)
        mock_ipc.dispatch.assert_called_with("workspace", "4")

    def test_next_crosses_into_surrounding(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 10  # last inner of mw0
        c = _cfg(left=[0], right=[21, 22])
        s = _state(config=c)
        dispatch_workspacesequential("next", s)
        mock_ipc.dispatch.assert_called_with("workspace", "21")

    def test_next_from_last_right_surr_changes_mw(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 22  # last right surr of mw0
        c = _cfg(left=[0], right=[21, 22])
        s = _state(config=c, current_mw=0)
        dispatch_workspacesequential("next", s)
        # Should land on first left surr of mw1 and update current_mw to 1
        mock_ipc.dispatch.assert_called_with("workspace", "0")
        assert s.current_mw == 1

    def test_skipsurrounding_skips_to_next_inner(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 10
        c = _cfg(left=[0], right=[21, 22])
        s = _state(config=c)
        dispatch_workspacesequential("next,skipsurrounding", s)
        # Jumps straight to mw1's first inner
        mock_ipc.dispatch.assert_called_with("workspace", "11")

    def test_skipempty_skips_unoccupied(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 1
        mock_ipc.workspaces_with_windows.return_value = {5}
        c = _cfg()
        s = _state(config=c)
        dispatch_workspacesequential("next,skipempty", s)
        mock_ipc.dispatch.assert_called_with("workspace", "5")

    def test_wrapin_wraps_within_band(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 10
        c = _cfg()  # no surrounding; last inner = 10
        s = _state(config=c)
        dispatch_workspacesequential("next,wrapin", s)
        # Wraps back to first inner
        mock_ipc.dispatch.assert_called_with("workspace", "1")
        assert s.current_mw == 0  # stays in same mw

    def test_all_empty_with_skipempty_is_noop(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 5
        mock_ipc.workspaces_with_windows.return_value = set()
        c = _cfg()
        s = _state(config=c)
        result = dispatch_workspacesequential("next,skipempty", s)
        assert result == "noop"
        mock_ipc.dispatch.assert_not_called()

    def test_invalid_direction_raises(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_workspacesequential("sideways", s)

    def test_updates_state_last_visited(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        mock_current_ws.return_value = 3
        c = _cfg()
        s = _state(config=c)
        dispatch_workspacesequential("next", s)
        assert s.last_visited.get(0) == 4

    def test_surrounding_target_does_not_update_last_visited(
        self, mock_ipc: MagicMock, mock_current_ws: MagicMock
    ) -> None:
        mock_current_ws.return_value = 10
        c = _cfg(left=[0], right=[21, 22])
        s = _state(config=c)
        dispatch_workspacesequential("next", s)
        # ws 21 is a surrounding — should not update last_visited
        assert 0 not in s.last_visited


# ---------------------------------------------------------------------------
# dispatch_metaworkspacesequential
# ---------------------------------------------------------------------------


class TestDispatchMetaworkspaceSequential:
    def test_next_switches_to_mw1(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])  # valid mws = 0,1
        s = _state(config=c, current_mw=0)
        dispatch_metaworkspacesequential("next", s)
        assert s.current_mw == 1
        mock_ipc.dispatch.assert_called_with("workspace", "11")

    def test_prev_switches_to_mw0(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, current_mw=1)
        dispatch_metaworkspacesequential("prev", s)
        assert s.current_mw == 0
        mock_ipc.dispatch.assert_called_with("workspace", "1")

    def test_next_from_last_mw_wraps_to_mw0(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, current_mw=1)
        dispatch_metaworkspacesequential("next", s)
        assert s.current_mw == 0

    def test_prev_from_mw0_wraps_to_last_mw(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, current_mw=0)
        dispatch_metaworkspacesequential("prev", s)
        assert s.current_mw == 1

    def test_nowrap_at_last_mw_is_noop(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, current_mw=1)
        result = dispatch_metaworkspacesequential("next,nowrap", s)
        assert result == "noop"
        mock_ipc.dispatch.assert_not_called()

    def test_nowrap_at_mw0_prev_is_noop(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, current_mw=0)
        result = dispatch_metaworkspacesequential("prev,nowrap", s)
        assert result == "noop"

    def test_lands_on_last_visited(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, current_mw=0, last_visited={1: 17})
        dispatch_metaworkspacesequential("next", s)
        mock_ipc.dispatch.assert_called_with("workspace", "17")

    def test_skipempty_skips_mw_with_no_windows(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[31])  # valid mws = 0,1,2
        s = _state(config=c, current_mw=0)
        # mw1 is empty, mw2 has windows
        mock_ipc.workspaces_with_windows.return_value = set(range(21, 31))
        dispatch_metaworkspacesequential("next,skipempty", s)
        assert s.current_mw == 2

    def test_skipempty_all_empty_is_noop(self, mock_ipc: MagicMock) -> None:
        c = _cfg(right=[21, 22])
        s = _state(config=c, current_mw=0)
        mock_ipc.workspaces_with_windows.return_value = set()
        result = dispatch_metaworkspacesequential("next,skipempty", s)
        assert result == "noop"

    def test_skipempty_all_empty_unbounded_is_noop(self, mock_ipc: MagicMock) -> None:
        # Without a right surrounding (unbounded), must still terminate.
        c = _cfg()
        s = _state(config=c, current_mw=0)
        mock_ipc.workspaces_with_windows.return_value = set()
        result = dispatch_metaworkspacesequential("next,skipempty", s)
        assert result == "noop"

    def test_surrounding_windows_dont_count_for_skipempty(self, mock_ipc: MagicMock) -> None:
        c = _cfg(left=[0], right=[21, 22])
        s = _state(config=c, current_mw=0)
        # mw1's inner workspaces are empty; only surrounding has windows
        mock_ipc.workspaces_with_windows.return_value = {0, 21, 22}
        result = dispatch_metaworkspacesequential("next,skipempty", s)
        # mw1 has no inner windows → skipped → noop (only 2 mws)
        assert result == "noop"

    def test_invalid_direction_raises(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError):
            dispatch_metaworkspacesequential("left", s)


# ---------------------------------------------------------------------------
# handle_dispatch (routing)
# ---------------------------------------------------------------------------


class TestHandleDispatch:
    def test_routes_known_dispatchers(self, mock_ipc: MagicMock, mock_current_ws: MagicMock) -> None:
        s = _state()
        for name in ("workspace", "metaworkspace", "workspacesequential", "metaworkspacesequential"):
            # Just check it doesn't raise DispatchError for unknown method
            try:
                handle_dispatch(name, "next" if "sequential" in name else "1", s)
            except DispatchError as exc:
                # Only argument errors are acceptable (not unknown dispatcher errors)
                assert "Unknown dispatcher" not in str(exc)

    def test_unknown_dispatcher_raises(self, mock_ipc: MagicMock) -> None:
        s = _state()
        with pytest.raises(DispatchError, match="Unknown dispatcher"):
            handle_dispatch("teleport", "1", s)
