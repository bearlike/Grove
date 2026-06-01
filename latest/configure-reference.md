# Configuration reference

This page is generated from Grove's Pydantic model.  Edit `src/grove/core/config.py` and run `make docs` (or push to the default branch, which regenerates in CI) to refresh.


## `agents` (list of `AgentSpec`)

One selectable agent in the new-workspace picker.


| Field | Type | Default | Description |
|---|---|---|---|
| `command` | `string` | `**required**` |  |
| `description` | `string` | ``''`` |  |
| `env` | `object` | `(none)` |  |
| `name` | `string` | `**required**` |  |

## `auth`

Daemon HTTP authentication knobs.

The handshake-based pairing flow gates every HTTP entry point on a valid
bearer token (no loopback bypass — see CLAUDE.md). ``enabled = false`` is
a test-only escape hatch; production daemons leave it ``true``.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``True`` |  |
| `pair_init_per_minute` | `integer` | ``5`` |  |
| `pair_poll_per_minute` | `integer` | ``60`` |  |
| `pairing_ttl_seconds` | `integer` | ``300`` |  |
| `session_ttl_seconds` | `integer` | ``2592000`` |  |

## `init_script`

Optional setup script run in its own tmux window before the agent starts.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``False`` |  |
| `fail_fast` | `boolean` | ``True`` |  |
| `inline` | `string \| null` | ``None`` |  |
| `path` | `string \| null` | ``None`` |  |
| `run_on_resume` | `boolean` | ``False`` |  |
| `shell` | `string` | ``'bash'`` |  |
| `timeout_seconds` | `integer` | ``300`` |  |

## `tmux`

tmux session/window naming and behavior.


| Field | Type | Default | Description |
|---|---|---|---|
| `activity_threshold_seconds` | `integer` | ``5`` |  |
| `agent_window_name` | `string` | ``'agent'`` |  |
| `history_limit` | `integer` | ``50000`` |  |
| `init_window_name` | `string` | ``'init'`` |  |
| `peek_pane_refresh_seconds` | `number` | ``0.25`` |  |
| `peek_stats_refresh_seconds` | `number` | ``3.0`` |  |
| `session_prefix` | `string` | ``'grove-'`` |  |
| `shell_window_name` | `string` | ``'shell'`` |  |

## `ui`

Client-facing UI knobs. The TUI consumes these; core ignores them.


| Field | Type | Default | Description |
|---|---|---|---|
| `keybindings` | `object` | `(none)` |  |
| `theme` | `string` | ``'auto'`` |  |

## `worktree`

Where worktrees live and how branches are named.


| Field | Type | Default | Description |
|---|---|---|---|
| `branch_prefix` | `string` | ``'grove/'`` |  |
| `root_template` | `string` | ``'${repo}/.worktrees'`` |  |
