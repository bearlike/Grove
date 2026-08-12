# CLI

## Every command and flag

Every verb runs the same engine as the TUI, at feature parity.

## The contract

- **Scope.** Most commands resolve the repo from `cwd` upward. `daemon`,
  `auth`, `fleet`, and `tickets owned` are host-wide.
- **Workspace resolution.** A WORKSPACE argument takes an exact id or a
  unique prefix, and an ambiguous one exits `1` and lists the candidates.
- **JSON first.** `ls`, `fleet`, and `debug` are JSON-native. `sessions list`
  and `sessions show` take `--json`, `sessions dump` defaults to it.
- **Exit codes.** `0` succeeds. `1` is a typed Grove error. `2` is a bad flag
  or a missing argument.
- **Non-interactive.** No pagers, and no prompt but `grove kill`'s
  confirmation, which `--yes` skips.

## `grove`

Launches the TUI for the repo found from `cwd`, or exits `1` outside one. See
the [TUI tour](use-tui.md). Also carries Typer's `--install-completion` and
`--show-completion`.

```bash
cd /path/to/my-project
grove
```

## Read commands

### `grove ls`

This repo's workspaces as JSON, one record each, in creation order.

```bash
grove ls
```

```json
[
  {
    "id": "forecast-cache",
    "title": "Forecast cache",
    "agent": "claude",
    "branch": "grove/forecast-cache",
    "status": "active",
    "worktree_path": "/path/to/my-project/.worktrees/forecast-cache",
    "tmux_session": "grove-forecast-cache"
  }
]
```

`status` is Grove's reconciled view, one of `active`, `idle`, `paused`,
`offline`, `orphaned`, or `error`. See [status semantics](features-status.md).

```bash
# Every workspace that needs attention, without opening the TUI.
grove ls | jq -r '.[] | select(.status=="offline" or .status=="orphaned" or .status=="error") | "\(.status)\t\(.title)\t\(.branch)"'
```

### `grove fleet`

Every workspace on this host as JSON, with lifecycle status, blended agent
activity, task phase, and ticket refs.

```bash
grove fleet
grove fleet | jq '.projects[].workspaces[] | select(.needs_attention)'
```

### `grove show`

