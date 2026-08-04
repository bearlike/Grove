# Init scripts

## Prepare a new worktree

A new worktree starts empty. No `.envrc`, no dependencies, no language server
cache. An init script prepares it before the agent spawns.

## What runs and when

| When | Behavior |
|---|---|
| Create | Runs once, before the tmux session and the agent exist. |
| Resume | Skipped by default, since the worktree keeps whatever the first run produced. Set `run_on_resume: true` for strict parity when your setup is cheap. |
| Output | Captured to a per-workspace init log, not a pane. A `fail_fast` failure quotes the log's last 20 lines in the error; `kill` deletes it. |

## Skip it for one create

- `enabled` is the config switch for every workspace. `grove create --no-init`
  and the dashboard's "Skip init script" checkbox turn it off for one
  workspace with no config edit, recording SKIPPED like `enabled: false`.
- A root workspace runs in your real repo root, where a fresh-worktree script
  can do the wrong thing, so picking Root checks the box for you.
- The choice is never stored or re-applied on resume or respawn.

## Fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `enabled`         | bool   | `false`  | Master switch. Init runs only when this is `true`. |
| `shell`           | string | `"bash"` | One of `bash`, `sh`, `zsh`. Run as `<shell> -c <inline>` or `<shell> <path>`. |
| `inline`          | string | `null`   | Inline shell snippet. Mutually exclusive with `path`. |
| `path`            | string | `null`   | Repo-relative path to a script file. Mutually exclusive with `inline`. |
| `timeout_seconds` | int    | `300`    | Hard wall-clock cap. Past this, Grove kills the script and treats it as failed. |
| `fail_fast`       | bool   | `true`   | Non-zero exit rolls back the worktree, the branch (if Grove created it), and the tmux session. |
| `run_on_resume`   | bool   | `false`  | Re-run the script on resume. Off by default. |
| `applies_to`      | string | `"all"`  | `"all"`, `"host"`, or `"container"`. Which workspaces the script runs for. |

## Scoping to host or container

A [container workspace](features-containers.md) has its own setup step
already: the devcontainer's `postCreateCommand` and lifecycle hooks. Grove's
init script runs earlier, preparing the host worktree, the directory bind
mounted into the container. Both run, so overlapping scripts prepare one
environment twice. `applies_to` draws the line:

| Value | Use when |
|---|---|
| `container` | The devcontainer hooks install the dependencies. |
| `host` | The script installs tooling that makes sense only outside a container. |
| `all` (default) | One script serves both, true for most projects. |

```json
{
  "init_script": {
    "enabled": true,
    "inline": "cp .env.example .env",
    "applies_to": "host"
  }
}
```

The scope matches what the workspace actually is, not what was requested: a
workspace that asked for a container and fell back to the host counts as
host, so `applies_to: "host"` still runs for it. An excluded workspace fails
nothing. Init reports SKIPPED.

## What the script knows about its workspace

Grove exports four variables on every verb that runs the script: create,
plus resume and respawn under `run_on_resume`.

| Variable | What it holds |
| --- | --- |
| `GROVE_REPO` | Absolute path to the repository root |
| `GROVE_WORKTREE` | Absolute path to this workspace's worktree |
| `GROVE_BRANCH` | The branch this workspace is on |
| `GROVE_AGENT` | The configured agent's name |

They are derived from the workspace record on each run, so a branch rename
leaves them correct instead of stale from create.

```json
{
  "init_script": {
    "enabled": true,
    "inline": "echo \"setting up $GROVE_BRANCH in $GROVE_WORKTREE\""
  }
}
```

> [!TIP]
> The script also inherits your own environment: anything already exported
> in the shell that launched Grove is available too.

## Inline and path are mutually exclusive

- Setting both in one layer fails at config load, before anything runs.
- Across the cascade, whichever layer sets `inline` or `path` last wins the
  whole choice, not just the field it touched. Grove drops the other field
  instead of merging both, logging a warning when that strips something.
- So a committed `.grove/config.json` path can serve as the team default
  while `.grove/config.local.json` swaps in an inline snippet, or the reverse.

## Three patterns

### Python project (uv)

```json
{
  "init_script": {
    "enabled": true,
    "inline": "uv sync && cp ../.envrc .envrc",
    "timeout_seconds": 180
  }
}
```

### Node project (pnpm)

```json
{
  "init_script": {
    "enabled": true,
    "inline": "pnpm install --frozen-lockfile && pnpm prebuild",
    "timeout_seconds": 600
  }
}
```

### Repo with a checked-in script

```json
{
  "init_script": {
    "enabled": true,
    "path": "scripts/grove-init.sh",
    "timeout_seconds": 300
  }
}
```

The `path` is repo-relative. Grove invokes the configured shell, not the file's
shebang.

## Failure semantics

Init succeeds, fails, or is skipped. Grove records the outcome once for both
`create` and `resume`, so the two paths cannot disagree, and the outcome
rides the workspace record so the TUI and the dashboard read it without
re-running the script. The display reads one of:

| Outcome | Meaning |
|---|---|
| **OK** | The script exited 0 within the timeout. |
| **FAILED** | Non-zero exit. With `fail_fast: true` (the default) Grove rolls back the worktree, branch, and tmux session. With `fail_fast: false` the workspace stays alive in `ERROR` state, where `kill` is the only lifecycle verb left. |
| **SKIPPED** | `enabled: false`, a skip requested for this create, `run_on_resume: false` on a resume, or `applies_to` excluding the workspace's actual runtime. |
| **TIMEOUT** | Wall-clock exceeded `timeout_seconds`. Treated like FAILED. |

## See also

- [Daily workflow](use-workflow.md): where init fits in create and resume.
- [Workspace lifecycle](features-workspace-lifecycle.md): what rollback does.
- [Configuration cascade](features-cascade.md): how layers merge.
- [Container workspaces](features-containers.md): the lifecycle hooks that run
  alongside init.
