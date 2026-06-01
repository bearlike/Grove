# CLI

The TUI is the primary surface. The CLI exposes a small set of read-only
views and one scaffold subcommand. Use them to script around Grove, debug
configuration, or generate JSON for another tool to consume. Every
subcommand uses the same six-layer cascade as the TUI.

## `grove`

Launch the TUI for the current repository.

```bash
cd path/to/repo
grove
```

The default callback runs even when no subcommand is given. It locates the
repo root from `cwd`, builds a `WorkspaceManager` against the merged config,
and hands it to the Textual app.

## `grove ls`

Print this repo's workspaces as JSON, one record per workspace.

```bash
grove ls
```

```json
[
  {
    "id": "auth-rewrite",
    "title": "Auth rewrite",
    "agent": "claude",
    "branch": "feat/auth-rewrite",
    "status": "active",
    "worktree_path": "/home/me/code/myproject/.worktrees/auth-rewrite",
    "tmux_session": "grove-auth-rewrite"
  }
]
```

The `status` field reflects Grove's computed view (`active`, `idle`,
`paused`, `offline`, `orphaned`, `error`). For the persisted-vs-computed
distinction, see [status semantics](features-status.md).

## `grove config show`

Print the merged effective config as JSON.

```bash
grove config show
```

## `grove config init`

Scaffold a project config at `<repo>/.grove/config.json`. Refuses to
overwrite unless `--force` is passed.

```bash
grove config init        # writes .grove/config.json + the user-side schema
grove config init -f     # overwrite an existing project config
```

The same call also writes `${user_config_dir}/grove/config.schema.json` so
the IDE picks up autocomplete via `$schema`.

## `grove config schema`

Rewrite the JSON Schema next to the user config without scaffolding anything
else. Useful after upgrading Grove if the model gained a new field.

```bash
grove config schema           # write to disk (default)
grove config schema --stdout  # print to stdout, used by the docs build
```

The `--stdout` form feeds `make docs-schema` and the CI workflow into the
docs hook, so the [configuration reference](configure-reference.md) always
matches the live model.

## `grove version`

Print the installed version.

```bash
grove version
# grove 0.1.0
```

## `grove debug`

Print the resolved paths Grove would use plus whether the config loaded
cleanly. JSON for easy `jq` consumption.

```bash
grove debug
```

```json
{
  "user_config_path": "/home/me/.config/grove/config.json",
  "user_state_path": "/home/me/.local/state/grove/state.json",
  "user_schema_path": "/home/me/.config/grove/config.schema.json",
  "project_config_path": "/home/me/code/myproject/.grove/config.json",
  "project_local_config_path": "/home/me/code/myproject/.grove/config.local.json",
  "repo_root": "/home/me/code/myproject",
  "config_loaded": true
}
```

## `GROVE_DEBUG=1`

Set the environment variable to flip loguru's stderr handler from `WARNING`
to `DEBUG`. Every `git` and `tmux` subprocess invocation, every cascade
merge, and every state-file read becomes visible.

```bash
GROVE_DEBUG=1 grove ls
```
