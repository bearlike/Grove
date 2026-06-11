# CLI

The TUI is Grove's primary surface, the place you create, attach, pause, and
kill workspaces. The CLI is the other half: a small, read-only set of views
plus the host-level commands that run the daemon and manage pairing. Reach for
it to script around Grove, to debug a config that is not loading, to feed JSON
to another tool, or, if you are a coding agent, to read what the other agents
on this project have been doing. Think of the CLI as the plumbing and the TUI
as the porcelain.

## The contract

A few properties hold across every command, and they are worth internalizing
before the per-command reference.

**Repo-scoped versus host-scoped.** Most commands resolve the project from the
current directory and operate on it. `grove`, `grove ls`, `grove sessions`,
`grove config`, and `grove debug` all walk up from `cwd` to the enclosing git
repository, so they work from anywhere inside the project, including from
inside any linked worktree. Two groups are different. `grove daemon` and
`grove auth` act on the host, not on a single repo. The daemon serves every
repo Grove knows about from one process, and pairing lives in a host-wide
session store, so those commands run the same from any directory.

**Read-only by construction.** Nothing under `grove ls`, `grove sessions`,
`grove config show`, `grove config schema --stdout`, or `grove debug` writes,
launches, moves, or deletes anything. They read state files, transcripts, and
git, and they print. Lifecycle actions, the ones that mutate worktrees and
tmux sessions, live in the TUI only. The two commands that do touch disk say so
plainly: `grove config init` scaffolds a file, and `grove config schema`
without `--stdout` writes the schema next to your user config.

**JSON first.** Every read command is built to pipe. `grove ls` and
`grove debug` are JSON-native. `grove sessions list` and `grove sessions show`
print a human table or transcript by default and switch to JSON with `--json`.
`grove sessions dump` is JSON by default. Field names are a stable contract:
parse them, do not scrape the human tables.

**Exit codes.** The convention is the usual three. `0` means success. `1`
means a typed Grove error: not in a git repository, a config that failed to
load, a session reference that matched nothing, a malformed id. The message
goes to stderr. `2` comes from the argument parser for a usage mistake, an
unknown flag or a missing required argument. So a script can branch on "did it
work" with a single check, and an agent can treat any non-zero exit as "this
did not return data, read stderr".

**Non-interactive.** There are no pagers and no prompts. Output goes straight
to stdout, errors to stderr. Everything is safe to run unattended from a
script, a CI job, or an agent's shell.

## `grove`

Launch the TUI for the current repository.

```bash
cd /home/me/code/weather-app
grove
```

The default callback runs even when you pass no subcommand. It locates the repo
root from `cwd`, builds the workspace manager against the merged config, and
hands it to the Textual app. If `cwd` is not inside a git repository it prints
the error to stderr and exits `1`. For everything the TUI can do, see the
[TUI tour](use-tui.md).

The root command also carries the standard Typer shell-completion helpers,
`--install-completion` and `--show-completion`, which install or print a
completion script for your current shell.

