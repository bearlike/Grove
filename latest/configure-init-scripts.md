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

## Fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| `enabled`         | bool   | `false`  | Master switch. Init runs only when this is `true`. |
| `shell`           | string | `"bash"` | One of `bash`, `sh`, `zsh`. Used as `<shell> -lc <script>`. |
| `inline`          | string | `null`   | Inline shell snippet. Mutually exclusive with `path`. |
| `path`            | string | `null`   | Repo-relative path to a script file. Mutually exclusive with `inline`. |
| `timeout_seconds` | int    | `300`    | Hard wall-clock cap. Past this, Grove kills the script and treats it as failed. |
| `fail_fast`       | bool   | `true`   | Non-zero exit rolls back the worktree, the branch (if Grove created it), and the tmux session. |
| `run_on_resume`   | bool   | `false`  | Re-run the script on resume. Off by default. |

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
- **SKIPPED**: `enabled: false`, or `run_on_resume: false` on a resume.
- **TIMEOUT**: wall-clock exceeded `timeout_seconds`. Treated like FAILED.

The init outcome is also exposed on the `WorkspaceEvent` stream so future
clients (web, MCP) can surface it without re-running the script.

## See also

- [Daily workflow](use-workflow.md): where init fits in the create and resume flow.
- [Workspace lifecycle](features-workspace-lifecycle.md): what rollback actually does.
