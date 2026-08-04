# Troubleshooting

## Fixes for common symptoms

Common failures, their causes, and the fix.

## Fresh install crashes with `Failed to initialise configuration handler`

**Symptom.** Right after install, `grove` exits with a CRITICAL log line:
`Failed to initialise configuration handler ...
GROVE_CONFIG_LOCAL_FILE_PATH field required`. The traceback names
`grove/entrypoints/base.py`.

**Cause.** Wrong package. PyPI's `grove` is an unrelated log-collection
framework that `uv tool install grove` (or `pipx`, `pip`) installs
instead.

**Fix.**

```bash
uv tool uninstall grove
uv tool install "grove[daemon] @ git+https://github.com/bearlike/Grove"
```

Confirm with `grove version` and `grove debug` (config and state paths).

## `tmux` not found

**Symptom.** `TmuxError: tmux binary not found on PATH`, or the TUI
refuses to launch.

**Causes.**

- `tmux` is not installed.
- Windows-native: the TUI needs tmux, and tmux only runs inside WSL2.

**Fix.**

- Linux: `apt install tmux`. macOS: `brew install tmux`.
- Windows: install WSL2 (`wsl --install`) and run `grove` from inside.
  `grove version`, `grove debug`, and `grove config show` still work.

## Worktree creation fails

**Symptom.** `BranchError`, `BranchConflict`, or `BranchAlreadyCheckedOut`.

**Causes.**

- Branch already exists locally, checked out elsewhere.
- Branch name conflicts with an existing path or ref.
- `git worktree add` failed for another reason: a lock file or permissions.

**Fix.**

- `git worktree list` confirms the branch is not attached.
  `git worktree prune` clears one deleted from disk without pruning.
- Permission errors: verify `worktree.root_template`'s parent is writable.
- *Track remote* failed: `git fetch && git branch -r` confirms the
  remote branch exists.

## Init script fails or times out

**Symptom.** Workspace lands in `ERROR` (`fail_fast: false`, outcome
FAILED or TIMEOUT) or rolls back entirely (`fail_fast: true`).

**Causes.**

- The script exited non-zero, ran past `init_script.timeout_seconds`,
  or its `shell` (bash, sh, zsh) is not installed.
- The daemon's `systemd --user` unit predates Grove's PATH fix,
  resolving stale binaries over your pyenv, nvm, or asdf toolchain.

**Fix.**

- A `fail_fast` failure keeps the init log, ending in the stderr that
  survives rollback.
- Stale daemon PATH: re-run `make systemd`
  (`packaging/systemd/README.md`), then
  `systemctl --user restart grove-daemon`.
- Attach the `init` tmux window before it completes, or resume with
  `run_on_resume: true`.
- Reproduce standalone: `cd /tmp/test && bash -lc "$(jq -r '.init_script.inline'
  /path/to/.grove/config.json)"`. Bump `timeout_seconds` if slow.

## Pause refuses on a dirty worktree

**Symptom.** `DirtyWorktreeError` flashed on the status bar. The
workspace stays RUNNING.

**Cause.** `pause` removes the worktree directory, which would
silently lose uncommitted work.

**Fix.** Switch to the `shell` window (`Ctrl-B 0/1`), commit or stash
(`git commit -am '…'` or `git stash push -u`), detach, then press
++p++ again.

## Agent window auto-closed, workspace shows OFFLINE

**Symptom.** A workspace that was ACTIVE flips to OFFLINE. The
contextual footer offers ++o++ (respawn) and ++k++ (kill) only.

**Cause.** The tmux session vanished: a terminal restart, host reboot,
`tmux kill-server`, or the agent exiting with nothing left to hold the
window.

**Fix.** Press ++o++ to respawn. Grove rebuilds the tmux session with
the same windows and command. If the worktree is also gone, the
workspace is ORPHANED and only `kill` remains.

## Config file is not loaded

**Symptom.** Changes to `.grove/config.json` do not take effect.
`grove config show` does not reflect them.

**Causes.**

- Wrong cascade layer: a user-layer setting the project layer overrides.
- JSON syntax error. Grove failed validation silently and fell back
  to defaults.
- File at the wrong path.

**Fix.**

- `grove debug` prints every path Grove checks and confirms
  `config_loaded: true`.
- `grove config show` shows the merged result and which layer wins.
- See [configuration cascade](features-cascade.md).

## JSON Schema autocomplete not working

**Symptom.** The IDE does not autocomplete `.grove/config.json` keys.

**Causes.**

- The `$schema` path does not resolve, or the schema file has not
  been written yet (fresh install).
- The IDE does not read JSON Schema from `$schema`.

**Fix.**

- `grove config schema` rewrites
  `${user_config_dir}/grove/config.schema.json`.
- Use an absolute `$schema` path. A relative one breaks across worktrees.
- Confirm the IDE reads JSON Schema: built in for VS Code and
  JetBrains, `redhat.vscode-yaml` otherwise.

## Workspace shows ORPHANED

**Symptom.** `kill` is the only available action.

**Cause.** The worktree directory was removed from disk while the
workspace was still running, and `respawn` needs one to rebuild from.

