# CLI

The CLI is Grove's full-featured shell surface. It covers the complete workspace
lifecycle (create, steer, pause, resume, respawn, kill, attach), read-only
inspection (ls, show, sessions), and host-level admin (config, auth, daemon).
Every lifecycle verb goes through the same engine the TUI uses, so the CLI, TUI,
web dashboard, and MCP are at feature parity.

Reach for the CLI to script around Grove, to drive workspaces from an agent's
own shell, to feed JSON to another tool, or to inspect what sibling agents on
the same project have been doing.

## The contract

A few properties hold across every command.

**Repo-scoped versus host-scoped.** Most commands resolve the project from the
current directory and operate on it. `grove`, `grove ls`, `grove create`,
`grove sessions`, `grove config`, and `grove debug` all walk up from `cwd` to
the enclosing git repository. They work from anywhere inside the project,
including from inside any linked worktree. Two groups are different: `grove
daemon` and `grove auth` act on the host, not on a single repo. The daemon
serves every repo Grove knows about from one process, and pairing lives in a
host-wide session store.

**Workspace resolution.** All lifecycle and inspection verbs accept a workspace
WORKSPACE argument. The engine resolves it by exact id first, then by a unique
id prefix. The eight-character prefix the tables print is almost always enough.
An ambiguous prefix exits `1` and lists the candidates, so you can extend it
without re-running `grove ls`.

**JSON first.** Every read command is built to pipe. `grove ls` and `grove
debug` are JSON-native. `grove sessions list` and `grove sessions show` print a
human table or transcript by default and switch to JSON with `--json`. `grove
sessions dump` is JSON by default. Field names are a stable contract: parse
them, do not scrape the human tables.

**Exit codes.** `0` means success. `1` means a typed Grove error: not in a git
repository, a config that failed to load, a session reference that matched
nothing, a malformed id. The message goes to stderr. `2` comes from the
argument parser for a usage mistake, an unknown flag, or a missing required
argument.

**Non-interactive.** There are no pagers and no prompts (except the `grove
kill` confirmation, which `--yes` skips). Output goes straight to stdout, errors
to stderr. Everything is safe to run unattended from a script, a CI job, or an
agent's shell.

## `grove`

Launch the TUI for the current repository.

```bash
cd /path/to/my-project
grove
```

No subcommand runs the TUI. It locates the repo root from `cwd`, builds the
workspace manager against the merged config, and hands control to the Textual
app. If `cwd` is not inside a git repository it prints the error to stderr and
exits `1`. For everything the TUI can do, see the [TUI tour](use-tui.md).

The root command also carries the standard Typer shell-completion helpers,
`--install-completion` and `--show-completion`, which install or print a
completion script for your current shell.

## Read commands

### `grove ls`

Print this repo's workspaces as JSON, one record per workspace.

```bash
grove ls
```

This is the scriptable twin of the TUI list. There are no options: it always
emits the full array to stdout, in creation order.

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

The `status` field is Grove's reconciled view. It is one of `active`, `idle`,
`paused`, `offline`, `orphaned`, or `error`. See
[status semantics](features-status.md) for the recovery path for each value.

A typical recipe: find every workspace that needs attention without opening the
TUI.

```bash
grove ls | jq -r '.[] | select(.status=="offline" or .status=="orphaned" or .status=="error") | "\(.status)\t\(.title)\t\(.branch)"'
```

### `grove show`

Inspect a single workspace: identity, git ahead/behind/diff/dirty, agent state
and turn counts, recent transcript turns, and a live pane snapshot. This is the
CLI analogue of the TUI peek rail.

```bash
grove show [WORKSPACE] [--last/-l N]
```

| Argument / option | Default | Meaning |
|---|---|---|
| `WORKSPACE` | inferred from cwd | Workspace id or unique prefix. Omit when running from inside a worktree. |
| `--last`, `-l` | 10 | Number of recent transcript turns to show. |

With no argument it infers the workspace from the current directory. With an
id it resolves by exact match then unique prefix, the same as every other
lifecycle verb.

```bash
grove show            # from inside a worktree, infers the workspace
grove show a1b2 -l 5  # by id prefix, last 5 turns
```

The output is human-readable sections (identity, git, agent, transcript, live
pane). It never mutates anything.

### `grove sessions`

Explore the coding-agent sessions recorded for this project. Think of it as
`git log` for agent conversations. Every worktree of the repo is scanned, so
a session started in a Grove workspace, in a hand-made worktree, or in the
repo root all show up in one place. Transcripts outlive worktrees, so paused
workspaces show up too. Everything here is read-only.

Three subcommands form a cost ladder. `list` is one line per session. `show`
reads one conversation as turns. `dump` emits the raw native records.

#### `grove sessions list`

