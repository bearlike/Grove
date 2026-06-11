# Troubleshooting

Common failures, their causes, and the fix.

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

**Fix.**

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

## No agent state on the dashboard

**Symptom.** A workspace shows up on the [Activity
Dashboard](features-activity.md) but carries no agent state, no turn or
token counts, and no session history. Only the basic active/idle signal
moves.

**Causes.**

- The agent's spec has no `kind`, or `kind: "generic"`. Generic agents
  are launched but never introspected; that is the contract.
- The agent genuinely is not Claude Code, and no adapter exists for it
  yet. Terminal-output activity is all Grove can offer.

**Fix.**

- Add `"kind": "claude_code"` to the agent's spec if it is Claude Code
  or speaks its session format. See [Agents](configure-agents.md#telling-grove-what-kind-of-agent-it-is).
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

## Windows-native limitations

**Symptom.** `grove` runs on Windows but the TUI does not show
workspaces, or fails on tmux operations.

**Cause.** tmux does not run natively on Windows.

**Fix.** Install a WSL2 distribution and run `grove` from inside it.
The Windows-native build is intentional but limited. It supports the
read-only subcommands (`grove version`, `grove config show`,
`grove debug`), so a Windows-only contributor can inspect their config
without provisioning WSL2 just for that.
