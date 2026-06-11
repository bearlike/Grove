# The peek rail

Grove's right-hand rail mirrors the agent's tmux pane in near-real time
and surfaces git position alongside it. It is the per-workspace
companion of the fleet-wide
[Activity Dashboard](features-activity.md). The whole rail is
best-effort by contract. Helpers that fail return zeros instead of
blocking the render loop.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-peek-preview.png" alt="Peek rail with the live workspace preview pane mirroring the agent's tmux output" /></div>
  <figcaption class="ms-shot__body">The right-hand rail: <em>summary</em> with branch, git stats, and recent commits; the <em>live workspace preview</em> mirroring the agent's tmux pane.</figcaption>
</figure>

## What the rail shows

The peek rail is two cards stacked vertically.

The **summary card** carries one row per workspace property: branch,
ahead, behind, dirty count, age. Counts are colour-coded by polarity.
Green when there is work to push, amber when there is work to pull or
clean, muted when zero. When Grove can read the agent's session, an
agent metrics line joins the card: the model, turn and reply and
tool-call counts, token usage, and the agent's state. Git facts and
session facts read as one status block.

The **agent card** mirrors the tmux pane attached to the workspace's
agent window. Output, ANSI colours, cursor position, refreshed at four
ticks per second when the workspace is RUNNING. The border colour
changes to clay when the pane is live.

## Activity signal

Grove uses `tmux #{window_activity}` as the activity timestamp. This
format variable updates whenever any pane in a window emits output.
Grove computes the workspace's age as `now - window_activity` and flips
between ACTIVE and IDLE on the configured threshold
(`tmux.activity_threshold_seconds`, default `5`).

Grove deliberately does not use `#{pane_activity}`. That format variable
was added in tmux ≥ 3.4 and returns an empty string on older versions
including the 3.2a that Ubuntu 22.04 ships. An empty value coerces to
`None` and reconciliation treats every workspace as IDLE. Grove's layout
is one pane per window (shell, agent), so window-level and pane-level
resolve to the same answer.

## Pane-target resolution

The agent card needs to know which tmux pane to capture. The policy
lives in `WorkspaceManager.pane_target(workspace_id)`:

1. The configured `agent_window_name` (default `agent`) if it exists.
2. The first non-`shell` window otherwise.
3. The `shell` window as last resort.
4. `None` if the session has no windows at all.

The fallbacks exist because workspaces created or reorganized outside
Grove (or whose agent window auto-closed when the agent process exited)
have arbitrary window layouts. The rail must show something live
whenever the session has any output. Hard-coding `f"{session}:{agent}"`
was the original peek-empty bug.

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
  cached full peek so the agent card looks live.
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
- [Status semantics](features-status.md): how the activity age feeds ACTIVE and IDLE.
- [Workspace lifecycle](features-workspace-lifecycle.md): what RUNNING, PAUSED, and ERROR mean.