List every agent session across this project's worktrees, newest first.

```bash
grove sessions list [--agent KIND] [-w PREFIX] [--since WINDOW] [-n N] [--json]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--agent` | text | all | Only sessions from this adapter kind, e.g. `claude_code`. |
| `--workspace`, `-w` | text | all | Workspace id prefix, or a case-insensitive title substring. |
| `--since` | text | all time | Only sessions modified since a relative window (`30m`, `6h`, `2d`, `1w`) or an ISO date. |
| `--limit`, `-n` | integer | unbounded | Keep the newest N rows after filtering. |
| `--json` | flag | off | Emit JSON instead of the table. |

The default is a compact table, one row per session, newest first. The `STATE`
column is the agent's live, computed-from-transcript state: `starting`,
`working`, `waiting`, `blocked`, `idle`, `error`, or `unknown`. `waiting` and
`blocked` are the ones that want a human.

```text
SESSION    AGENT        WORKSPACE            STATE     TURNS MODIFIED         TITLE / PROMPT
7b3f2c1a   claude_code  Forecast cache       waiting      14 2 minutes ago   Add an LRU layer to the forecast client
a91e0d34   claude_code  Radar overlay        working       6 just now        Wire the radar tiles onto the map
```

With `--json` you get the full metadata for each session, the same fields the
web dashboard reads. Field names are a stable contract.

A common scripted recipe: which agents are waiting for a human?

```bash
grove sessions list --json \
  | jq -r '.[] | select(.state=="waiting" or .state=="blocked") | "\(.state)\t\(.workspace_title // "-")\t\(.title // .last_prompt)"'
```

Or show only what moved in the last two hours, the five most recent:

```bash
grove sessions list --since 2h --limit 5
```

#### `grove sessions show`

Print a session's conversation as normalized turns, oldest first.

```bash
grove sessions show REF [-l N] [--json]
```

| Argument / option | Default | Meaning |
|---|---|---|
| `REF` (required) | (none) | Session id, or any unique prefix. |
| `--last`, `-l` | all turns | Print only the most recent N turns. |
| `--json` | off | Emit structured turns as JSON. |

Use `--last` to catch up on where an agent landed without reading the whole
history:

```bash
grove sessions show 7b3f2c1a --last 3
```

With `--json` the turns become structured data: the session's full metadata
under `session`, plus an ordered `turns` array.

#### `grove sessions dump`

Dump a session's raw native records (main transcript plus any sub-agent files).
This is the escape hatch for when the normalized turns are not enough, for
example to inspect exact `tool_result` payloads or token accounting.

```bash
grove sessions dump REF [--jsonl]
```

| Argument / option | Default | Meaning |
|---|---|---|
| `REF` (required) | (none) | Session id, or any unique prefix. |
| `--jsonl` | off | Stream the original transcript lines verbatim instead of the JSON object. |

A long session's transcript can be megabytes. Reach for `dump` only when you
genuinely need the raw records; for catching up, `list` and `show --last N` are
far cheaper.

```bash
grove sessions dump 7b3f2c1a --jsonl | jq -c 'select(.type=="assistant")'
```

#### `grove sessions remap`

Implemented in [`cli_sessions.py`](repo:src/grove/tui/cli_sessions.py). Pin an existing agent session as a workspace's tracked primary. This is the
manual counterpart to Grove's automatic session tracking: re-point a
workspace whose Grove-minted session died (a `/clear` rotated the id, the
process crashed) at the live or recovered session, or adopt a hand-started
session as the workspace's own.

```bash
grove sessions remap WORKSPACE SESSION
```

| Argument | Meaning |
|---|---|
| `WORKSPACE` (required) | Workspace id, or any unique prefix. |
| `SESSION` (required) | Session id, or any unique prefix, resolved in the workspace's project. |

```bash
grove sessions remap a1b2 cafef00d
```

Trusted and idempotent: the session ref is resolved in the workspace's own
project, and re-running with the same pair is a no-op. On success it prints
the workspace id, title, and the session id now tracked.

## Lifecycle commands

All lifecycle verbs accept an id prefix for the WORKSPACE argument. They make
in-process calls through the same engine the TUI uses, so the CLI, TUI,
dashboard, and MCP are at full parity.

### `grove create`

Create a workspace: a git worktree, a branch, a tmux session, and a running
agent.

```bash
grove create TITLE --agent/-a NAME [--model/-m ID] [branch flags] [--base REF] [--description/-d TEXT] [--no-init] [--prompt/-p TEXT]
```