## `grove ls`

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
    "worktree_path": "/home/me/code/weather-app/.worktrees/forecast-cache",
    "tmux_session": "grove-forecast-cache"
  }
]
```

The `status` field is Grove's reconciled view, not a raw stored value. It is
one of `active`, `idle`, `paused`, `offline`, `orphaned`, or `error`. The
persisted intent (what a lifecycle action last wrote) and this computed view
(what is true right now) are two different things; the
[status semantics](features-status.md) page names every value and the recovery
path for each.

A typical recipe is to find every workspace that needs attention, the offline,
orphaned, and errored ones, without opening the TUI:

```bash
grove ls | jq -r '.[] | select(.status=="offline" or .status=="orphaned" or .status=="error") | "\(.status)\t\(.title)\t\(.branch)"'
```

## `grove sessions`

Explore the coding-agent sessions recorded for this project. Think of it as
`git log` for agent conversations. Every worktree of the repo is scanned, so a
session started in a Grove workspace, in a hand-made worktree, or in the repo
root all show up in one place, and so do the sessions of paused workspaces
whose worktree is gone, because transcripts outlive worktrees. It works from
any directory inside the project. Everything here is read-only: the explorer
composes the agent adapters' read-only scans and never launches, mutates, or
deletes a thing.

Three subcommands form a ladder from cheap to expensive. `list` is one line
per session. `show` reads one conversation as turns. `dump` emits the raw
native records. The [agent activity](features-activity.md) page covers the
capability; this is the command reference.

### `grove sessions list`

List every agent session across this project's worktrees, newest first.

```bash
grove sessions list [--agent KIND] [-w PREFIX] [--since WINDOW] [-n N] [--json]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--agent` | text | all | Only sessions from this adapter kind, for example `claude_code`. |
| `--workspace`, `-w` | text | all | Workspace id prefix, or a case-insensitive title substring. |
| `--since` | text | all time | Only sessions modified since a relative window (`30m`, `6h`, `2d`, `1w`) or an ISO date. |
| `--limit`, `-n` | integer | unbounded | Keep the newest N rows after filtering. |
| `--json` | flag | off | Emit JSON instead of the table. |

The default is a compact table, one row per session, newest first. The columns
are the short (8-character) session id, the agent kind, the owning workspace
(its title or branch, or `-` when Grove does not manage that directory), the
live state, the human turn count, how long ago the transcript was last
modified, and the session title or its latest prompt.

```text
SESSION    AGENT        WORKSPACE            STATE     TURNS MODIFIED         TITLE / PROMPT
7b3f2c1a   claude_code  Forecast cache       waiting      14 2 minutes ago   Add an LRU layer to the forecast client
a91e0d34   claude_code  Radar overlay        working       6 just now        Wire the radar tiles onto the map
c0ffee12   claude_code  -                    idle         31 3 hours ago     Investigate the flaky geocoding test
```

The `STATE` column is the agent's live, computed-from-transcript state, a
separate axis from the workspace `status` you get from `grove ls`. It is one of
`starting`, `working`, `waiting`, `blocked`, `idle`, `error`, or `unknown`.
`waiting` and `blocked` are the ones that want a human: the turn ended, or the
agent is sitting on an explicit prompt. See
[agent activity](features-activity.md#what-grove-watches) for what each state
means.

`--since` accepts either a relative window or an ISO date. The relative forms
are an integer followed by `m`, `h`, `d`, or `w`; anything else is parsed as an
ISO 8601 date or datetime, and a bare date with no timezone is read in your
local zone. A value that is neither is a usage error (exit `2`).
`--workspace` resolves a workspace either by an id prefix or by a substring of
its title, so `-w forecast` and `-w "Forecast cache"` both find the same one.

With `--json` you get the full metadata for each session, the same fields the
web dashboard reads. This is what you pipe to `jq`:

```json
[
  {
    "session_id": "7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f",
    "agent": "claude_code",
    "provenance": "grove_launched",
    "workspace_id": "forecast-cache",
    "workspace_title": "Forecast cache",
    "workspace_branch": "grove/forecast-cache",
    "cwd": "/home/me/code/weather-app/.worktrees/forecast-cache",
    "git_branch": "grove/forecast-cache",
    "transcript_path": "/home/me/.claude/projects/-home-me-code-weather-app--worktrees-forecast-cache/7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f.jsonl",
    "created_at": "2026-06-11T09:14:02+00:00",
    "modified_at": "2026-06-11T11:42:55+00:00",
    "size_bytes": 184320,
    "title": "Add an LRU layer to the forecast client",
    "first_prompt": "Let's add an LRU cache in front of the forecast API client.",
    "last_prompt": "Can you also add a metrics counter for cache hits?",
    "state": "waiting",
    "human_turns": 14,
    "assistant_replies": 41,
    "tool_calls": 96,
    "model": "claude-sonnet-4-5"
  }
]
```

The `session_id` in JSON is the full id; the table shows only its first eight
characters to stay narrow. The `provenance` field records how Grove came to
know about the session. `grove_launched` means Grove minted the session id when
it created the workspace, the deterministic case. `hook_discovered` and
`fs_discovered` are sessions Grove adopted out of band: a hook reported one, or
a filesystem scan found a transcript Grove never started. The
[agent activity](features-activity.md#session-history) page describes the same
distinction in product terms.

A common scripted recipe is "which agents on this project are waiting for me?":

```bash
grove sessions list --json \
  | jq -r '.[] | select(.state=="waiting" or .state=="blocked") | "\(.state)\t\(.workspace_title // "-")\t\(.title // .last_prompt)"'
