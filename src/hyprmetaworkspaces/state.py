from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config


@dataclass
class DaemonState:
    config: Config
    current_mw: int = 0
    # last visited inner workspace number per metaworkspace index
    last_visited: dict[int, int] = field(default_factory=dict)

    def get_last_visited(self, mw: int) -> int:
        """Return the last visited workspace for mw, or the default if never visited."""
        return self.last_visited.get(mw, self.config.default_workspace(mw))

    def record_visit(self, ws: int) -> None:
        """
        Called when Hyprland reports a workspace change to ws.
        Updates current_mw and last_visited if ws belongs to a known mw or is surrounding.
        """
        if self.config.is_surrounding(ws):
            # Surrounding workspace: don't change current_mw, don't record as inner visit
            return

        mw = self.config.workspace_to_mw(ws)
        if mw is None:
            # Outside the system entirely
            return

        if not self.config.is_valid_mw(mw):
            # In range of a blocked mw — don't update state
            return

        self.current_mw = mw
        self.last_visited[mw] = ws
