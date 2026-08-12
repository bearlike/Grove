# Agent activity and sessions

## Watch the whole fleet

Run six agents and you cannot watch them. Five are fine, one has sat on a
permission prompt for twenty minutes, and finding out means cycling every tmux
window. Grove reads what each agent is doing and puts the fleet on one wall that
answers one question: who needs me right now?

It tracks two time scales. **Live activity** is what every agent is doing now,
across every project. **Session history** is what happened. Both are read-only,
built from transcripts and git state on disk, so steering from the dashboard
sends a fresh follow-up rather than rewriting the record.

## The Activity Dashboard

Press ++d++ in the TUI, or open the [web dashboard](use-webapp.md)
home: the same wall, every agent session across every repository the
daemon knows about, attention-first.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-home.png" alt="Grove's fleet dashboard: a flat, attention-sorted grid of workspace cards behind a session rail listing every workspace by recency" /></div>
  <figcaption class="ms-shot__body">The wall in the browser, attention-first.</figcaption>
</figure>

In the browser the fleet grid is the home surface (`/`), one flat wall rather
than sectioned by repo. It streams over SSE within a second with a poll
fallback, and a filter narrows the recency-sorted rail and grid by project,
state or attention. Each card carries the agent and state, workspace title and
project, a one-line summary, a diff and commit-divergence line, todo progress,
and linked tickets. Open a card for the agent's live terminal, on the
workspace's own Terminal tab.

In the TUI, tiles group by project. Quiet ones stay compact, and a working,
waiting, blocked or erroring one expands to a terminal tail.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-dashboard.svg" alt="The TUI Activity Dashboard: agent tiles grouped by project, working and waiting tiles promoted with token metrics and a live pane tail" /></div>
  <figcaption class="ms-shot__body">The same wall in the terminal.</figcaption>
</figure>

## What Grove watches

Four signals feed every tile.

1. **Agent state.** For agents Grove can introspect (Claude Code and
   Codex from transcripts, Mewbo over its API): `◌ starting`,
   `▶ working`, `◑ waiting`, `⚠ blocked`, `○ idle`, `✗ error`. Waiting
   means the turn ended and the agent wants you, blocked means an
   explicit prompt, and no session yet reads as `· unknown`.
2. **Terminal output.** Agents Grove cannot introspect still get a
   signal: a producing tmux window reads as active, quiet as idle.
3. **Dirty files.** Uncommitted worktree paths, visible before any
   commit.
4. **Recent commits.** The last few commits on the branch.

The same state shows on the workspace list and the [peek
rail](features-peek.md). It is not
[workspace status](features-status.md), and a workspace can be ACTIVE while its
agent is WAITING. Status describes the workspace, agent state describes the
conversation inside it, and the dashboard shows both.

## Attention never dims

Tiles render in three tiers that drive both brightness and order. **Active** is
full brightness with a pulsing glyph. **Attention** (waiting, blocked, error) is
full brightness in accent color, and sorts to the front because it needs you.
**Dormant** (idle, offline, starting) dims: alive, nothing to say.

Bright means look here. Dim means all quiet.

## Which agents Grove can read

Every agent entry declares a `kind`, the adapter that introspects it.

- `claude_code`: transcript-derived state, turn and token counts,
  title, and history.
- `codex`: the Codex CLI's rollout transcripts, the same signals.
- `mewbo`: introspects a remote session over its REST API.
- `generic`: default, falls back to the terminal-output signal.

The built-in `claude` and `codex` agents ship with their matching kind, and
declaring one is a single line in the agent spec. See
[Agents](configure-agents.md#telling-grove-what-kind-of-agent-it-is).

Set `hooks.enabled: true` for exact state, where a status hook pushes Claude
Code's lifecycle as it changes instead of polling. `.claude/settings.json` stays
untouched and turning it off is one flag.

## The agent's own todo list

Claude Code's `TodoWrite`, Codex's `update_plan`, and Mewbo's task board each
give an agent a running checklist. Grove parses it from the transcript and
surfaces it everywhere: `grove show` prints it, TUI and web cards show a bounded
count (`4/7 done`), the [issue-ops](issue-ops.md) sticky comment renders it, and
`grove_get_workspace_todo` hands an MCP client the raw items. It is read-only.
The [task phase](features-status.md#the-third-axis-task-phase) is its
counterpart: a todo list is inferred, a phase is stated.

## Session history

Grove discovers every recorded session for a project, across the repo root and
every worktree. `grove sessions` is the front door, the `git log` of agent
conversations:

```bash
grove sessions list                # every session, newest first
grove sessions show 7b3f2c1a       # one conversation, as turns
grove sessions dump 7b3f2c1a       # the raw transcript records
```

Filters for agent, workspace, and time window live on the [CLI
page](use-cli.md#grove-sessions). The web dashboard's detail page
replays the conversation, the browser twin of `grove sessions show`.

Grove labels each session's origin too: sessions it launched are
tagged as Grove's, and hand-started ones show up labeled as such.

### Adoption and recovery

A workspace's session pin must survive a daemon restart or another
user on the box, so Grove never trusts a bare session id.
Adoption is pane-verified: the candidate's transcript birth and the
workspace's tmux pane must agree before Grove treats it as live (see
[`sessions.py`](repo:src/grove/core/sessions.py) and
[`manager.py`](repo:src/grove/core/manager.py)). Resume runs the same check. If the Grove-minted session dies and a
successor gets gate-rejected, the pointer goes stale and the
transcript reads blank.
Fix it with `grove sessions remap WORKSPACE SESSION`, the TUI's ++x++
key, or the web remap picker.

### The Session Catalog: every session on this host

Most of your agent history lives outside this project. Every repository you have
pointed Claude Code or Codex at holds its own transcripts, and
`grove sessions list --host` scans every session store on the machine:

```bash
grove sessions list --host              # every repo on this host
```

The table gains `PROJECT` and `BRANCH` columns plus a live marker on rows with a
matching agent process. The [web dashboard's Sessions
screen](use-webapp.md#the-session-catalog) and the TUI's `h` key show the same
catalog.

The scan reads each transcript's first few lines rather than the whole file, so
it stays fast across hundreds of sessions. A transcript missing a working
directory, or pointing at a non-git directory, still shows up rather than being
dropped. The catalog is read-only, and no verb adopts a session found this way
into a workspace.

## See also

- [Telemetry and tracing](features-telemetry.md): the same sessions as
  queryable OpenTelemetry traces, months after the fact.
- [TUI tour](use-tui.md): the dashboard screen and its keys.
- [Web dashboard](use-webapp.md): the live grid and the transcript.
- [CLI](use-cli.md#grove-sessions): `grove sessions list`, `show`, `dump`.
- [Agents](configure-agents.md): declaring an agent's `kind`.
- [Task phase](features-status.md#the-third-axis-task-phase): the
  agent's self-reported progress.
- [Status semantics](features-status.md): workspace status.
- [The peek rail](features-peek.md): one agent, in depth.