One workspace's identity, git ahead/behind/diff/dirty, agent state and turn
counts, [task phase](features-status.md#the-third-axis-task-phase) and todo
list, recent transcript turns, and a live pane snapshot. The peek rail as a
command.

```bash
grove show [WORKSPACE] [--last/-l N]
```

| Argument / option | Default | Meaning |
|---|---|---|
| `WORKSPACE` | inferred from cwd | Id or unique prefix. Omit from inside a worktree. |
| `--last`, `-l` | 10 | Recent transcript turns to show. |

```bash
grove show            # from inside a worktree, infers the workspace
grove show a1b2 -l 5  # by id prefix, last 5 turns
```

### `grove phase`

Sets or reads a workspace's [task
phase](features-status.md#the-third-axis-task-phase), writing the file named
by `GROVE_PHASE_FILE`, the channel available everywhere.

```bash
grove phase <phase> [--note TEXT] [WORKSPACE]   # set
grove phase [WORKSPACE]                         # read
```

| Argument / option | Default | Meaning |
|---|---|---|
| `PHASE` | (none) | `scoping`, `planning`, `implementing`, `verifying`, `delivering`, or `done`. Omit to read instead of set. |
| `--note` | none | One-line note, 200 characters or less. |
| `WORKSPACE` | inferred from cwd | Id or unique prefix, resolved as in `grove show`. |

```bash
grove phase implementing --note "wiring the parser"   # from inside the worktree
grove phase                                           # read the current phase back
grove phase verifying a1b2                             # set another workspace's phase by id
```

### `grove sessions`

The sessions recorded for this project, a kind of `git log` for agent
conversations. Transcripts outlive worktrees, so paused ones show up too. The
subcommands form a cost ladder, cheapest first.

#### `grove sessions list`

Every session across this project's worktrees, newest first.

```bash
grove sessions list [--host] [--agent KIND] [-w PREFIX] [--since WINDOW] [-n N] [--json]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--host` | flag | off | Every repo on this host. See [the Session Catalog](features-activity.md#the-session-catalog-every-session-on-this-host). |
| `--agent` | text | all | This adapter kind only, e.g. `claude_code`. |
| `--workspace`, `-w` | text | all | Workspace id prefix, or case-insensitive title substring. |
| `--since` | text | all time | Modified since a window (`30m`, `6h`, `2d`, `1w`) or ISO date. |
| `--limit`, `-n` | integer | unbounded | Newest N rows after filtering. |
| `--json` | flag | off | JSON instead of the table, the metadata the dashboard reads. |

`STATE` is computed from the transcript, one of `starting`, `working`,
`waiting`, `blocked`, `idle`, `error`, or `unknown`. `waiting` or `blocked`
wants a human.

```text
SESSION    AGENT        WORKSPACE            STATE     TURNS MODIFIED         TITLE / PROMPT
7b3f2c1a   claude_code  Forecast cache       waiting      14 2 minutes ago   Add an LRU layer to the forecast client
a91e0d34   claude_code  Radar overlay        working       6 just now        Wire the radar tiles onto the map
```

```bash
# Which agents are waiting for a human?
grove sessions list --json \
  | jq -r '.[] | select(.state=="waiting" or .state=="blocked") | "\(.state)\t\(.workspace_title // "-")\t\(.title // .last_prompt)"'

# Only what moved in the last two hours, five most recent.
grove sessions list --since 2h --limit 5
```

#### `grove sessions show`

One conversation as normalized turns, oldest first.

```bash
grove sessions show REF [-l N] [--json]
```

| Argument / option | Default | Meaning |
|---|---|---|
| `REF` (required) | (none) | Session id or unique prefix. |
| `--last`, `-l` | all turns | The most recent N turns only. |
| `--json` | off | Turns as JSON, metadata under `session` plus ordered `turns`. |

```bash
grove sessions show 7b3f2c1a --last 3
```

#### `grove sessions dump`

Raw native records, the main transcript plus any sub-agent files. Megabytes
are possible, so `list` and `show` are cheaper for catching up.

```bash
grove sessions dump REF [--jsonl]
```

| Argument / option | Default | Meaning |
|---|---|---|
| `REF` (required) | (none) | Session id or unique prefix. |
| `--jsonl` | off | Original transcript lines verbatim, not the JSON object. |

```bash
grove sessions dump 7b3f2c1a --jsonl | jq -c 'select(.type=="assistant")'
```

#### `grove sessions remap`

Implemented in [`cli_sessions.py`](repo:src/grove/tui/cli_sessions.py). Pins an
existing session as a workspace's tracked primary, after a `/clear` rotated
the id or to adopt a hand-started session. Re-running the same pair is a
no-op.

```bash
grove sessions remap WORKSPACE SESSION
```

| Argument | Meaning |
|---|---|
| `WORKSPACE` (required) | Workspace id or unique prefix. |
| `SESSION` (required) | Session id or unique prefix, in the workspace's project. |

```bash
grove sessions remap a1b2 cafef00d
```

## Lifecycle commands

### `grove create`

A git worktree, a branch, a tmux session, and a running agent.

```bash
grove create TITLE --agent/-a NAME [--model/-m ID] [--runtime host|container] [branch flags] [--base REF] [--description/-d TEXT] [--no-init] [--prompt/-p TEXT] [--brief/--no-brief]
```

| Option | Meaning |
|---|---|
| `TITLE` (required) | Label whose slug seeds the worktree path and tmux session name. |
| `--agent`, `-a` (required) | Agent to launch, matching a name in your config. |
| `--model`, `-m ID` | Model id (claude: `sonnet`/`opus`/`haiku`, codex: `gpt-5.5`), forwarded verbatim, never validated. The catalog only informs the choice, blank means the tool's default. |
| `--runtime` | `host` or `container`. Omit for the default. Create-time only, never editable after. See [Container Workspaces](features-containers.md). |
| `--branch`, `-b NAME` | New branch with this exact name off `--base`. |
| `--checkout`, `-c NAME` | Existing local branch, checked out into the worktree. |
| `--track`, `-t REF` | Remote branch, via a fresh local tracking branch. |
| `--root` | Repo root on the current branch, no worktree. |
| `--base REF` | Ref to branch off (default `HEAD`). Valid only with auto or `--branch`. |
| `--description`, `-d` | Free-form note on the workspace. |
| `--no-init` | Skip the init script for this create only. |
| `--prompt`, `-p` | First task, delivered race-free at boot so it starts working immediately. |
| `--brief` / `--no-brief` | A first-turn note pointing at the `working-in-grove` skill, so the agent reports task phase and keeps tickets current. Omit for the default (`brief.enabled`, on). Create-time only, and persisted. |

The branch flags are mutually exclusive. Omitting them all auto-names a branch
from the title slug.

```bash
# Auto branch (title slug → grove/fix-login-YYYYMMDD-HHmmss)
grove create "fix login" --agent claude

# Named branch off main
grove create "fix login" --agent claude --branch fix/login --base origin/main

# Reuse an existing local branch
grove create "review pr" --agent claude --checkout existing-wip

# Track a remote branch
grove create "track ci" --agent claude --track origin/feature/ci

# In-place (no worktree, no new branch)
grove create "in place" --agent claude --root

# Start working immediately
grove create "add cache" --agent claude --prompt "add an LRU cache in front of the API client"
```

### `grove message`

Steers a running workspace's agent, the TUI's steer modal.

```bash
grove message WORKSPACE TEXT
grove message a1b2 "now add a test for the empty case"
```

### `grove pause`

Removes the worktree and tmux session, keeping the branch `grove resume`
rebuilds from. `--force` is required with uncommitted changes.

```bash
grove pause WORKSPACE [--force/-f]
```

| Option | Meaning |
|---|---|
| `--force`, `-f` | Pause even with uncommitted changes. |

```bash
grove pause a1b2
grove pause a1b2 --force    # discard uncommitted changes
```

### `grove resume`

Rebuilds a paused workspace's worktree from its branch, relaunching tmux and
the agent.

```bash
grove resume WORKSPACE
grove resume a1b2
```

### `grove respawn`

Recreates a vanished tmux session when the worktree still exists, which
`resume` does not cover. For a container it reattaches to the running agent.

```bash
grove respawn WORKSPACE
grove respawn a1b2
```

### `grove kill`

Destroys a workspace. Tmux session and worktree go, remote branches stay
untouched, and the local branch follows provenance. Grove-created branches
are deleted, `--checkout` branches kept.

```bash
grove kill WORKSPACE [--delete-branch | --keep-branch] [--yes/-y]
```

| Option | Meaning |
|---|---|
| `--delete-branch` | Delete the local branch too. |
| `--keep-branch` | Keep it, overriding the provenance default. |
| `--yes`, `-y` | Skip the confirmation prompt. |

```bash
grove kill a1b2              # prompts for confirmation
grove kill a1b2 --keep-branch -y   # keep branch, no prompt
```

### `grove attach`

Your terminal to a workspace's tmux session, replacing the current process
with `tmux attach`, or `switch-client` from inside tmux. Detach with Ctrl-b
d.

```bash
grove attach WORKSPACE
grove attach a1b2
```

### `grove shell`

An interactive shell inside a container workspace, at the agent's working
directory, replacing the current process like `grove attach`. Runs under the
container's own tmux, so it survives between visits.

```bash
grove shell WORKSPACE
grove shell a1b2
```

A host workspace has no container, so `grove shell` points at `grove attach`.
`container.shell` picks which shell runs, `bash` then `sh`.

### `grove agent`

Several agents in one containerized workspace, each in its own persistent
tmux session.

```bash
grove agent list WORKSPACE
grove agent add WORKSPACE [--agent NAME] [--name SLOT] [--model ID] [--prompt TEXT]
grove agent attach WORKSPACE SLOT
grove agent peek WORKSPACE SLOT [--lines N]
grove agent message WORKSPACE SLOT TEXT
grove agent kill WORKSPACE SLOT [--yes]
```

```bash
grove agent add a1b2 --agent codex --name reviewer
grove agent message a1b2 reviewer "review the last three commits"
```

`add` auto-names slots `agent-2`, `agent-3`, unless you pass `--name`, and
runs the workspace's own agent unless you pass `--agent`. `list` reads the
container, showing what is really running. `kill` refuses the workspace's
own agent, still `grove pause` and `grove kill`'s job.

### `grove code`

A container workspace in VS Code via the Dev Containers extension. Needs the
`code` CLI on `PATH` and a `container` runtime. See [Container
Workspaces](features-containers.md#opening-it-in-vs-code) for the
dual-editing rule.

```bash
grove code WORKSPACE
grove code a1b2
```

### `grove tickets`

Attaches, lists, and detaches issues and pull requests, and hands an issue to
the fleet. Most subcommands take one free-text `ref`, a URL, `#42`, a bare
`42`, or `owner/repo#42`.

```bash
grove tickets attach REF [--workspace/-w ID]
grove tickets list [--workspace/-w ID]
grove tickets detach REF [--workspace/-w ID]
grove tickets handover REF
grove tickets owned
grove tickets handback REF
```

```bash
grove tickets attach https://github.com/acme/api/pull/42   # cwd-inferred workspace
grove tickets attach '#42' -w a1b2
grove tickets list
```

`--workspace` is inferred from the worktree. `attach` and `detach` are
idempotent, so a repeat corrects a wrong guess.

```bash
grove tickets handover 42   # assign Grove's account, start a workspace on it
grove tickets owned         # every ticket Grove holds, host-wide, and whether work is live
grove tickets handback 42   # unassign, leaving any workspace alone
```

`handover` records the marker the poll uses so no second workspace starts.
`handback` keeps that marker so the poll does not reclaim it. Stopping the
work is still `grove kill`'s job. See [ticket
providers](features-ticket-providers.md).

## Admin commands

### `grove config show`

The merged effective config for this repo, as JSON, after the cascade
resolves.

```bash
grove config show
grove config show | jq '.worktree.root_template'
```

### `grove config add-project`

Registers a git repo (default `cwd`) in the user config's known-projects
list, so it appears in cross-project surfaces with zero workspaces and no
config of its own.

```bash
grove config add-project
grove config add-project /path/to/other-project
```

### `grove config init`

A project config at `<repo>/.grove/config.json`, refusing to clobber an
existing file without `-f`. The stub covers the worktree root and branch
prefix, a single `claude` agent, and a disabled init script.

```bash
grove config init                    # writes .grove/config.json
grove config init -f                 # overwrite an existing config
grove config init --with-onboarding  # also install skills + register the MCP server
```

### `grove skills install`

Grove's bundled skills, copied into Claude and Codex skill directories.

```bash
grove skills install                          # asks where to install
grove skills install --target project         # user | project | all
grove skills install -t user --agent claude   # claude | codex | all
```

### `grove mcp install`

This install as an MCP server via `claude mcp add` or `codex mcp add`, with
the same `--target` and `--agent` flags.

```bash
grove mcp install
grove mcp install --target project --agent claude
```

### `grove init devcontainer`

`.devcontainer/devcontainer.json` from Grove's packaged default container
config, the remedy every "default container" notice points at. See
[Container Workspaces](features-containers.md).

```bash
grove init devcontainer          # writes .devcontainer/devcontainer.json
grove init devcontainer --force  # overwrite an existing one
```

### `grove config schema`

The JSON Schema, written next to the user config and nothing else. Useful
after an upgrade, when the config model may have gained fields.

```bash
grove config schema           # write to disk
grove config schema --stdout  # print to stdout instead
```

### `grove daemon serve`

The HTTP daemon behind the [web dashboard](use-webapp.md), serving every repo
from one process. The [systemd user
service](use-webapp.md#always-on-with-systemd) normally keeps it up.

```bash
grove daemon serve              # 127.0.0.1:7421
grove daemon serve --port 7777
```

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Interface to bind. Loopback is deliberate. See [the security model](use-auth.md#the-security-model). |
| `--port` | `7421` | Port to listen on. `0` auto-picks a free one. |
| `--print-port` | off | Print the bound port once listening, so the local transport finds an auto-picked one. |

### `grove auth`

Pairing requests and active sessions. See [authentication](use-auth.md).

- `grove auth pending` lists requests waiting for approval.
- `grove auth approve <challenge-id>` approves one.
- `grove auth deny <challenge-id>` rejects one.
- `grove auth sessions` lists active sessions.
- `grove auth revoke <session-id>` cuts one off.

```bash
grove auth pending
# 7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f  code=BFCD-GH23  label='My Tablet'  state=pending  expires_at=...

grove auth approve 7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f
grove auth sessions
grove auth revoke 9d21f0e4-1a2b-4c3d-8e5f-6a7b8c9d0e1f
```

### `grove doctor`

The host against what Grove's container runtime needs, the Docker daemon,
Compose v2, and `@devcontainers/cli`, with a hint per failure.

```bash
grove doctor           # human-readable table
grove doctor --json    # for scripts
```

Exit code `0` means every required check passed. The two advisory rows,
`in-container tmux` and `container firewall`, are bundles Grove builds
itself, so a missing one is not a fault.

### `grove version`

The installed Grove version.

```bash
grove version
# grove 0.1.0
```

### `grove debug`

The resolved paths Grove would use for this repo, plus whether the config
loaded cleanly. Run it first when something "isn't loading".

```bash
grove debug
```

```json
{
  "user_config_path": "/home/user/.config/grove/config.json",
  "user_state_path": "/home/user/.local/state/grove/state.json",
  "user_schema_path": "/home/user/.config/grove/config.schema.json",
  "project_config_path": "/path/to/my-project/.grove/config.json",
  "project_local_config_path": "/path/to/my-project/.grove/config.local.json",
  "repo_root": "/path/to/my-project",
  "config_loaded": true
}
```

## `GROVE_DEBUG=1`

Flips the logger's stderr handler from `WARNING` to `DEBUG`, so every `git`
and `tmux` invocation, cascade merge, and state-file read becomes visible,
keeping stdout JSON pipeable.

```bash
GROVE_DEBUG=1 grove ls
```

## Using the CLI from an agent

A coding agent with shell access inside a Grove-managed worktree has the full
command surface, reading what siblings are doing and, when authorized,
driving the same fleet.

### Discover what is happening

```bash
# Every workspace in this project, with its reconciled status.
grove ls

# Every agent session across every worktree, newest first.
grove sessions list

# Just the sibling sessions: drop your own branch, keep the others.
MY_BRANCH=$(git branch --show-current)
grove sessions list --json \
  | jq -r --arg me "$MY_BRANCH" '
      .[] | select(.git_branch != $me)
      | "\(.session_id[0:8])  \(.state)  \(.workspace_title // "-")  \(.title // .last_prompt // "")"'
```

### Read context cheaply

Stop at the first rung that answers your question.

```bash
# Cheapest: scan the list, filtered to recent activity.
grove sessions list --since 1h

# Mid cost: read the last few turns of one sibling session.
grove sessions show a91e0d34 --last 5

# Expensive, last resort: redirect to a file, not your context.
grove sessions dump a91e0d34 --jsonl > /tmp/a91e0d34.jsonl
```

### Create and steer from a script

```bash
# Spin up a new workspace with an initial task.
grove create "add retry logic" --agent claude \
  --prompt "wrap the API client's fetch method with exponential backoff"

# Steer a running workspace by id prefix.
grove message a1b2 "now add a test for the retry case"

# Check its transcript to see where it landed.
grove sessions show a1b2 --last 3

# Pause it when the task is done.
grove pause a1b2
```

### Rules of thumb

- Pass `--json` whenever you parse the result. Tables are for humans.
- Treat a non-zero exit as "no data", meaning not found, ambiguous prefix, or
  no git repository.
- Redirect `dump` to a file, not your context, and use `--since` and `-w` to
  shrink a result first.

## See also

- [Agent activity and sessions](features-activity.md), `grove sessions`.
- [Status semantics](features-status.md), `grove ls`.
- [Task phase](features-status.md#the-third-axis-task-phase), `grove phase`.
- [Ticket providers](features-ticket-providers.md), `grove tickets`.
- [Configuration cascade](features-cascade.md), `grove config show`.
- [Configuration reference](configure-reference.md), `grove config schema`.
- [Authentication and pairing](use-auth.md), `grove auth`.
- [Web dashboard](use-webapp.md), `grove daemon serve`.
