# Agent activity and sessions

Run one agent and you watch it. Run six and you cannot. Five are fine,
one has been sitting on a permission prompt for twenty minutes, and the
only way to find out is to cycle through every tmux window. That does
not scale past two workspaces.

Grove watches for you. It reads what each agent is actually doing and
puts the whole fleet on one wall. The wall answers one question: who
needs me right now?

## Two views, one system

Grove tracks agents on two time scales, and gives each its own surface.

- **Live activity** is the present tense. What is each agent doing at
  this moment? The Activity Dashboard shows it, across every project.
- **Session history** is the past tense. What did the agent do, and
  what did you ask for? `grove sessions` and the web dashboard's
  sessions panel replay it.

Both are read-only by construction. Grove reads transcripts and git
state from disk. It never writes into an agent's session.

## The Activity Dashboard

Press `d` in the TUI, or open `/activity` in the
[web dashboard](use-webapp.md). Either way you get the same wall: every
workspace across every repository the daemon knows about, one tile per
workspace, grouped by project.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-activity-wall.png" alt="The Activity Dashboard in the web dashboard: agent tiles across two projects, with one live terminal pane focused" /></div>
  <figcaption class="ms-shot__body">The wall in the browser. Two projects, six workspaces, one live pane. Working agents glow, waiting agents carry the amber accent, and the idle one dims.</figcaption>
</figure>

Quiet tiles stay compact. A tile whose agent is working, waiting,
blocked, or erroring grows and shows a live tail of the agent's
terminal. So the wall is not a static grid. It breathes with the fleet,
and the tiles that matter take the space.

Each tile carries the branch, the agent and model, the diff size,
ahead and behind counts, turn and token totals, and the agent's own
one-line summary of what it is doing.

## What Grove watches

Four signals feed every tile.

1. **Agent state.** For agents Grove knows how to introspect (Claude
   Code today), Grove reads the session transcript and derives a
   precise state: `◌ starting`, `▶ working`, `◑ waiting`, `⚠ blocked`,
   `○ idle`, `✗ error`. Waiting means the turn ended and the agent
   wants you. Blocked means it is stuck on an explicit prompt. Before
   any transcript exists, the state reads as a quiet `· unknown`.
2. **Terminal output.** Agents Grove cannot introspect still get a
   signal. If the tmux window is producing output, the workspace reads
   as active. Quiet past the threshold reads as idle.
3. **Dirty files.** The count of uncommitted paths in the worktree.
   This is the earliest sign of progress, visible before any commit
   lands.
4. **Recent commits.** The last few commits on the workspace branch.
   The durable record of what actually got done, and when.

The same agent state shows up outside the dashboard too. The workspace
list adds the glyph and label to each card, and the peek rail adds a
metrics line with the model, turn counts, and token usage. See the
[TUI tour](use-tui.md) for where each lands.

Note the distinction from [workspace status](features-status.md). A
workspace can be ACTIVE (the terminal is producing output) while its
agent is WAITING (the turn ended and it wants you). Status describes
the workspace; agent state describes the conversation inside it. The
dashboard shows both.

## Attention never dims

Tiles render in three tiers, and the tiers drive both brightness and
order.

- **Active** tiles are full brightness with a pulsing glyph. Something
  is happening.
- **Attention** tiles (waiting, blocked, error) are full brightness
  with an accent color. These are the ones that need you, so they are
  never dimmed and they sort to the front.
- **Dormant** tiles (idle, offline, starting) dim. They are alive but
  have nothing to say.

The result reads like a heat map. Bright means look here. Dim means
all quiet.

## Which agents Grove can read

Every agent entry in your config declares a `kind`. The kind tells
Grove which adapter, if any, can introspect that agent's sessions.

- `claude_code` enables the full treatment: transcript-derived state,
  turn and token counts, the session's own title, and session history.
  The built-in `claude` agent ships with this kind.
- `generic` is the default for everything else. The command runs
  normally and the dashboard falls back to the terminal-output signal.

Declaring a kind is one line in the agent spec. See
[Agents](configure-agents.md#telling-grove-what-kind-of-agent-it-is)
for the field and the cascade rules around it.

For exact state, there is one more opt-in. Set `hooks.enabled: true`
and Grove launches Claude Code agents with a lightweight status hook
that pushes the precise lifecycle state as it changes. Polling can tell
you the agent went quiet. The hook can tell you it is blocked on a
permission prompt. Your own `.claude/settings.json` is never touched,
and turning it off is one flag.

## Session history

Live state tells you what is happening. Sessions tell you what
happened. Grove discovers every recorded agent session for a project,
across the repo root and every worktree, whether Grove started it or
you did.

From the terminal, `grove sessions` is the front door. Think of it as
`git log` for agent conversations:

```bash
grove sessions list                # every session, newest first
grove sessions show 7b3f2c1a       # one conversation, as turns
grove sessions dump 7b3f2c1a       # the raw transcript records
```

The full command reference, with filters for agent, workspace, and
time window, lives on the [CLI page](use-cli.md#grove-sessions).

In the web dashboard, each workspace detail page carries a sessions
panel. It lists the workspace's sessions newest first, and expanding a
row loads the conversation inline.

Grove also labels where each session came from. Sessions Grove launched
are tagged as Grove's, because Grove handed the agent its session id at
create time. Sessions you started by hand in the same directory show up
too, labeled as hand-started. Nothing is hidden just because Grove did
not start it.

## See also

- [TUI tour](use-tui.md): the dashboard screen, its keys, and where
  agent state appears on the list.
- [Web dashboard](use-webapp.md): the `/activity` wall and the
  sessions panel in the browser.
- [CLI](use-cli.md#grove-sessions): `grove sessions list`, `show`, and `dump`.
- [Agents](configure-agents.md): declaring an agent's `kind`.
- [Status semantics](features-status.md): workspace status, the other
  dimension on every tile.
- [The peek rail](features-peek.md): the per-workspace view, one
  selected agent in depth.
