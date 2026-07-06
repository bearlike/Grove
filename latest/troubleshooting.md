# Troubleshooting

Common failures, their causes, and the fix.

## Fresh install crashes with `Failed to initialise configuration handler`

**Symptom.** Right after installing, `grove` exits with a CRITICAL log
line such as `Failed to initialise configuration handler ...
GROVE_CONFIG_LOCAL_FILE_PATH field required`, and the traceback mentions
`grove/entrypoints/base.py`.

**Cause.** You installed the wrong product. The name `grove` on PyPI
belongs to an unrelated log-collection framework, so a bare
`uv tool install grove` (or `pipx install grove`, `pip install grove`)
pulls that package instead of this one. Grove is not published on PyPI
and installs straight from the repo.

**Fix.** Uninstall the imposter, then install from the repo:

```bash
uv tool uninstall grove
uv tool install "grove[daemon] @ git+https://github.com/bearlike/Grove"
```

Verify with `grove version` (it prints `grove <version>`) and
`grove debug` (it prints the config and state paths Grove will use).

## `tmux` not found

**Symptom.** `TmuxError: tmux binary not found on PATH`, or the TUI
refuses to launch with a clear error.

**Causes.**

- `tmux` is not installed on this machine.
- You are running on Windows-native. The TUI requires tmux, which only
  runs inside WSL2.

**Fix.**

- Linux: `apt install tmux` (or your distro's equivalent).
- macOS: `brew install tmux`.
- Windows: install a WSL2 distribution (`wsl --install`) and run
  `grove` from inside it. The Windows-native binary supports the
  non-tmux subcommands (`grove version`, `grove debug`,
  `grove config show`).

## Worktree creation fails

**Symptom.** `BranchError`, `BranchConflict`, or `BranchAlreadyCheckedOut`.

**Causes.**

- Branch already exists locally and is checked out elsewhere.
- Branch name conflicts with an existing path or ref.
- `git worktree add` returns non-zero for some other reason (lock file,
  permissions, missing remote tracking).

**Fix.**

- Run `git worktree list` and confirm the branch is not already
  attached.
- If a stale worktree was deleted from disk without `git worktree prune`,
  run `git worktree prune` and try again.
- For permission errors, verify the parent directory of
  `worktree.root_template` is writable.
- If the create modal's *Track remote* path failed, verify the remote
  branch exists with `git fetch && git branch -r`.

## Init script fails or times out

**Symptom.** Workspace lands in `ERROR` state (with `fail_fast: false`)
or rolls back entirely (with `fail_fast: true`). The init outcome shows
FAILED or TIMEOUT.

**Causes.**

- The script exited non-zero.
- The script ran past `init_script.timeout_seconds`.
- The configured `shell` (bash, sh, zsh) is not installed.
- The daemon runs as a `systemd --user` service installed before
  Grove baked a PATH into the unit, so the script resolves stale
  system binaries instead of your pyenv/nvm/asdf-managed toolchain.

**Fix.**

- Read the error: a `fail_fast` failure names the kept init log and
  ends with the log's tail (the failing command's stderr). The log
  survives the rollback, so open it for the full output.
