# CLI

The TUI is the primary surface. The CLI exposes a small set of read-only
views, a config scaffold, and the host-level commands that run the daemon and
manage pairing. Use them to script around Grove, debug configuration, or
generate JSON for another tool to consume. The repo-scoped subcommands use
the same six-layer cascade as the TUI; the `daemon` and `auth` groups operate
at the host level, not against a single repo.

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

## `grove daemon serve`

Run the HTTP daemon that backs the [web dashboard](use-webapp.md) and any
remote client. It binds loopback by default and serves every repo Grove
knows about from one process.

```bash
grove daemon serve                 # 127.0.0.1:7421
grove daemon serve --port 7777     # a different port
```

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Interface to bind. Loopback is deliberate. See [the security model](use-auth.md#the-security-model). |
| `--port` | `7421` | Port to listen on. `0` picks a free one. |
| `--print-port` | off | Print the bound port to stdout once listening. Handy when `--port 0` auto-picks. |

Every endpoint except the health probe (`/healthz`) and the pairing handshake
requires a paired session. [Authentication & pairing](use-auth.md) covers how
a device gets one.

## `grove auth`

Approve or deny pairing requests and manage active sessions. These act on the
host's session store (`${user_config_dir}/grove/auth.json`), so they work
from any directory, and they are not scoped to a repo. The full pairing story is
on the [authentication](use-auth.md) page; the commands are:

`grove auth pending` lists requests waiting for approval, one per line with
the challenge id, the matching code, the device label, and the expiry.

```bash
grove auth pending
# 7b3f2c1a-…  code=BFCD-GH23  label='Pixel 8'  state=pending  expires_at=2026-06-01T18:30:00+00:00
```

`grove auth approve <challenge-id>` approves a request; the device picks up
its token on its next poll, so this command never prints a token.
`grove auth deny <challenge-id>` rejects one. Both take an id from
`grove auth pending`.

`grove auth sessions` lists active (non-revoked) sessions; `grove auth revoke
<session-id>` cuts one off until that device pairs again.

```bash
grove auth approve 7b3f2c1a-…      # id from `grove auth pending`
grove auth sessions
grove auth revoke 9d21f0e4-…       # id from `grove auth sessions`
```

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