| Option | Meaning |
|---|---|
| `TITLE` (required) | Human label; its slug seeds the worktree path and tmux session name. |
| `--agent`, `-a` (required) | Agent to launch (must match a name in your config). |
| `--model`, `-m ID` | Model id for the agent tool (e.g. claude: `sonnet`/`opus`/`haiku`, codex: `gpt-5.5`). Forwarded to the agent verbatim, never validated — the per-agent catalog only informs the choice. Blank means the tool's own default. |
| `--branch`, `-b NAME` | Create a new branch with this exact name off `--base`. |
| `--checkout`, `-c NAME` | Check out an existing local branch into the worktree. |
| `--track`, `-t REF` | Track a remote branch by creating a fresh local tracking branch. |
| `--root` | Run in the repo root on the current branch, no worktree. |
| `--base REF` | Git ref to branch off (default `HEAD`). Valid only with auto or `--branch`. |
| `--description`, `-d` | Free-form note attached to the workspace. |
| `--no-init` | Skip the init script for this create only. |
| `--prompt`, `-p` | The agent's first task, delivered race-free at boot so the workspace starts working immediately. |

The branch flags are mutually exclusive. Omitting all of them lets Grove
auto-name a branch from the title slug, which is the standard default.

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

On success it prints the new workspace id, title, agent, branch, worktree path,
and tmux session name.

### `grove message`

Send a follow-up steering message to a running workspace's agent.

```bash
grove message WORKSPACE TEXT
```

```bash
grove message a1b2 "now add a test for the empty case"
```

Mirrors the TUI's steer modal. The workspace must be running; the engine raises
a clean error if there is no live pane to deliver the message to.

### `grove pause`

Remove a workspace's worktree and tmux session while keeping the branch. The
branch is safe; `grove resume` rebuilds the worktree from it later.

```bash
grove pause WORKSPACE [--force/-f]
```

| Option | Meaning |
|---|---|
| `--force`, `-f` | Pause even with uncommitted changes in the worktree. |

Without `--force`, Grove refuses to pause a workspace that has uncommitted
changes, so no work is ever silently discarded.

```bash
grove pause a1b2
grove pause a1b2 --force    # discard uncommitted changes
```

### `grove resume`

Recreate a paused workspace's worktree from its branch and relaunch the tmux
session and agent.

```bash
grove resume WORKSPACE
```

```bash
grove resume a1b2
```

### `grove respawn`

Recreate a vanished tmux session when the worktree still exists. Use this, not
`resume`, when the agent's terminal died but the files are still on disk.

```bash
grove respawn WORKSPACE
```

```bash
grove respawn a1b2
```

### `grove kill`

Destroy a workspace: the tmux session and worktree are removed. Remote branches
are never touched.

```bash
grove kill WORKSPACE [--delete-branch | --keep-branch] [--yes/-y]
```

| Option | Meaning |
|---|---|
| `--delete-branch` | Also delete the local branch. |
| `--keep-branch` | Keep the local branch (overrides the default provenance logic). |
| `--yes`, `-y` | Skip the confirmation prompt. |

By default the engine decides based on provenance: Grove-created branches are
deleted, branches you attached via `--checkout` are kept. Override this with
`--delete-branch` or `--keep-branch`.

```bash
grove kill a1b2              # prompts for confirmation
grove kill a1b2 --keep-branch -y   # keep branch, no prompt
```

### `grove attach`

Attach your terminal to a workspace's tmux session. This replaces the current
process with `tmux attach` (or `tmux switch-client` when you are already inside
tmux). Detach with the usual tmux key (Ctrl-b d).

```bash
grove attach WORKSPACE
```

```bash
grove attach a1b2
```

## Admin commands

### `grove config show`

Print the merged effective config for the current repo, as JSON, after the full
six-layer cascade has resolved.

```bash
grove config show
grove config show | jq '.worktree.root_template'
```

### `grove config init`

Scaffold a project config at `<repo>/.grove/config.json`. One of the two CLI
commands that write to disk.

```bash
grove config init       # writes .grove/config.json
grove config init -f    # overwrite an existing config
```

By default it refuses to clobber an existing file. The stub it writes covers
the worktree root and branch prefix, a single `claude` agent, and a disabled
init script. It also writes the JSON Schema next to your user config and points
the stub's `$schema` at it, so your editor gets autocomplete immediately.

### `grove config schema`

Write the JSON Schema next to the user config, without scaffolding anything
else. Useful after upgrading Grove when the config model may have gained fields.

```bash
grove config schema           # write to disk
grove config schema --stdout  # print to stdout instead
```

### `grove daemon serve`

Run the HTTP daemon that backs the [web dashboard](use-webapp.md). It binds
loopback by default and serves every repo Grove knows about from one process.
This is a host-scoped command.

