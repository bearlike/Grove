# The peek rail

Grove's right-hand rail surfaces git position, agent metrics, and a live
preview for the selected workspace, all without attaching. It is the
per-workspace companion of the fleet-wide
[Activity Dashboard](features-activity.md). The whole rail is
best-effort by contract. Helpers that fail return zeros instead of
blocking the render loop.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-list.svg" alt="Grove TUI with the peek rail showing the summary card and the transcript tab" /></div>
  <figcaption class="ms-shot__body">The right-hand rail: <em>summary</em> with branch, git stats, agent metrics, and recent commits; below it a <em>preview</em> with transcript and terminal tabs.</figcaption>
</figure>

## What the rail shows

The peek rail stacks a summary card over a tabbed preview pane.

The **summary card** carries one row per workspace property: branch,
ahead, behind, dirty count, age. Counts are colour-coded by polarity.
Green when there is work to push, amber when there is work to pull or
clean, muted when zero. When Grove can read the agent's session, an
agent metrics line joins the card: the model, turn and reply and
tool-call counts, token usage, and the agent's state. If the agent has
reported a [task phase](features-status.md#the-third-axis-task-phase),
that shows too, alongside its bounded todo progress. The card closes
with recent commits. Git facts and session facts read as one status
block.

The **preview** pane below it has two tabs:

- The **transcript** tab shows the agent's recent turns. Each consecutive
  run of tool calls collapses into one grouped row, so the conversation
  reads as a clean exchange rather than a wall of tool noise. It is a
  glance surface, not the full sessions browser, so it never expands the
  grouped rows. For the full history, press `s`. See
  [the sessions browser](use-tui.md#the-sessions-browser).
- The **terminal** tab mirrors the tmux pane attached to the workspace's
  agent window. Output, ANSI colours, cursor position, refreshed at four
  ticks per second when the workspace is RUNNING. The border colour
  changes to clay when the pane is live.

The preview defaults to transcript when the agent has recorded turns, and
to terminal otherwise. Pick a tab yourself and Grove keeps your choice
for that workspace. Because transcripts outlive their worktrees, the
preview stays visible for a paused workspace that still has a recorded
session.

## Activity signal

Grove uses `tmux #{window_activity}` as the activity timestamp. This
format variable updates whenever any pane in a window emits output.
Grove computes the workspace's age as `now - window_activity` and flips
between ACTIVE and IDLE on the configured threshold
(`tmux.activity_threshold_seconds`, default `30`).

Grove deliberately does not use `#{pane_activity}`. That format variable
was added in tmux ≥ 3.4 and returns an empty string on older versions
including the 3.2a that Ubuntu 22.04 ships. An empty value coerces to
`None` and reconciliation treats every workspace as IDLE. Grove's layout
is one pane per window (shell, agent), so window-level and pane-level
resolve to the same answer.

## Pane-target resolution

The terminal tab needs to know which tmux pane to capture. The policy
lives in [`WorkspaceManager.pane_target`](repo:src/grove/core/manager.py) (`workspace_id`):

1. The configured `agent_window_name` (default `agent`) if it exists.
2. The first non-`shell` window otherwise.
3. The `shell` window as last resort.
4. `None` if the session has no windows at all.

The fallbacks exist because workspaces created or reorganized outside
Grove (or whose agent window auto-closed when the agent process exited)
have arbitrary window layouts. The rail must show something live
whenever the session has any output. Hard-coding `f"{session}:{agent}"`
was the original peek-empty bug.

That whole list is the host answer. A [container
workspace](features-containers.md) resolves elsewhere, because its agent's
pane lives on the tmux inside the container. Grove captures there, so the
rail keeps updating with nothing attached on the host, and the target
renders as `container:<id>:<session>` to say where the pane is rather than
pass it off as one your own tmux can attach to.

## Best-effort by contract

`peek()` never raises. It calls into `git`, `tmux`, the state store, and
the activity-age helper. Any of them can fail (binary missing, branch
deleted, session gone). Each helper catches its own failure, logs once
at debug, and returns the zero value for its slot (empty string, zero
count, `None`). The rail keeps painting and the user sees the slot as
unknown rather than the whole UI stuttering.

The lifecycle methods (`create`, `pause`, `kill`) keep a loud failure
surface. Those are transactional, and a partial failure must surface to
the operator. Peek is observation; lifecycle is action. The two have
opposite contracts on purpose.

## Two refresh cadences

Two timers keep the rail current at the right cost:

- **Fast pane tick** (`peek_pane_refresh_seconds`, default `0.25`) calls
  `peek_pane()` only. One `tmux capture-pane` per running workspace. No
  git work, no state-store reads. Splices a fresh snapshot into the
  cached full peek so the terminal tab looks live.
- **Slow stats tick** (`peek_stats_refresh_seconds`, default `3.0`)
  calls `peek()` end to end. git ahead and behind, dirty count, diff
  stats, activity age. These do not change at sub-second granularity.
  Rerunning them on every fast tick would burn IO without any
  user-visible benefit.

Both timers freeze when a modal is open
(`if self.app.screen is not self: return`), so a confirm dialog never
has the pane tick painting behind it.

## See also

- [Agent activity and sessions](features-activity.md): the fleet-wide view. The peek
  rail watches one workspace; the Activity Dashboard watches them all.
- [Status semantics](features-status.md): how the activity age feeds ACTIVE and IDLE, and the task-phase axis.
- [Workspace lifecycle](features-workspace-lifecycle.md): what RUNNING, PAUSED, and ERROR mean.