```

Or "show me only what moved in the last two hours, the five most recent":

```bash
grove sessions list --since 2h --limit 5
```

### `grove sessions show`

Print a session's conversation as normalized turns, oldest first.

```bash
grove sessions show REF [-l N] [--json]
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `REF` (required) | text | (none) | Session id, or any unique prefix of one. |
| `--last`, `-l` | integer | all turns | Print only the most recent N turns. |
| `--json` | flag | off | Emit structured turns as JSON. |

`REF` is resolved by exact id first, then by unique prefix, so the eight
characters the table shows are almost always enough to name a session.
Resolution can fail in two ways, and the message tells you which. If nothing
matches you get `no session matches '<ref>' in this project`; if the prefix is
ambiguous you get `session ref '<ref>' is ambiguous:` followed by the candidate
ids, so you can extend the prefix without re-listing. Both exit `1`.

The default output is the conversation rendered as turns: a header line with
the id, the agent, the workspace, and the branch, then each of your prompts
(marked `❯`) followed by the assistant replies (`⏺`) and tool calls (`⚒`) that
ran before your next prompt. Long text is truncated for readability.

```text
7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f · claude_code · Forecast cache · grove/forecast-cache
title: Add an LRU layer to the forecast client

── turn 1 2026-06-11T09:14:02
❯ Let's add an LRU cache in front of the forecast API client.
  ⏺ I'll start by reading the existing client to see how requests are made.
  ⚒ Read(src/weather/forecast_client.py)
  ⏺ The client has no caching layer. I'll add a bounded LRU keyed by coordinates.
  ⚒ Edit(src/weather/forecast_client.py)
```

Use `--last` to read just the tail of a long session, which is the cheap way to
catch up on where an agent landed without scrolling through its whole history:

```bash
grove sessions show 7b3f2c1a --last 3
```

With `--json` the turns become structured data. The payload is the session's
full listing metadata (the same object `sessions list --json` produces) under
`session`, plus an ordered `turns` array. Each turn carries the human
`user_text` (empty for a leading continuation block in a resumed session), the
turn's `started_at`, and an `entries` array of the assistant and tool rows,
each a `role` (`assistant` or `tool`) and its `text`.

```json
{
  "session": {
    "session_id": "7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f",
    "agent": "claude_code",
    "provenance": "grove_launched",
    "workspace_title": "Forecast cache",
    "state": "waiting",
    "human_turns": 14,
    "model": "claude-sonnet-4-5"
  },
  "turns": [
    {
      "user_text": "Let's add an LRU cache in front of the forecast API client.",
      "started_at": "2026-06-11T09:14:02+00:00",
      "entries": [
        { "role": "assistant", "text": "I'll start by reading the existing client." },
        { "role": "tool", "text": "Read(src/weather/forecast_client.py)" }
      ]
    }
  ]
}
```

The `session` object above is trimmed for the example; in practice it carries
every field listed under `sessions list --json`.

### `grove sessions dump`

Dump a session's raw native records: the main transcript plus any sub-agent
files. This is the escape hatch for when the normalized turns are not enough,
for example to inspect the exact `tool_result` payloads or token accounting an
agent recorded.

