from __future__ import annotations

import pytest

from hyprmetaworkspaces.config import Config
from hyprmetaworkspaces.state import DaemonState


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


@pytest.fixture
def bounded_config() -> Config:
    return _cfg(zero_last=True, left=[0], right=[21, 22])


# ---------------------------------------------------------------------------
# get_last_visited
# ---------------------------------------------------------------------------


class TestGetLastVisited:
    def test_default_zero_last(self) -> None:
        s = DaemonState(config=_cfg(zero_last=True))
        assert s.get_last_visited(0) == 1
        assert s.get_last_visited(2) == 21

    def test_default_zero_first(self) -> None:
        s = DaemonState(config=_cfg(zero_last=False))
        assert s.get_last_visited(0) == 0
        assert s.get_last_visited(1) == 10

    def test_returns_recorded_value(self) -> None:
        s = DaemonState(config=_cfg())
        s.last_visited[0] = 5
        assert s.get_last_visited(0) == 5


# ---------------------------------------------------------------------------
# record_visit
# ---------------------------------------------------------------------------


class TestRecordVisit:
    def test_inner_ws_updates_mw_and_last_visited(self, bounded_config: Config) -> None:
        s = DaemonState(config=bounded_config)
        s.record_visit(5)
        assert s.current_mw == 0
        assert s.last_visited[0] == 5

    def test_inner_ws_of_mw1_switches_current_mw(self, bounded_config: Config) -> None:
        s = DaemonState(config=bounded_config)
        s.record_visit(15)
        assert s.current_mw == 1
        assert s.last_visited[1] == 15

    def test_right_surrounding_does_not_change_mw(self, bounded_config: Config) -> None:
        s = DaemonState(config=bounded_config, current_mw=1)
        s.last_visited[1] = 15
        s.record_visit(21)
        assert s.current_mw == 1
        assert s.last_visited.get(1) == 15

    def test_left_surrounding_ws0_does_not_change_mw(self, bounded_config: Config) -> None:
        s = DaemonState(config=bounded_config, current_mw=0)
        s.record_visit(0)
        assert s.current_mw == 0
        assert 0 not in s.last_visited

    def test_ws_in_blocked_mw_does_not_update_state(self, bounded_config: Config) -> None:
        # mw2 is blocked; ws 25 falls in mw2
        s = DaemonState(config=bounded_config, current_mw=0)
        s.record_visit(25)
        assert s.current_mw == 0
        assert 2 not in s.last_visited

    def test_external_ws_beyond_bound_ignored(self) -> None:
        config = _cfg(zero_last=True, right=[21])
        s = DaemonState(config=config, current_mw=0)
        # ws 999 is in mw99, which is > upper_bound (mw1)
        s.record_visit(999)
        assert s.current_mw == 0

    def test_successive_visits_update_last_visited(self, bounded_config: Config) -> None:
        s = DaemonState(config=bounded_config)
        s.record_visit(3)
        s.record_visit(7)
        assert s.last_visited[0] == 7

    def test_ws0_not_surrounding_not_in_any_mw(self) -> None:
        # ws 0 with zero_last=True and no surrounding → workspace_to_mw(0) = None → no-op
        s = DaemonState(config=_cfg(zero_last=True))
        s.record_visit(0)
        assert s.current_mw == 0
        assert not s.last_visited
