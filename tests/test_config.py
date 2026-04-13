from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hyprmetaworkspaces.config import Config, ConfigError, _validate, load_config


def zl(**kwargs: object) -> Config:
    """workspace_zero_last=True config, with optional surrounding overrides."""
    return Config(
        workspace_zero_last=True,
        surrounding_left=kwargs.get("left", []),  # type: ignore[arg-type]
        surrounding_right=kwargs.get("right", []),  # type: ignore[arg-type]
    )


def nzl(**kwargs: object) -> Config:
    """workspace_zero_last=False config."""
    return Config(
        workspace_zero_last=False,
        surrounding_left=kwargs.get("left", []),  # type: ignore[arg-type]
        surrounding_right=kwargs.get("right", []),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# digit_to_workspace
# ---------------------------------------------------------------------------


class TestDigitToWorkspace:
    def test_zero_last_digits_1_to_9(self) -> None:
        c = zl()
        for mw in range(3):
            for d in range(1, 10):
                assert c.digit_to_workspace(mw, d) == mw * 10 + d

    def test_zero_last_digit_0_maps_to_n10(self) -> None:
        c = zl()
        assert c.digit_to_workspace(0, 0) == 10
        assert c.digit_to_workspace(1, 0) == 20
        assert c.digit_to_workspace(2, 0) == 30

    def test_zero_first_all_digits(self) -> None:
        c = nzl()
        for mw in range(3):
            for d in range(0, 10):
                assert c.digit_to_workspace(mw, d) == mw * 10 + d


# ---------------------------------------------------------------------------
# inner_workspaces
# ---------------------------------------------------------------------------


class TestInnerWorkspaces:
    def test_zero_last_mw0(self) -> None:
        assert zl().inner_workspaces(0) == list(range(1, 11))

    def test_zero_last_mw1(self) -> None:
        assert zl().inner_workspaces(1) == list(range(11, 21))

    def test_zero_first_mw0(self) -> None:
        assert nzl().inner_workspaces(0) == list(range(0, 10))

    def test_zero_first_mw2(self) -> None:
        assert nzl().inner_workspaces(2) == list(range(20, 30))

    def test_always_ten_workspaces(self) -> None:
        c = zl()
        for mw in range(5):
            assert len(c.inner_workspaces(mw)) == 10


# ---------------------------------------------------------------------------
# workspace_to_mw
# ---------------------------------------------------------------------------


class TestWorkspaceToMw:
    def test_zero_last_basic(self) -> None:
        c = zl()
        assert c.workspace_to_mw(1) == 0
        assert c.workspace_to_mw(10) == 0
        assert c.workspace_to_mw(11) == 1
        assert c.workspace_to_mw(20) == 1
        assert c.workspace_to_mw(21) == 2

    def test_zero_last_ws0_is_none(self) -> None:
        assert zl().workspace_to_mw(0) is None

    def test_zero_last_negative_is_none(self) -> None:
        assert zl().workspace_to_mw(-1) is None

    def test_zero_first_basic(self) -> None:
        c = nzl()
        assert c.workspace_to_mw(0) == 0
        assert c.workspace_to_mw(9) == 0
        assert c.workspace_to_mw(10) == 1
        assert c.workspace_to_mw(19) == 1

    def test_zero_first_negative_is_none(self) -> None:
        assert nzl().workspace_to_mw(-1) is None

    def test_surrounding_ws_still_maps_to_its_numeric_mw(self) -> None:
        # workspace_to_mw doesn't know about surrounding designation;
        # ws 21 falls numerically within mw2 (21-30)
        c = zl(left=[0], right=[21, 22])
        assert c.workspace_to_mw(21) == 2
        assert c.workspace_to_mw(0) is None


# ---------------------------------------------------------------------------
# is_surrounding
# ---------------------------------------------------------------------------


class TestIsSurrounding:
    def test_left_member(self) -> None:
        c = zl(left=[0], right=[21, 22])
        assert c.is_surrounding(0)

    def test_right_members(self) -> None:
        c = zl(left=[0], right=[21, 22])
        assert c.is_surrounding(21)
        assert c.is_surrounding(22)

    def test_inner_not_surrounding(self) -> None:
        c = zl(left=[0], right=[21, 22])
        assert not c.is_surrounding(5)

    def test_empty_surrounding(self) -> None:
        c = zl()
        assert not c.is_surrounding(0)
        assert not c.is_surrounding(99)


# ---------------------------------------------------------------------------
# upper_bound_mw
# ---------------------------------------------------------------------------


class TestUpperBoundMw:
    def test_no_surrounding_is_unbounded(self) -> None:
        assert zl().upper_bound_mw() is None

    def test_right_surr_21_22_blocks_mw2(self) -> None:
        # 21 and 22 are in mw2 (21-30) → upper = mw1
        c = zl(left=[0], right=[21, 22])
        assert c.upper_bound_mw() == 1

    def test_tighter_bound_wins(self) -> None:
        # right=21,31: 21 in mw2, 31 in mw3 → tighter = mw1
        c = zl(right=[21, 31])
        assert c.upper_bound_mw() == 1

    def test_left_surr_ws0_does_not_bound(self) -> None:
        # ws 0 → workspace_to_mw(0) = None → no bounding
        c = zl(left=[0])
        assert c.upper_bound_mw() is None

    def test_exotic_surroundings(self) -> None:
        # 1011-1014 all in mw101 (1011-1020) → upper = mw100
        c = Config(workspace_zero_last=True, surrounding_left=[1011, 1013], surrounding_right=[1012, 1014])
        assert c.upper_bound_mw() == 100


# ---------------------------------------------------------------------------
# is_valid_mw
# ---------------------------------------------------------------------------


class TestIsValidMw:
    def test_negative_always_invalid(self) -> None:
        assert not zl().is_valid_mw(-1)

    def test_within_bound(self) -> None:
        c = zl(left=[0], right=[21, 22])
        assert c.is_valid_mw(0)
        assert c.is_valid_mw(1)

    def test_at_and_beyond_bound(self) -> None:
        c = zl(left=[0], right=[21, 22])
        assert not c.is_valid_mw(2)
        assert not c.is_valid_mw(99)

    def test_unbounded_large_mw_valid(self) -> None:
        assert zl().is_valid_mw(999)


# ---------------------------------------------------------------------------
# band
# ---------------------------------------------------------------------------


class TestBand:
    def test_no_surrounding(self) -> None:
        assert zl().band(0) == list(range(1, 11))

    def test_with_surrounding_mw0(self) -> None:
        c = zl(left=[0], right=[21, 22])
        assert c.band(0) == [0] + list(range(1, 11)) + [21, 22]

    def test_with_surrounding_mw1(self) -> None:
        c = zl(left=[0], right=[21, 22])
        assert c.band(1) == [0] + list(range(11, 21)) + [21, 22]

    def test_exotic_surroundings_order(self) -> None:
        c = Config(workspace_zero_last=True, surrounding_left=[1011, 1013], surrounding_right=[1012, 1014])
        assert c.band(5) == [1011, 1013] + list(range(51, 61)) + [1012, 1014]


# ---------------------------------------------------------------------------
# default_workspace
# ---------------------------------------------------------------------------


class TestDefaultWorkspace:
    def test_zero_last(self) -> None:
        c = zl()
        assert c.default_workspace(0) == 1
        assert c.default_workspace(1) == 11
        assert c.default_workspace(2) == 21

    def test_zero_first(self) -> None:
        c = nzl()
        assert c.default_workspace(0) == 0
        assert c.default_workspace(1) == 10
        assert c.default_workspace(2) == 20


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_zero_last_with_0_left(self) -> None:
        _validate(True, [0], [21, 22])

    def test_valid_no_surrounding(self) -> None:
        _validate(True, [], [])
        _validate(False, [], [])

    def test_duplicate_across_lists_rejected(self) -> None:
        with pytest.raises(ConfigError, match="Duplicate"):
            _validate(True, [21], [21])

    def test_duplicate_within_list_rejected(self) -> None:
        with pytest.raises(ConfigError, match="Duplicate"):
            _validate(True, [21, 21], [])

    def test_zero_first_disallows_ws0(self) -> None:
        with pytest.raises(ConfigError):
            _validate(False, [0], [])

    def test_zero_first_disallows_ws9(self) -> None:
        with pytest.raises(ConfigError):
            _validate(False, [], [9])

    def test_zero_first_allows_ws10(self) -> None:
        _validate(False, [], [10])

    @pytest.mark.parametrize("ws", range(1, 11))
    def test_zero_last_disallows_ws1_through_10(self, ws: int) -> None:
        with pytest.raises(ConfigError):
            _validate(True, [ws], [])

    def test_zero_last_allows_ws0(self) -> None:
        _validate(True, [0], [])

    def test_zero_last_allows_ws11_plus(self) -> None:
        _validate(True, [], [11, 100, 999])

    def test_exotic_valid(self) -> None:
        _validate(True, [1011, 1013], [1012, 1014])


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_defaults_when_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_CONFIG_DIR": tmpdir}):
                c = load_config()
        assert c.workspace_zero_last is True
        assert c.surrounding_left == []
        assert c.surrounding_right == []

    def test_parses_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "hypr").mkdir()
            (Path(tmpdir) / "hypr" / "hyprmetaworkspaces.conf").write_text(
                "workspace_zero_last=false\n"
                "surrounding_left_workspaces=10\n"
                "surrounding_right_workspaces=20,30\n"
            )
            with patch.dict(os.environ, {"XDG_CONFIG_DIR": tmpdir}):
                c = load_config()
        assert c.workspace_zero_last is False
        assert c.surrounding_left == [10]
        assert c.surrounding_right == [20, 30]

    def test_ignores_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "hypr").mkdir()
            (Path(tmpdir) / "hypr" / "hyprmetaworkspaces.conf").write_text(
                "# comment\n\nworkspace_zero_last=true\n"
            )
            with patch.dict(os.environ, {"XDG_CONFIG_DIR": tmpdir}):
                c = load_config()
        assert c.workspace_zero_last is True