```bash
grove sessions dump REF [--jsonl]
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `REF` (required) | text | (none) | Session id, or any unique prefix (resolved like `show`). |
| `--jsonl` | flag | off | Stream the original transcript lines verbatim instead of the JSON object. |

The default output is one self-describing JSON object: the resolved
`session_id` plus a `files` array, each entry a transcript `path` and its parsed
`records`. The shape is stable whether or not sub-agent files exist (the array
is simply longer when they do, main transcript first).

```json
{
  "session_id": "7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f",
  "files": [
    {
      "path": "/home/me/.claude/projects/-home-me-code-weather-app--worktrees-forecast-cache/7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f.jsonl",
      "records": [
        { "type": "user", "message": { "role": "user", "content": "..." } },
        { "type": "assistant", "message": { "role": "assistant", "content": "..." } }
      ]
    }
  ]
}
```

`--jsonl` streams the raw transcript lines untouched (main transcript first,
then sub-agent files), one JSON object per line, which is the form `jq -c` and
line-oriented tools expect:

```bash
grove sessions dump 7b3f2c1a > session.json
grove sessions dump 7b3f2c1a --jsonl | jq -c 'select(.type=="assistant")'
```

A word of warning: a long session's transcript can be megabytes. Reach for
`dump` only when you genuinely need the raw records; for catching up, `list`
and `show --last N` are far cheaper. If the session has no transcript file on
disk yet (a just-started workspace in the `starting` state), `dump` prints
`no transcript files on disk yet` to stderr and exits `1`.

## `grove config show`

Print the merged effective config for the current repo, as JSON.

```bash
grove config show
```

This is the config after the full six-layer cascade has resolved: built-in
defaults, then the user, project, project-local, environment-variable, and
flag layers folded together. It is the single best way to answer "what value is actually in
effect here, and which layer won". For how the layers stack and merge, see the
[configuration cascade](features-cascade.md). If the config fails to load, the
error goes to stderr and the command exits `1`.

```bash
grove config show | jq '.worktree.root_template, .tmux.activity_threshold_seconds'
```

## `grove config init`

Scaffold a project config at `<repo>/.grove/config.json`. This is one of the
two CLI commands that write to disk.

```bash
grove config init       # writes .grove/config.json plus the user-side schema
grove config init -f    # overwrite an existing project config
```

| Option | Default | Meaning |
|---|---|---|
| `--force`, `-f` | off | Overwrite an existing `.grove/config.json`. |

By default it refuses to clobber an existing file: if `<repo>/.grove/config.json`
is already there it prints `… already exists; pass --force to overwrite` and
exits `1`. The stub it writes covers the worktree root and branch prefix, a
single `claude` agent, and a disabled init script, enough to start editing. The
same call also writes the JSON Schema next to your user config and points the
stub's `$schema` at it, so your editor gets autocomplete and validation
immediately. Running outside a git repository is an error (exit `1`).

## `grove config schema`

Rewrite the JSON Schema next to the user config, without scaffolding anything
else. Useful after upgrading Grove, when the config model may have gained a
field your editor does not know about yet.

```bash
grove config schema           # write to disk (the default)
grove config schema --stdout  # print to stdout instead
```

| Option | Default | Meaning |
|---|---|---|
| `--stdout` | off | Print the schema to stdout instead of writing it to disk. |

The on-disk form (no flag) writes the schema and prints the path it wrote. The
`--stdout` form prints the schema and touches nothing; the docs build pipes
exactly that output into a hook that renders the
[configuration reference](configure-reference.md), which is why that page can
never drift from the live model.

## `grove daemon serve`

Run the HTTP daemon that backs the [web dashboard](use-webapp.md) and any
remote client. It binds loopback by default and serves every repo Grove knows
about from one process. This is a host-scoped command, not tied to any repo.

```bash
grove daemon serve              # 127.0.0.1:7421
grove daemon serve --port 7777  # a different port
```

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Interface to bind. Loopback is deliberate and load-bearing. See [the security model](use-auth.md#the-security-model). |
| `--port` | `7421` | Port to listen on. `0` auto-picks a free one. |
| `--print-port` | off | Print the bound port to stdout once listening. Handy when `--port 0` auto-picks, and used by the local transport to discover where it landed. |

Every endpoint except the health probe and the pairing handshake requires a
paired session. [Authentication and pairing](use-auth.md) covers how a device
gets one. In normal use you do not run this by hand; the
[systemd user service](use-webapp.md#always-on-with-systemd) keeps it up.

## `grove auth`

Approve or deny pairing requests and manage active sessions. These commands act
on the host's session store, so they work from any directory and are not scoped
to a repo. The full pairing story is on the
[authentication](use-auth.md) page; here are the commands.

`grove auth pending` lists requests waiting for approval, one per line, with the
challenge id, the matching pairing code, the device label, the state, and the
expiry. When there is nothing waiting it prints `no pending pairings`.

```bash
grove auth pending
# 7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f  code=BFCD-GH23  label='Pixel 8'  state=pending  expires_at=2026-06-11T18:30:00+00:00
```

`grove auth approve <challenge-id>` approves a request. The requesting device
picks up its token on its next poll, so this command never prints a token; it
confirms with `approved <id> (label='…')`. `grove auth deny <challenge-id>`
rejects one. Both take an id from `grove auth pending`, and both reject a
malformed id with `invalid challenge id: <value>` (exit `1`).

`grove auth sessions` lists the currently active (non-revoked) sessions, one per
line, with the session id, label, and timestamps, or `no active sessions` when
there are none. `grove auth revoke <session-id>` cuts one off; that device must
pair again to return.

```bash
grove auth approve 7b3f2c1a-9d4e-4c2b-8f10-2a6b1c3d4e5f   # id from `grove auth pending`
grove auth sessions
grove auth revoke 9d21f0e4-1a2b-4c3d-8e5f-6a7b8c9d0e1f    # id from `grove auth sessions`
```

## `grove version`

Print the installed Grove version.

```bash
grove version
# grove 0.1.0
```

## `grove debug`

Print the resolved paths Grove would use for this repo, plus whether the config
loaded cleanly. JSON, for easy `jq` consumption. This is the first command to
run when something is "not loading" and you need to know which files Grove is
actually looking at.

```bash
grove debug
```

```json
{
  "user_config_path": "/home/me/.config/grove/config.json",
  "user_state_path": "/home/me/.local/state/grove/state.json",
  "user_schema_path": "/home/me/.config/grove/config.schema.json",
  "project_config_path": "/home/me/code/weather-app/.grove/config.json",
  "project_local_config_path": "/home/me/code/weather-app/.grove/config.local.json",
  "repo_root": "/home/me/code/weather-app",
  "config_loaded": true
}
```

Outside a git repository the four repo-relative fields and `repo_root` are
`null`, and `config_loaded` reflects whether the resolvable layers parsed. Note
that `debug` reports paths and a load flag, never the config values themselves;
for the merged values use `grove config show`.

## `GROVE_DEBUG=1`

Set the environment variable to flip the logger's stderr handler from `WARNING`
to `DEBUG`. Every `git` and `tmux` subprocess invocation, every cascade merge,
and every state-file read becomes visible on stderr. It is the companion to
`grove debug`: that tells you which files Grove will read, this shows you what
happens when it reads them.

```bash
GROVE_DEBUG=1 grove ls
```

Because the diagnostics go to stderr, they never pollute the JSON on stdout, so
you can keep piping to `jq` while you watch the trace.

## Using the CLI from an agent

If you are a coding agent with shell access inside a Grove-managed worktree,
`grove sessions` is your window into the rest of the project. You are one agent
in one worktree; sibling agents are working other branches of the same repo
right now, and their transcripts are on disk. The CLI lets you read them
without leaving your shell, and without any risk of disturbing them. This is
the agent-facing equivalent of asking a teammate "what are you working on?",
except the answer is already written down.

### Discover what else is happening

Start by mapping the project. `grove ls` gives you the workspaces and their
status; `grove sessions list` gives you the agent conversations across all of
them. From inside your own worktree, both resolve the whole project
automatically.

```bash
# Every workspace in this project, with its reconciled status.
grove ls

