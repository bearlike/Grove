# Troubleshooting

## Fixes for common symptoms

Each entry names what you see, why it happens, and what to do.

## Fresh install crashes with `Failed to initialise configuration handler`

`grove` exits with a CRITICAL line naming `GROVE_CONFIG_LOCAL_FILE_PATH` and
`grove/entrypoints/base.py`. You installed the wrong package. PyPI's `grove` is
an unrelated log-collection framework.

```bash
uv tool uninstall grove
uv tool install "grove[daemon] @ git+https://github.com/bearlike/Grove"
```

Confirm with `grove version` and `grove debug`.

## `tmux` not found

`TmuxError: tmux binary not found on PATH`, or the TUI refuses to launch.
Either tmux is missing, or you are on Windows, where it only runs inside
WSL2.

Install with `apt install tmux` or `brew install tmux`. On Windows run
`wsl --install` and launch `grove` from inside. The read-only subcommands
(`grove version`, `grove debug`, `grove config show`) work either way.

## Worktree creation fails

`BranchError`, `BranchConflict`, or `BranchAlreadyCheckedOut`. The branch
already exists and is checked out elsewhere, its name collides with an
existing path or ref, or `git worktree add` hit a lock file or permission
problem.

`git worktree list` confirms the branch is not attached, and
`git worktree prune` clears one deleted from disk. For permission errors,
check that `worktree.root_template`'s parent is writable. If *Track remote*
failed, `git fetch && git branch -r` confirms the remote branch exists.

## Init script fails or times out

The workspace lands in `ERROR` under `fail_fast: false`, or rolls back
entirely under `fail_fast: true`. The script exited non-zero, ran past
`init_script.timeout_seconds`, or its `shell` is not installed. A daemon
unit predating Grove's PATH fix also resolves stale binaries over your
pyenv, nvm, or asdf toolchain.

A `fail_fast` failure keeps the init log, ending in the stderr that
survives rollback. For a stale daemon PATH, re-run `make systemd` and
`systemctl --user restart grove-daemon`. Attach the `init` tmux window
before it completes, or resume with `run_on_resume: true`. To reproduce
standalone:

```bash
cd /tmp/test && bash -lc "$(jq -r '.init_script.inline' /path/to/.grove/config.json)"
```

## Pause refuses on a dirty worktree

`DirtyWorktreeError` flashes on the status bar and the workspace stays
RUNNING, because `pause` removes the worktree directory and would silently
lose uncommitted work.

Switch to the `shell` window (`Ctrl-B 0/1`), commit or stash, detach, then
press ++p++ again.

## Agent window auto-closed, workspace shows OFFLINE

An ACTIVE workspace flips to OFFLINE and the footer offers only ++o++ and
++k++. The tmux session vanished: a terminal restart, a host reboot,
`tmux kill-server`, or the agent exiting.

Press ++o++ to respawn. Grove rebuilds the session with the same windows
and command. If the worktree is gone too, the workspace is ORPHANED and
only `kill` remains.

## Config file is not loaded

Changes to `.grove/config.json` do not take effect and `grove config show`
does not reflect them. Usually a higher cascade layer overrides yours, a
JSON syntax error sent Grove back to defaults, or the file sits at the
wrong path.

`grove debug` prints every path Grove checks and confirms
`config_loaded: true`. `grove config show` shows the merged result and
which layer wins. See the [configuration cascade](features-cascade.md).

## JSON Schema autocomplete not working

The IDE does not autocomplete `.grove/config.json`. Either `$schema` does not
resolve, the schema file has not been written yet, or the IDE does not read JSON
Schema at all.

`grove config schema` rewrites `${user_config_dir}/grove/config.schema.json`. Use
an absolute `$schema` path, since a relative one breaks across worktrees. VS Code
and JetBrains read JSON Schema natively; elsewhere install `redhat.vscode-yaml`.

## Workspace shows ORPHANED

`kill` is the only action offered, because the worktree directory was removed
while the workspace was running and `respawn` needs one to rebuild from.

