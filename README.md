# hyprmetaworkspaces

A two-level workspace hierarchy for [Hyprland](https://hyprland.org). Groups Hyprland's flat numbered workspaces into **metaworkspaces** of 10, navigable via a daemon + CLI.

## Overview

- **`hyprmetaworkspaced`** — async daemon that tracks the current metaworkspace and listens to Hyprland IPC events
- **`hyprmwctl`** — CLI client that sends commands to the daemon over a Unix socket (JSON-RPC 2.0)

Metaworkspace 0 contains workspaces 1–10, metaworkspace 1 contains 11–20, and so on. Digits 1–9 map directly; digit 0 maps to the 10th workspace (configurable with `workspace_zero_last`).

### Why not a Hyprland plugin?
I wanted to use python and not c++ # TODO: MAKE CPP AND PLUGIN

## Installation

### Nix flake

```nix
# flake.nix
{
  inputs.hyprmetaworkspaces.url = "github:gordoncyu/hyprmetaworkspaces";

  # In your system/home-manager config:
  environment.systemPackages = [ hyprmetaworkspaces.packages.${system}.default ];
}
```

### Development

```sh
nix develop
pytest tests/ -v
```

## Configuration

Config file: `$XDG_CONFIG_HOME/hypr/hyprmetaworkspaces.conf` (defaults to `~/.config/hypr/hyprmetaworkspaces.conf`)

```ini
# Digit 0 maps to the last workspace in each metaworkspace (ws 10, 20, 30, ...)
# Set to false to have digit 0 map to ws 0, 10, 20, ...
workspace_zero_last=true

# Surrounding workspaces sit outside the normal 10-workspace grid.
# They appear at the edges of every metaworkspace's navigation band.
# Comma-separated workspace numbers. Leave empty for none.
surrounding_left_workspaces=
surrounding_right_workspaces=
```

All options are optional — the defaults work out of the box.

## Usage

### Hyprland binds

```ini
# Switch to inner workspace by digit (0-9)
bind = $mod, 1, exec, hyprmwctl dispatch workspace 1
bind = $mod, 2, exec, hyprmwctl dispatch workspace 2
# ...
bind = $mod, 0, exec, hyprmwctl dispatch workspace 0

# Switch metaworkspace
bind = $mod CTRL, 1, exec, hyprmwctl dispatch metaworkspace 0
bind = $mod CTRL, 2, exec, hyprmwctl dispatch metaworkspace 1
bind = $mod CTRL, 3, exec, hyprmwctl dispatch metaworkspace 2

# Sequential navigation
bind = $mod, bracketright, exec, hyprmwctl dispatch workspacesequential next
bind = $mod, bracketleft,  exec, hyprmwctl dispatch workspacesequential prev

# Sequential with flags
bind = $mod SHIFT, bracketright, exec, hyprmwctl dispatch workspacesequential next,skipempty,wrapout
bind = $mod SHIFT, bracketleft,  exec, hyprmwctl dispatch workspacesequential prev,skipempty,wrapin

# Step through metaworkspaces
bind = $mod CTRL, bracketright, exec, hyprmwctl dispatch metaworkspacesequential next
bind = $mod CTRL, bracketleft,  exec, hyprmwctl dispatch metaworkspacesequential prev

# Move window to metaworkspace (follows window)
bind = $mod SHIFT, 1, exec, hyprmwctl dispatch movetometaworkspace 0
bind = $mod SHIFT, 2, exec, hyprmwctl dispatch movetometaworkspace 1

# Move window to metaworkspace (stay on current workspace)
bind = $mod CTRL SHIFT, 1, exec, hyprmwctl dispatch movetometaworkspacesilent 0
bind = $mod CTRL SHIFT, 2, exec, hyprmwctl dispatch movetometaworkspacesilent 1
```

### Dispatchers

**`workspace <digit>`** — navigate to workspace 0–9 within the current metaworkspace.

**`movetoworkspace <digit>`** — move the focused window to workspace 0–9 in the current metaworkspace and follow it.

**`movetoworkspacesilent <digit>`** — same but stay on the current workspace.

**`metaworkspace <n>[,chordworkspace]`** — switch to metaworkspace *n*, landing on the last-visited inner workspace (or the default). With `chordworkspace`, enters a Hyprland submap for digit selection.

**`movetometaworkspace <n>[,chordworkspace]`** — move the focused window to metaworkspace *n* and follow it. Lands on the last-visited workspace (or default). With `chordworkspace`, enters a submap for digit selection without switching until a digit is pressed.

**`movetometaworkspacesilent <n>[,chordworkspace]`** — same as above but stays on the current workspace.

**`workspacesequential <next|prev>[,flags...]`** — step to the next/previous workspace in band order.
- `skipempty` — skip workspaces with no windows
- `skipsurrounding` — skip surrounding workspaces
- `wrapin` — wrap within the current metaworkspace's band
- `wrapout` — cross into the next/previous metaworkspace at band edges (default)

**`metaworkspacesequential <next|prev>[,flags...]`** — step to the next/previous metaworkspace.
- `skipempty` — skip metaworkspaces with no windows
- `wrap` / `nowrap` — control wrapping (default: wrap)

**`get-state`** — print current daemon state as JSON.

### Arguments

Arguments are comma-delimited. Both forms work:

```sh
hyprmwctl dispatch workspacesequential next,skipempty,wrapout
hyprmwctl dispatch workspacesequential next skipempty wrapout
```

## Concepts

### Metaworkspaces

Each metaworkspace is a group of 10 Hyprland workspaces. With `workspace_zero_last=true` (default):

| Metaworkspace | Workspaces |
|---------------|------------|
| 0             | 1–10       |
| 1             | 11–20      |
| 2             | 21–30      |
| ...           | ...        |

### Surrounding workspaces

Arbitrary workspace numbers that sit outside the normal grid. They appear at the left and right edges of every metaworkspace's **band** — the full navigation sequence:

```
[left_surrounding...] [inner 1–10] [right_surrounding...]
```

Surrounding workspaces can have any numeric value (e.g., `left=1011,1013 right=1012,1014`). Navigation follows band position, not numeric order.

If a surrounding workspace's number falls within a metaworkspace's inner range, that metaworkspace (and all higher ones) become invalid.

### State tracking

The daemon subscribes to Hyprland's event socket. When you switch workspaces by any means (including raw `hyprctl`), the daemon updates its internal state — current metaworkspace and last-visited workspace per metaworkspace.

## Architecture

```
┌─────────────┐  JSON-RPC/Unix socket  ┌──────────────────┐  Hyprland IPC  ┌──────────┐
│  hyprmwctl  │ ──────────────────────> │ hyprmetaworkspaced│ ─────────────> │ Hyprland │
└─────────────┘                         │                  │ <───────────── │          │
                                        │  (event listener)│  workspace>>N  └──────────┘
                                        └──────────────────┘
```

## Testing

```sh
# Unit tests (no Hyprland needed)
pytest tests/test_config.py tests/test_state.py tests/test_dispatchers.py -v

# Integration tests (requires a live Hyprland session — spawns a nested compositor)
pytest tests/test_integration.py -v

# All tests
pytest tests/ -v

# Via nix
nix flake check
```

## License

MIT