# Every agent session across every worktree, newest first.
grove sessions list

# Just the sibling sessions: drop your own, keep the others, as compact rows.
MY_BRANCH=$(git branch --show-current)
grove sessions list --json \
  | jq -r --arg me "$MY_BRANCH" '
      .[] | select(.git_branch != $me)
      | "\(.session_id[0:8])  \(.state)  \(.workspace_title // "-")  \(.title // .last_prompt // "")"'
```

### Retrieve context cheaply, and bound the token cost

Read in order of cost, and stop as soon as you have what you need. The three
subcommands are a deliberate ladder.

1. `grove sessions list` is one line per session. It is almost always enough to
   answer "who is doing what" and "what needs attention". Filter it before you
   read anything heavier.
2. `grove sessions show <id> --last N` reads only the tail of one conversation.
   Prefer it over an unbounded `show`: a handful of recent turns usually tells
   you where a sibling agent landed, at a fraction of the tokens.
3. `grove sessions dump <id>` is the raw records, and the output can be
   megabytes. Escalate to it only when you truly need the unnormalized
   transcript, for example to read exact tool results. It is rarely the right
   first move.

```bash
# Cheapest: scan the list, filtered to recent activity.
grove sessions list --since 1h

# Mid cost: read the last few turns of one sibling session.
grove sessions show a91e0d34 --last 5