**Fix.** `kill` removes the workspace and tears down any remaining
tmux session. If the branch still exists, create a fresh workspace
against it via the create modal's *Existing local* path.

## Workspace shows a blank transcript / wrong session

**Symptom.** A workspace's transcript is empty, or replays a conversation
that is not the one running in its pane.

**Cause.** The Grove-minted session died and a successor started in the
same directory, but the pane-verified adoption gate rejected it, since
the transcript's birth and the live pane disagreed. See
[`sessions.py`](repo:src/grove/core/sessions.py) and
[Session adoption and recovery](features-activity.md#adoption-and-recovery).

**Fix.**

```bash
grove sessions remap WORKSPACE SESSION
```

Or the TUI's ++x++ key, or the web dashboard's session-picker remap
picker.

## A Grove update killed my other tmux sessions

**Symptom.** Restarting or updating Grove's daemon tears down every
tmux session on the box, not just Grove's own.

**Cause.** The `grove-daemon` unit predates the `KillMode=process` fix.
The daemon forks the shared default tmux server into its own cgroup,
and systemd's default `KillMode=control-group` kills that whole
cgroup, tmux included, on every restart.

> [!WARNING] Check before you restart
> An unfixed unit makes every daemon restart destructive to unrelated
> tmux sessions.

**Fix.**

```bash
make systemd
systemctl --user daemon-reload
```

See [packaging](repo:packaging/CLAUDE.md).

## No agent state on the dashboard

**Symptom.** A workspace shows on the [Activity
Dashboard](features-activity.md) with no agent state, turn or token
counts, or session history, only the active/idle signal.

**Causes.**

- No `kind` set, or `kind: "generic"`: launches, never introspected.
- No adapter for the `kind`. Grove ships adapters for Claude Code,
  Codex CLI, and remote Mewbo. Anything else gets terminal-output
  activity only.

**Fix.** Set `kind` to `claude_code`, `codex`, or `mewbo` and confirm
with `grove config show`. See
[Agents](configure-agents.md#telling-grove-what-kind-of-agent-it-is).
`grove sessions list` finds sessions regardless of `kind`.

## Web dashboard shows "unreachable"

**Symptom.** The dashboard loads but the status bar reads "unreachable",
or no workspaces ever appear.

**Causes.**

- The daemon is not running, or is bound to a different host or port
  than the web app expects.
- A systemd-hosted web app points at a stopped daemon, or a stale
  `GROVE_DAEMON_URL`.

**Fix.**

- Start the daemon: `grove daemon serve` (default port `7421`).
- Probe it: `curl http://127.0.0.1:7421/healthz` should return
  `{"status":"ok",…}`.
- Non-default daemon: set `GROVE_DAEMON_URL` in `webapp/.env.local`
  (or the unit's `DAEMON_PORT` / `DAEMON_URL` variables) and restart.
- See [web dashboard](use-webapp.md).

## Pairing code expired, or a device keeps asking to pair

**Symptom.** The browser shows "code expired", or a previously paired
device is bounced back to the pairing screen.

**Causes.**

- A pairing code lives five minutes. Approving later fails.
- The session was revoked with `grove auth revoke`, or its thirty-day
  window lapsed unused.

**Fix.**

- Request and approve a fresh code promptly: the TUI modal, or
  `grove auth pending` then `grove auth approve <id>`.
- Tidy sessions with `grove auth sessions` and `grove auth revoke`.
- See [authentication & pairing](use-auth.md).

## Old UI or behavior after an update

**Symptom.** You pulled new code or upgraded Grove, but a surface still
shows the old version: a new endpoint 404s, the TUI looks unchanged, or
the dashboard lacks a feature you merged.

**Cause.** Grove runs as three long-lived surfaces, each refreshing on
its own. Updating the package does not restart what is running.

**Fix.**

- **CLI and TUI.** Relaunch `grove`.
- **Daemon.** `systemctl --user restart grove-daemon` (or stop and
  re-run `grove daemon serve`).
- **Web dashboard.** Rebuild and restart it. The prod server serves a
  pre-built bundle, so new routes stay silently absent otherwise.

A checkout install is editable, so reinstall only when dependencies
change.

## `grove-mcp` reports `No module named 'mcp'`

**Symptom.** The MCP server fails to start with `ModuleNotFoundError: No
module named 'mcp'`.

**Cause.** The MCP SDK is optional. The lean `grove[daemon]` install
omits it, so the always-present `grove-mcp` script cannot import it.

**Fix.** Install `grove[mcp]` or `grove[all]`, for example
`uv tool install --reinstall "grove[all] @
git+https://github.com/bearlike/Grove"`. The MCP client respawns the
server per connection, so no restart is needed once installed. See
[MCP server](use-mcp.md).

## Windows-native limitations

**Symptom.** `grove` runs on Windows but the TUI does not show
workspaces, or fails on tmux operations.

**Cause.** tmux does not run natively on Windows.

**Fix.** Install WSL2 and run `grove` from inside it. The
Windows-native build still supports the read-only subcommands (`grove
version`, `grove config show`, `grove debug`).
