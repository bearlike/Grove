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

## Windows-native limitations

**Symptom.** `grove` runs on Windows but the TUI does not show
workspaces, or fails on tmux operations.

**Cause.** tmux does not run natively on Windows.

**Fix.** Install a WSL2 distribution and run `grove` from inside it.
The Windows-native build is intentional but limited. It supports the
read-only subcommands (`grove version`, `grove config show`,
`grove debug`), so a Windows-only contributor can inspect their config
without provisioning WSL2 just for that.
