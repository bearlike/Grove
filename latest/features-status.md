# Status semantics

Grove tracks three separate signals, and it helps to keep them apart from
the start. The **workspace status** is about the container: is the
worktree on disk, is the tmux session up, has the pane produced output
lately. The **agent activity state** is about the coding agent running
inside that container: is it in the tool loop, has it handed the turn
back to you, is it blocked on a prompt. The **task phase** is about the
job itself: how far through the work the agent says it is, scoping
through done. A workspace can be ACTIVE while its agent is WAITING while
it reports `verifying` — all three at once, and an orchestrator watching
twenty workspaces needs all three to know what to look at first. This
page covers the workspace status in full, then names the agent activity
states, then the task phase, and points to where each lives.

A workspace's status is two things at once. It is a *persisted intent*
(what Grove was last told to make true) and a *computed view* (what is
actually true right now). Reconciliation happens at one site. The
sections below name every status and walk the recovery decision tree.

## The spectrum

The colors and glyphs below match the running TUI. The landing page and
this gallery share one source of truth in
[`src/grove/tui/theme.py`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/theme.py).

<div class="grove-status-grid" markdown>

<div class="grove-status-chip" data-status="active">
  <span class="grove-status-chip__glyph">●</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Active</p>
    <p class="grove-status-chip__body">Computed. Session up. The agent pane produced output within the activity threshold.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="idle">
  <span class="grove-status-chip__glyph">◐</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Idle</p>
    <p class="grove-status-chip__body">Computed. Session up. The pane has been quiet past the threshold.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="paused">
  <span class="grove-status-chip__glyph">‖</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Paused</p>
    <p class="grove-status-chip__body">Persisted. Worktree removed by the user; branch retained. <code>R</code> resumes.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="offline">
  <span class="grove-status-chip__glyph">○</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Offline</p>
    <p class="grove-status-chip__body">Computed. Session vanished externally. <code>o</code> respawns from the persisted worktree.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="orphaned">
  <span class="grove-status-chip__glyph">⊘</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Orphaned</p>
    <p class="grove-status-chip__body">Computed. Worktree directory missing on disk; respawn no longer applies. <code>k</code> only.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="error">
  <span class="grove-status-chip__glyph">✗</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Error</p>
    <p class="grove-status-chip__body">Persisted. Init script failed with <code>fail_fast: false</code>. Review the log; <code>k</code> only.</p>
  </div>
</div>

</div>

## Two domains, one enum

`WorkspaceStatus` is a single enum that carries both kinds of values:

- **Persisted**: written to the state file, survives restarts. `RUNNING`,
  `PAUSED`, `ERROR`. These are the values lifecycle methods write.
  `create()` and `resume()` write `RUNNING`. `pause()` writes `PAUSED`.
  Init failure with `fail_fast: false` writes `ERROR`.
- **Computed**: never persisted. Derived at read time from the persisted
  intent plus current world state. `ACTIVE`, `IDLE`, `OFFLINE`,
  `ORPHANED`. `JsonWorkspaceStore.save` rejects writes whose status is
  not in `PERSISTED_STATUSES` as a defense-in-depth check.

The combined enum is one source of truth for the values themselves
(name, glyph, hex). The split matters for the write path. Only persisted
statuses are ever saved.

## What each status means

| Status | Domain | Meaning |
|---|---|---|
| **`RUNNING`** | persisted | Last lifecycle write said "this should be running". |
| **`ACTIVE`**  | computed  | Persisted RUNNING with recent activity (within `activity_threshold_seconds`). |
| **`IDLE`**    | computed  | Persisted RUNNING with no recent activity. |
| **`OFFLINE`** | computed  | Persisted RUNNING with the tmux session gone. Recoverable via respawn. |
| **`ORPHANED`**| computed  | Persisted RUNNING with the worktree directory gone. Not recoverable. Kill only. |
| **`PAUSED`**  | persisted | Worktree removed; branch kept. Resume re-creates the worktree. |
| **`ERROR`**   | persisted | Init script failed with `fail_fast: false`. Review the log first, then kill. |

## The single reconcile site

`WorkspaceManager._reconcile_status` is the only site that promotes a
persisted intent into a computed view. Every consumer (`list()`,
`peek()`, `peek_pane()`, `attach()`, `respawn()`) calls it. Nothing
branches on `RUNNING` outside this method. Adding a new computed status
means extending this one method plus the glyph and hex tables, not
chasing call sites.

The reconciliation logic in plain English:

1. If the persisted status is `PAUSED` or `ERROR`, return it as is.
2. If the worktree directory is missing, return `ORPHANED`.
3. If the tmux session is missing, return `OFFLINE`.
4. If the activity age is below the threshold, return `ACTIVE`.
5. Otherwise return `IDLE`.

The order of the checks matters. A session that is gone always reports
`OFFLINE` even if the worktree is also gone, because the worktree check
runs first and ORPHANED is the more severe condition.

## Recovery decision tree

```
Status?
├── ACTIVE / IDLE → it's running. Attach (Enter), pause (p), or kill (k).
├── PAUSED        → resume (R) recreates the worktree. Or kill (k) to remove.
├── OFFLINE       → respawn (o) rebuilds the tmux session from the existing worktree.
├── ORPHANED      → kill (k) is the only path; the worktree dir is gone.
└── ERROR         → review the init log, then kill (k). Init won't re-run from this state.
```

The contextual footer enforces this mapping. Keys that do not apply to
the current row's status render dimmed. The availability rule is data,
not branches: one function in `screens/list.py`, so the footer cannot
drift from what is runnable.

## Legacy values

Older state files (pre-status-split) sometimes carry a `stale` value that
no longer exists in the enum. The store's decoder coerces those back to
the persisted intent that produced them, usually `RUNNING`. Loading an
old state file works without a migration step. New writes never produce
these values; legacy reads do.