# Expensive, last resort: the raw records, redirected to a file, not your context.
grove sessions dump a91e0d34 --jsonl > /tmp/a91e0d34.jsonl
```

### Scope the query

Three filters narrow the listing so you parse less. Combine them freely.

```bash
# One worktree's sessions, by branch substring or workspace id prefix.
grove sessions list -w radar-overlay

# One agent kind (skip non-introspectable agents).
grove sessions list --agent claude_code

# Only what moved recently.
grove sessions list --since 2h
```

For a single conversation, add `--last N` to `show` so you read only the tail,
and add `--json` whenever a machine, including you, is going to parse the
result.

### Tell Grove-launched sessions from hand-started ones

The `--json` output carries a `provenance` field. `grove_launched` means Grove
created the workspace and minted the session, so the session belongs to a
managed workspace and you can trust its `workspace_*` fields. `hook_discovered`
and `fs_discovered` are sessions Grove adopted that it did not start, a
transcript found by a hook or by a filesystem scan; those may have no owning
workspace, in which case the `workspace_*` fields are `null`. Group by it when
you want only the workspaces Grove is actually managing:

```bash
grove sessions list --json \
  | jq -r '.[] | select(.provenance=="grove_launched") | .workspace_title' \
  | sort -u
```

### Why this is safe

Three guarantees make these commands safe to call from an autonomous loop.

- **Read-only by construction.** `sessions` and `ls` compose read-only scans of
  transcripts and git. They cannot launch, mutate, or delete another agent's
  session. Reading a sibling's transcript can never disturb that sibling.
- **Location-independent.** Every repo-scoped command resolves the project from
  `cwd`, so they behave identically from your worktree, from a sibling's, or
  from the repo root. You never need to `cd` to the right place first.
- **Stable JSON.** The field names above are a contract, not incidental output.
  Parse `--json`; do not scrape the human tables, whose spacing and truncation
  are for eyes, not parsers.

### Rules of thumb

An agent can follow these verbatim.

- Always pass `--json` when you intend to parse the result, and pipe it through
  `jq`. The tables are for humans.
- Resolve a session by its full id, or by a unique prefix (the eight characters
  the table shows are usually unique). The id is stable; do not parse it out of
  prose.
- Treat any non-zero exit as "no data": either not found, or an ambiguous
  prefix, or you are not in a git repository. Read stderr for which one. Exit
  `2` specifically means you got a flag or argument wrong.
- Climb the cost ladder: `list`, then `show --last N`, then `dump`, and stop at
  the first rung that answers your question. `dump` output can be large, so
  redirect it to a file rather than into your own context.
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