`kill` removes the workspace and tears down any remaining tmux session. If the
branch survives, create a fresh workspace against it through the create modal's
*Existing local* path.

## Workspace shows a blank transcript or the wrong session

The transcript is empty, or replays a conversation that is not the one in the
pane. The Grove-minted session died and a successor started in the same
directory, but the pane-verified adoption gate rejected it because the
transcript's birth and the live pane disagreed. See
[`sessions.py`](repo:src/grove/core/sessions.py) and
[session adoption](features-activity.md#adoption-and-recovery).

```bash
grove sessions remap WORKSPACE SESSION
```

The TUI's ++x++ key and the dashboard's session picker do the same.

## A Grove update killed my other tmux sessions

Restarting the daemon tears down every tmux session on the box. The
`grove-daemon` unit predates the `KillMode=process` fix: the daemon forks the
shared tmux server into its own cgroup, and systemd's default
`KillMode=control-group` kills that whole cgroup on every restart.

> [!WARNING] Check before you restart
> An unfixed unit makes every daemon restart destructive to unrelated tmux
> sessions.

```bash
make systemd
systemctl --user daemon-reload
```

See [packaging](repo:packaging/CLAUDE.md).

## No agent state on the dashboard

A workspace appears on the [Activity Dashboard](features-activity.md) with only
the active/idle signal, no turn or token counts and no session history. Either
`kind` is unset or `generic`, which launches without introspection, or there is
no adapter for the `kind` you named. Grove ships adapters for Claude Code, Codex
CLI, and remote Mewbo.

Set `kind` to `claude_code`, `codex`, or `mewbo` and confirm with
`grove config show`. See
[Agents](configure-agents.md#telling-grove-what-kind-of-agent-it-is).
`grove sessions list` finds sessions whatever the `kind`.

## Web dashboard shows "unreachable"

The dashboard loads but the status bar reads "unreachable", or no workspaces ever
appear. The daemon is not running, is bound to a different host or port than the
web app expects, or `GROVE_DAEMON_URL` is stale.

Start it with `grove daemon serve` (default port `7421`) and probe with
`curl http://127.0.0.1:7421/healthz`, which returns `{"status":"ok",…}`. For a
non-default daemon, set `GROVE_DAEMON_URL` in `webapp/.env.local` or the unit's
`DAEMON_PORT` / `DAEMON_URL`, then restart. See
[web dashboard](use-webapp.md).

## Pairing code expired, or a device keeps asking to pair

The browser shows "code expired", or a paired device is bounced back to the
pairing screen. A code lives five minutes, and a session ends when
`grove auth revoke` runs or its thirty-day window lapses unused.

Request and approve a fresh code promptly, through the TUI modal or
`grove auth pending` then `grove auth approve <id>`. Tidy up with
`grove auth sessions` and `grove auth revoke`. See
[authentication and pairing](use-auth.md).

## Old UI or behavior after an update

A surface still shows the old version after you pull: a new endpoint 404s, the
TUI looks unchanged, or the dashboard lacks a feature you merged. Grove runs as
several long-lived surfaces and updating the package does not restart what is
already running.

Relaunch `grove` for the CLI and TUI. Restart the daemon with
`systemctl --user restart grove-daemon`. Rebuild the dashboard and then restart
it, since the production server serves a pre-built bundle and new routes stay
silently absent otherwise. A checkout install is editable, so reinstall only when
dependencies change.

## `grove-mcp` reports `No module named 'mcp'`

The MCP server fails to start. The SDK is optional and the lean `grove[daemon]`
install omits it, leaving the always-present `grove-mcp` script unable to import
it.

```bash
uv tool install --reinstall "grove[all] @ git+https://github.com/bearlike/Grove"
```

The MCP client respawns the server per connection, so nothing needs restarting
once it is installed. See [MCP server](use-mcp.md).

## Windows-native limitations

`grove` runs but the TUI shows no workspaces, or fails on tmux operations,
because tmux does not run natively on Windows.

Install WSL2 and run `grove` from inside it. The Windows-native build still
supports the read-only subcommands.