## The other axis: agent activity

The workspace status above answers "is this container running". It says
nothing about what the agent inside is doing. That is a separate axis,
read agent-agnostically from the agent's own on-disk transcript rather
than from tmux output. It lives in `grove.core.agents` as
`AgentActivityState`, and every client renders it from one shared palette
in `grove.core.contracts.agent_palette`.

| Agent state | Meaning |
|---|---|
| **`STARTING`** | The session id is known, but the transcript file is not on disk yet. |
| **`WORKING`** | The agent is in the tool loop or mid-response. |
| **`WAITING`** | The turn ended. The agent may need you. |
| **`BLOCKED`** | An explicit permission or input prompt is open. |
| **`IDLE`** | The session is alive but has been quiet. |
| **`ERROR`** | The transcript shows a parse or process error, or a failed run. |
| **`UNKNOWN`** | The transcript is unreadable or suppressed (for example a generic agent with no adapter). |

The two axes are orthogonal. A workspace status of ACTIVE means the pane
produced output recently; an agent state of WAITING means the agent has
handed the turn back. Those happen together all the time: the agent
prints its final message (output, so ACTIVE) and then waits for you (turn
ended, so WAITING). The [Activity Dashboard](features-activity.md) shows
both at once, and the adapters that derive each agent's state per kind
are described in [Agents](configure-agents.md).

## The third axis: task phase

Status says the container is up. Activity state says the agent is in its
tool loop. Neither says how far through the *job* it is. A workspace can
sit ACTIVE and WORKING for the entire lifetime of a task, whether that
task is a one-line typo fix or a week-long migration. Task phase is the
agent's own report of where it stands, so an orchestrator watching a
fleet can tell "still reading the ticket" from "running the tests" from
"opening the PR" without reading a single line of transcript.

The six phases are ordered, and they converge, but they are not a
progress bar that only moves forward:

| Phase | Meaning |
|---|---|
| `scoping` | Reading the ticket and the code, working out what the job is. |
| `planning` | Understands the problem, choosing an approach. |
| `implementing` | Editing files. |
| `verifying` | Running tests, linters, the build; reviewing its own diff. |
| `delivering` | Committing, pushing, opening or updating the PR. |
| `done` | Handed off. |

Moving backwards is a correct report, not an error. If `verifying` shows
the design was wrong, the honest next report is `planning` again. And no
phase reported at all is its own distinct, meaningful state: it is not
"step zero" of `scoping`, it just means the agent has not said anything
yet.

### How an agent reports

An agent reports its phase by writing the file Grove names for it in the
launch environment, as `GROVE_PHASE_FILE`:

```json
{"phase": "implementing", "note": "wiring the parser"}
```

Grove composes that path rather than letting the agent derive one, because
a worktree does not always host a single agent: several workspaces can run
in one repo root, and several agents can run in one container. Each gets a
distinct file under `.grove/phase/` in the worktree, so no report ever
overwrites another. An agent that finds no such variable falls back to
`.grove/phase.json` at the top of the worktree, which Grove still reads.

`note` is optional: one line, 200 characters or less, free text for
whatever the agent wants to add ("running make lint", "waiting on a
flaky test"). The agent never writes a timestamp; Grove reads the file's
own mtime instead. The file is excluded from git automatically, so it
never shows up as a dirty change to commit. A malformed or unreadable
file is ignored silently rather than surfaced as an error.

Writing a file, rather than calling an API, is deliberate. A containerized
agent can reach neither Grove's daemon (loopback-bound, no route in from
inside the container) nor the `grove` CLI (not installed in the
container). The worktree is bind-mounted into the container, so a file
written inside it is visible to Grove on the host immediately. A file is
the one channel that works for a host workspace and a container
workspace, for Claude Code and Codex and any future harness, and it
means the agent never has to know its own workspace id: it just writes
where it already is.

### Convenience surfaces

The file is the only channel guaranteed to work everywhere. On a host
workspace, three more surfaces read and write the same state:

- **CLI**: `grove phase <phase> --note "..."` sets it, inferring the
  workspace from your current directory the way `grove show` does; the
  read form prints the current phase the same way. See
  [CLI: `grove phase`](use-cli.md#grove-phase).
- **MCP**: `grove_set_workspace_phase` and `grove_get_workspace_phase`.
  See [MCP tools](use-mcp.md#tools).
- **Daemon**: `POST /workspaces/{id}/phase` and
  `GET /workspaces/{id}/phase`, the same endpoints the web dashboard and
  MCP server call underneath.

None of these are required. They exist for the case where an agent (or
you, by hand) can reach the daemon or has the CLI on its `PATH`. The file
is the mechanism; these are conveniences on top of it.

### Where it shows up

The phase renders next to the other two axes everywhere Grove already
shows workspace state: the TUI workspace card, the web dashboard's
Overview card, and, for a ticket-linked workspace, the live sticky
comment Grove maintains on the issue (see [issue-ops](issue-ops.md)). The
sticky comment renders it as dot progress against the six phases, filled
dots for phases reached, the current one named, plus the latest note:

```
●●●○○○ Verifying (4/6) — running make lint
```

## See also

- [Workspace lifecycle](features-workspace-lifecycle.md): what each lifecycle op writes.
- [The peek rail](features-peek.md): how the activity signal feeds ACTIVE and IDLE.
- [Agent activity and sessions](features-activity.md): agent state, the other dimension. A workspace can be ACTIVE while its agent is WAITING.
- [Issue ops](issue-ops.md): the sticky ticket comment that renders task phase as dot progress.
- [Daily workflow](use-workflow.md): recovering from a vanished session.
