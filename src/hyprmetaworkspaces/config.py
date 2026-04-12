from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    workspace_zero_last: bool
    surrounding_left: list[int]
    surrounding_right: list[int]

    def digit_to_workspace(self, mw: int, digit: int) -> int:
        """Map a 0-9 digit to a Hyprland workspace number within metaworkspace mw."""
        if self.workspace_zero_last:
            if digit == 0:
                return mw * 10 + 10
            return mw * 10 + digit
        else:
            return mw * 10 + digit

    def inner_workspaces(self, mw: int) -> list[int]:
        """Return the 10 inner workspace numbers for metaworkspace mw, in navigation order."""
        if self.workspace_zero_last:
            # 1,2,...,9,10 (digit 0 → last)
            return [mw * 10 + d for d in range(1, 11)]
        else:
            # 0,1,...,9
            return [mw * 10 + d for d in range(0, 10)]

    def workspace_to_mw(self, ws: int) -> int | None:
        """Return which metaworkspace owns this workspace, or None if it's surrounding/outside."""
        if self.workspace_zero_last:
            if ws <= 0:
                return None
            # ws in [mw*10+1, mw*10+10] → mw = (ws-1) // 10
            mw = (ws - 1) // 10
            base = mw * 10
            if ws >= base + 1 and ws <= base + 10:
                return mw
            return None
        else:
            if ws < 0:
                return None
            mw = ws // 10
            return mw

    def is_surrounding(self, ws: int) -> bool:
        return ws in self.surrounding_left or ws in self.surrounding_right

    @property
    def all_surrounding(self) -> set[int]:
        return set(self.surrounding_left) | set(self.surrounding_right)

    def upper_bound_mw(self) -> int | None:
        """
        Return the upper bound (inclusive) of valid metaworkspaces, or None if unbounded.
        Any surrounding workspace that falls within mw M blocks M and everything above.
        """
        bound: int | None = None
        for ws in self.all_surrounding:
            mw = self.workspace_to_mw(ws)
            if mw is not None:
                if bound is None or mw - 1 < bound:
                    bound = mw - 1
        return bound

    def is_valid_mw(self, mw: int) -> bool:
        if mw < 0:
            return False
        upper = self.upper_bound_mw()
        if upper is not None and mw > upper:
            return False
        return True

    def band(self, mw: int) -> list[int]:
        """Full navigation band for a metaworkspace: left surrounding + inner + right surrounding."""
        return self.surrounding_left + self.inner_workspaces(mw) + self.surrounding_right

    def default_workspace(self, mw: int) -> int:
        """Default workspace to land on when a metaworkspace has never been visited."""
        if self.workspace_zero_last:
            return mw * 10 + 1
        else:
            return mw * 10


def _parse_int_list(value: str) -> list[int]:
    value = value.strip()
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",")]


def load_config() -> Config:
    xdg_config = os.environ.get("XDG_CONFIG_DIR") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    config_path = Path(xdg_config) / "hypr" / "hyprmetaworkspaces.conf"

    raw: dict[str, str] = {}
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                raw[key.strip()] = value.strip()

    workspace_zero_last_str = raw.get("workspace_zero_last", "true").lower()
    workspace_zero_last = workspace_zero_last_str in ("true", "1", "yes")

    surrounding_left = _parse_int_list(raw.get("surrounding_left_workspaces", ""))
    surrounding_right = _parse_int_list(raw.get("surrounding_right_workspaces", ""))

    _validate(workspace_zero_last, surrounding_left, surrounding_right)

    return Config(
        workspace_zero_last=workspace_zero_last,
        surrounding_left=surrounding_left,
        surrounding_right=surrounding_right,
    )


def _validate(workspace_zero_last: bool, surrounding_left: list[int], surrounding_right: list[int]) -> None:
    all_surr = surrounding_left + surrounding_right
    seen: set[int] = set()
    for ws in all_surr:
        if ws in seen:
            raise ConfigError(f"Duplicate surrounding workspace: {ws}")
        seen.add(ws)

    for ws in all_surr:
        if workspace_zero_last:
            if ws != 0 and ws <= 10:
                raise ConfigError(
                    f"Surrounding workspace {ws} is invalid with workspace_zero_last=true "
                    f"(must be > 10 or == 0)"
                )
        else:
            if ws < 10:
                raise ConfigError(
                    f"Surrounding workspace {ws} is invalid with workspace_zero_last=false "
                    f"(must be >= 10)"
                )

    # Ensure at least mw 0 is valid — check no surrounding falls in mw 0's inner range
    if workspace_zero_last:
        mw0_range = set(range(1, 11))
    else:
        mw0_range = set(range(0, 10))

    for ws in all_surr:
        if ws in mw0_range:
            raise ConfigError(
                f"Surrounding workspace {ws} falls within metaworkspace 0's inner range, "
                f"which would leave no valid metaworkspaces"
            )
