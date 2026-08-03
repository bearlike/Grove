# Init scripts

A new worktree starts empty. No `.envrc`, no installed dependencies, no
language server cache. An init script automates the setup so the agent
finds a ready environment when it spawns.

## What runs and when

Init runs once when Grove creates a workspace, before the agent command is
sent. By default it does not run on resume, since a resumed worktree
already has whatever the original init produced. Set `run_on_resume: true`
if your setup is cheap and you want strict parity.

The script runs in its own tmux window named `init`. You can attach to the
workspace and watch it scroll, or check the window after the fact. Output
stays in the tmux scrollback (`history_limit: 50000` by default).

## Skip it for one create

`enabled` is the config-level switch for every workspace. The create modal
adds a per-create override: a "Skip init script" checkbox that turns the
script off for that one workspace, no config edit required. The outcome
records as SKIPPED, the same as if init were disabled.

Reach for it when a given task does not need the setup, or when the script
would be unsafe in the target. A root workspace runs in your real repo
root, where a script written for a fresh worktree can do the wrong thing,
so the modal checks the box for you whenever you pick Root. The choice
applies to that create only. It is never stored and never re-applied on
resume or respawn.

## Fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `enabled`         | bool   | `false`  | Master switch. Init runs only when this is `true`. |
| `shell`           | string | `"bash"` | One of `bash`, `sh`, `zsh`. Used as `<shell> -c <inline>` for inline scripts, or `<shell> <path>` for file-based scripts. |
| `inline`          | string | `null`   | Inline shell snippet. Mutually exclusive with `path`. |
| `path`            | string | `null`   | Repo-relative path to a script file. Mutually exclusive with `inline`. |
| `timeout_seconds` | int    | `300`    | Hard wall-clock cap. Past this, Grove kills the script and treats it as failed. |
| `fail_fast`       | bool   | `true`   | Non-zero exit rolls back the worktree, the branch (if Grove created it), and the tmux session. |
| `run_on_resume`   | bool   | `false`  | Re-run the script on resume. Off by default. |
| `applies_to`      | string | `"all"`  | `"all"`, `"host"`, or `"container"`. Which workspaces the script runs for. |

## Scoping to host or container

A [container workspace](features-containers.md) already has its own setup
step: a devcontainer's `postCreateCommand` and the rest of its lifecycle
hooks. Grove's init script runs earlier and prepares something different,
the host worktree itself, the directory that gets bind mounted into the
container. Both run, in that order, so a project can end up preparing its
environment twice if the two scripts overlap.

`applies_to` lets you draw the line. Set it to `container` when a project's
devcontainer hooks already install dependencies, so the host script skips
straight past that work and only handles what the worktree needs before the
container starts. Set it to `host` when the script installs tooling that
only makes sense outside a container, and the devcontainer hooks cover the
rest. Leave it at `all`, the default, when one script legitimately serves
both, which is most projects.

```json
{
  "init_script": {
    "enabled": true,
    "inline": "cp .env.example .env",
    "applies_to": "host"
  }
}
```

The scope matches what the workspace actually is, not what was requested.
A workspace that asked for a container and fell back to the host counts as
a host workspace here, so `applies_to: "host"` still runs for it.

When the scope excludes a workspace, nothing fails. The init step is simply
reported as SKIPPED, the same as `enabled: false`.

## What the script knows about its workspace

Grove exports four variables into the script's environment, on every verb that
runs it — create, and resume or respawn when `run_on_resume` is on.

| Variable | What it holds |
| --- | --- |
| `GROVE_REPO` | Absolute path to the repository root |
| `GROVE_WORKTREE` | Absolute path to this workspace's worktree |
| `GROVE_BRANCH` | The branch this workspace is on |
| `GROVE_AGENT` | The configured agent's name |

They are derived from the workspace record each time the script runs, so they
stay correct after a branch is renamed rather than reporting whatever was true
at create.

```json
{
  "init_script": {
    "enabled": true,
    "inline": "echo \"setting up $GROVE_BRANCH in $GROVE_WORKTREE\""
  }
}
```

> [!TIP]
> The script also inherits your own environment, so anything already exported
> in the shell that launched Grove is available too.

## Inline and path are mutually exclusive

Setting both `inline` and `path` in the same config layer fails at config
load, before Grove ever tries to run the script.

Across the cascade, whichever layer sets `inline` or `path` last wins the
whole choice, not just the field it touched. If a lower layer set the
other field, Grove drops it rather than merging both into one config. This
is what lets your committed `.grove/config.json` script serve as the team
default while your own `.grove/config.local.json` swaps in an inline
snippet for your machine, or the reverse: the highest layer to declare
either field decides the pattern outright. Grove logs a warning whenever
an override actually strips a field this way, so the effective choice is
never a surprise.

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

The `path` is repo-relative. Make the file executable. Grove invokes the
configured shell, not the file's shebang.

## Failure semantics

Init can succeed, fail, or be skipped. Grove records the outcome once, in
the same place for both `create` and `resume`, so the two paths cannot
report different things for the same situation. The display reads one of:

- **OK**: the script exited 0 within the timeout.
- **FAILED**: non-zero exit. With `fail_fast: true` (the default) Grove
  rolls back the worktree, branch, and tmux session. With `fail_fast: false`
  the workspace stays alive in `ERROR` state and the contextual footer
  offers `kill` only.
- **SKIPPED**: `enabled: false`, the "Skip init script" box was checked
  for this create, `run_on_resume: false` on a resume, or `applies_to`
  excludes the workspace's actual runtime.
- **TIMEOUT**: wall-clock exceeded `timeout_seconds`. Treated like FAILED.

The init outcome is also exposed on the `WorkspaceEvent` stream so future
clients (web, MCP) can surface it without re-running the script.

## See also

- [Daily workflow](use-workflow.md): where init fits in the create and resume flow.
- [Workspace lifecycle](features-workspace-lifecycle.md): what rollback actually does.
- [Configuration cascade](features-cascade.md): how layers merge, including the
  `inline`/`path` override rule.
- [Container workspaces](features-containers.md): the devcontainer lifecycle
  hooks that run alongside the init script.