```bash
grove daemon serve              # 127.0.0.1:7421
grove daemon serve --port 7777
```

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Interface to bind. Loopback is deliberate. See [the security model](use-auth.md#the-security-model). |
| `--port` | `7421` | Port to listen on. `0` auto-picks a free one. |
| `--print-port` | off | Print the bound port to stdout once listening. Used by the local transport to discover an auto-picked port. |

In normal use you do not run this by hand; the
[systemd user service](use-webapp.md#always-on-with-systemd) keeps it up.

### `grove auth`

Approve or deny pairing requests and manage active sessions. These commands act
on the host's session store, so they work from any directory. The full pairing
story is on the [authentication](use-auth.md) page.

`grove auth pending` lists requests waiting for approval.

```bash
grove auth pending
# 7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f  code=BFCD-GH23  label='My Tablet'  state=pending  expires_at=...
```

`grove auth approve <challenge-id>` approves a request. The requesting device
picks up its token on its next poll. `grove auth deny <challenge-id>` rejects
one.

`grove auth sessions` lists the currently active sessions. `grove auth revoke
<session-id>` cuts one off; that device must pair again to return.

```bash
grove auth approve 7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f
grove auth sessions
grove auth revoke 9d21f0e4-1a2b-4c3d-8e5f-6a7b8c9d0e1f
```

### `grove version`

Print the installed Grove version.

```bash
grove version
# grove 0.1.0
```

### `grove debug`

Print the resolved paths Grove would use for this repo, plus whether the config
loaded cleanly. JSON, for easy `jq` consumption. Run this first when something
is "not loading".

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

Note that `debug` reports paths and a load flag, never the config values
themselves; for the merged values use `grove config show`.

## `GROVE_DEBUG=1`

Set the environment variable to flip the logger's stderr handler from `WARNING`
to `DEBUG`. Every `git` and `tmux` subprocess invocation, every cascade merge,
and every state-file read becomes visible on stderr. It is the companion to
`grove debug`: that tells you which files Grove will read; this shows you what
happens when it reads them.

```bash
GROVE_DEBUG=1 grove ls
```

Because the diagnostics go to stderr, they never pollute the JSON on stdout, so
you can keep piping to `jq` while you watch the trace.

## Using the CLI from an agent

If you are a coding agent with shell access inside a Grove-managed worktree,
you have the full command surface available. You can read what sibling agents
have been doing and, when authorized, drive lifecycle actions on the same fleet.

### Discover what is happening

Start by mapping the project. `grove ls` gives you the workspaces and their
status. `grove sessions list` gives you the agent conversations across all of
them. Both resolve the whole project automatically from inside any worktree.

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

### Read context cheaply, and bound the token cost

The three `sessions` subcommands are a deliberate cost ladder. Stop at the
first rung that answers your question.

1. `grove sessions list` is one line per session. It is almost always enough
   to answer "who is doing what" and "what needs attention". Filter it before
   you read anything heavier.
2. `grove sessions show <id> --last N` reads only the tail of one conversation.
   A handful of recent turns usually tells you where a sibling agent landed,
   at a fraction of the tokens.
3. `grove sessions dump <id>` is the raw records, and the output can be
   megabytes. Escalate only when you truly need the unnormalized transcript.

```bash
# Cheapest: scan the list, filtered to recent activity.
grove sessions list --since 1h

# Mid cost: read the last few turns of one sibling session.
grove sessions show a91e0d34 --last 5

# Expensive, last resort: redirect to a file, not your context.
grove sessions dump a91e0d34 --jsonl > /tmp/a91e0d34.jsonl
```

### Create and steer workspaces from a script

The lifecycle verbs are the same engine path as the TUI, so they work from any
agent shell.

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

- Always pass `--json` when you intend to parse the result, and pipe it through
  `jq`. The tables are for humans.
- Resolve a session by its full id, or by a unique prefix. The eight characters
  the table shows are usually unique and stable.
- Treat any non-zero exit as "no data": either not found, ambiguous prefix, or
  not in a git repository. Read stderr for which one. Exit `2` specifically
  means you got a flag or argument wrong.
- Climb the cost ladder: `list`, then `show --last N`, then `dump`, and stop at
  the first rung that answers your question. Redirect `dump` output to a file
  rather than into your own context.
- Use `--since` and `-w` to shrink the result before you read it, rather than
  listing everything and filtering in your head.

## See also

- [Agent activity and sessions](features-activity.md): the capability behind
  `grove sessions`, agent state, and provenance, in product terms.
- [Status semantics](features-status.md): what the `status` field from
  `grove ls` means, and the recovery path for each value.
- [Configuration cascade](features-cascade.md): how the layers `grove config
  show` resolves stack and merge.
- [Configuration reference](configure-reference.md): the field-by-field schema
  `grove config schema` generates.
- [Authentication and pairing](use-auth.md): the device side of `grove auth`.
- [Web dashboard](use-webapp.md): the browser surface `grove daemon serve`
  backs.