- If the failing tool resolves to an old system version only under the
  daemon, re-run `make systemd` from your normal shell (the unit bakes
  that shell's PATH at install time; see `packaging/systemd/README.md`)
  and restart with `systemctl --user restart grove-daemon`.
- Attach to the workspace before init completes (or pause and resume
  with `run_on_resume: true`) and watch the `init` tmux window output.
- Run the script standalone in a worktree-shaped environment to
  reproduce: `cd /tmp/test && bash -lc "$(jq -r '.init_script.inline'
  /path/to/.grove/config.json)"`.
- Bump `timeout_seconds` if the script is genuinely slow (lockfile
  install on a cold cache, dependency build).

## Pause refuses on a dirty worktree

**Symptom.** `DirtyWorktreeError` flashed on the status bar. The
workspace stays RUNNING.

**Cause.** `pause` removes the worktree directory, which would silently
lose any uncommitted work. Grove refuses.

**Fix.** Switch to the workspace's `shell` window (`Ctrl-B 0/1`),
commit or stash your work (`git commit -am '…'` or `git stash push -u`),
detach, then press `p` again.

## Agent window auto-closed; workspace shows OFFLINE

**Symptom.** A workspace that was ACTIVE flips to OFFLINE. The
contextual footer offers `o` (respawn) and `k` (kill) only.

**Cause.** The tmux session vanished externally. The terminal restarted,
the host rebooted, someone ran `tmux kill-server`, or the agent process
exited and the window auto-closed without a long-running process to keep
it alive.

**Fix.** Press `o` to respawn. Grove rebuilds the tmux session from
scratch with the same windows and the same agent command. If the
worktree directory itself is also gone, the workspace is ORPHANED and
respawn no longer applies. `kill` is the only path forward.

## Config file is not loaded

**Symptom.** Changes you made to `.grove/config.json` do not take
effect. `grove config show` does not reflect them.

**Causes.**

- Wrong cascade layer (a setting in the user layer is being overridden
  by the project layer, for example).
- JSON syntax error. The file failed validation silently and Grove fell
  back to defaults.
- File at the wrong path. The cascade looks at specific locations.

**Fix.**

- Run `grove debug` to print every path Grove checks and confirm
  `config_loaded: true`.
- Run `grove config show` to see the merged result and identify which
  layer wins for a given field.
- See the [configuration cascade](features-cascade.md) page if a layer
  is not taking effect.

## JSON Schema autocomplete not working

**Symptom.** The IDE does not autocomplete `.grove/config.json` keys.

**Causes.**

- The `$schema` path in the config does not resolve.
- The schema file has not been written yet (fresh install).
- The IDE does not read JSON Schema from `$schema` (rare).

**Fix.**

- Run `grove config schema` to rewrite
  `${user_config_dir}/grove/config.schema.json`.
- Use an absolute path in `$schema`. Relative paths are easier to
  break across worktrees.
- Verify the IDE plugin (VS Code's built-in JSON support, the
  `redhat.vscode-yaml` extension for YAML, JetBrains' built-in JSON
  schema support) is enabled.

## Workspace shows ORPHANED

**Symptom.** `kill` is the only available action.

**Cause.** The worktree directory has been removed from disk while the
workspace was still running. Grove cannot recover the work; `respawn`
needs an existing worktree to rebuild from.

**Fix.** `kill` removes the workspace from Grove's state and tears down
any remaining tmux session. If the branch still exists in your repo,
create a fresh workspace against it via the create modal's *Existing
local* path.

## Workspace shows a blank transcript / wrong session

**Symptom.** A workspace's transcript is empty, or replays a conversation
that is not the one running in its pane.

**Cause.** The Grove-minted session died and a successor session started in
the same directory, but Grove's pane-verified adoption gate rejected it (the
transcript birth and the workspace's live pane didn't agree), so the
workspace's session pointer is stale. See [`sessions.py`](repo:src/grove/core/sessions.py)
for the adoption gate and [Session adoption and recovery](features-activity.md#adoption-and-recovery).

**Fix.** Re-point the workspace at the correct live session:

```bash
grove sessions remap WORKSPACE SESSION
```

Or use the TUI's `x` key on the workspace, or the remap picker in the web
dashboard's session-picker empty state.

## A Grove update killed my other tmux sessions

**Symptom.** Restarting or updating Grove's daemon tears down every tmux
session on the box, not just the ones Grove manages.

**Cause.** The `grove-daemon` systemd unit was installed before the
`KillMode=process` fix landed. The daemon forks the shared default tmux
server into its own cgroup, and systemd's default `KillMode=control-group`
kills that whole cgroup, tmux server included, on every
`systemctl restart grove-daemon`.

> [!WARNING] Check before you restart
> If your unit predates this fix, any daemon restart is destructive to
> unrelated tmux sessions. Apply the fix first.

**Fix.** Re-render the unit with the fix baked in, then reload systemd:

```bash
make systemd
systemctl --user daemon-reload
```

See [packaging](repo:packaging/CLAUDE.md) for the durable KillMode lesson.

## No agent state on the dashboard

**Symptom.** A workspace shows up on the [Activity
Dashboard](features-activity.md) but carries no agent state, no turn or
token counts, and no session history. Only the basic active/idle signal
moves.

**Causes.**

- The agent's spec has no `kind`, or `kind: "generic"`. Generic agents
  are launched but never introspected; that is the contract.
- The agent has no adapter for its kind yet. Grove ships adapters for
  Claude Code, the Codex CLI, and remote Mewbo sessions; anything else
  gets terminal-output activity only.

**Fix.**

- Set the agent's `kind` to the matching adapter (`claude_code`,
  `codex`, or `mewbo`) if the tool speaks one of those session formats.
  See [Agents](configure-agents.md#telling-grove-what-kind-of-agent-it-is).
- Confirm the merged result with `grove config show` and check the
  agent's `kind` in the output.
- Session history is broader than live state: `grove sessions list`
  discovers recorded sessions in the project's worktrees regardless of
  any workspace's `kind`.

## Web dashboard shows "unreachable"

**Symptom.** The dashboard loads but the status bar reads "unreachable",
or no workspaces ever appear.

**Causes.**

- The daemon is not running, or is bound to a different host or port than
  the web app expects.
- A systemd-hosted web app points at a daemon that has stopped, or at a
  stale `GROVE_DAEMON_URL`.

**Fix.**

- Start the daemon with `grove daemon serve` and confirm the port (default
  `7421`).
- Probe the daemon directly: `curl http://127.0.0.1:7421/healthz` should
  return `{"status":"ok",…}`.
- If the web app talks to a non-default daemon, set `GROVE_DAEMON_URL` in
  `webapp/.env.local` (or the `DAEMON_PORT` / `DAEMON_URL` Make variables for
  the systemd unit) and restart it.
- See the [web dashboard](use-webapp.md) page for the two-process layout.

## Pairing code expired, or a device keeps asking to pair

**Symptom.** The browser shows "code expired", or a previously paired device
is bounced back to the pairing screen.

**Causes.**

- A pairing code lives for five minutes; approving later than that fails.
- The session was revoked with `grove auth revoke`, or its thirty-day window
  lapsed without use.

**Fix.**

- Request a fresh code in the browser and approve it promptly, either from the
  TUI modal or with `grove auth pending` then `grove auth approve <id>`.
- Confirm the code shown on the host matches the one on the device before
  approving.
- List and tidy sessions with `grove auth sessions` and `grove auth revoke`.
- See [authentication & pairing](use-auth.md) for the full flow.

## Old UI or behavior after an update

**Symptom.** You pulled new code or upgraded Grove, but a surface still
shows the old version. A new endpoint returns 404, the TUI looks
unchanged, or the dashboard does not have a feature you just merged.

**Cause.** Grove runs as three long-lived surfaces, and each refreshes on
its own. Updating the package does not restart what is already running.

**Fix.** Refresh the surface that is stale.

- **CLI and TUI.** Relaunch `grove`. A running TUI keeps the old code
  until you quit and start it again.
- **Daemon.** Restart it: `systemctl --user restart grove-daemon` (or
  stop and re-run `grove daemon serve`). A green `pytest` with a 404 from
  the live daemon is the classic tell.
- **Web dashboard.** Rebuild and restart it. The prod server serves a
  pre-built bundle, so new routes or components are silently absent until
  you rebuild and restart the web process.

If you installed from a checkout, this is an editable install: you only
need a reinstall when dependencies change, not on every code pull.

## `grove-mcp` reports `No module named 'mcp'`

**Symptom.** The MCP server fails to start with `ModuleNotFoundError: No
module named 'mcp'`.

**Cause.** The MCP SDK is an optional extra. The lean `grove[daemon]`
install deliberately omits it, so the always-present `grove-mcp` script
cannot import its dependency.

**Fix.** Install an extra that includes the MCP SDK: `grove[mcp]` or
`grove[all]` (the latter pulls daemon plus client plus MCP). Reinstall,
for example `uv tool install --reinstall "grove[all] @
git+https://github.com/bearlike/Grove"`. The MCP client respawns the
server per connection, so no separate restart is needed once the extra is
present. See the [MCP server](use-mcp.md) page.

## Windows-native limitations

**Symptom.** `grove` runs on Windows but the TUI does not show
workspaces, or fails on tmux operations.

**Cause.** tmux does not run natively on Windows.

**Fix.** Install a WSL2 distribution and run `grove` from inside it.
The Windows-native build is intentional but limited. It supports the
read-only subcommands (`grove version`, `grove config show`,
`grove debug`), so a Windows-only contributor can inspect their config
without provisioning WSL2 just for that.
