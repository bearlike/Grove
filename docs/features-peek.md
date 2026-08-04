# The peek rail

## Read a workspace at a glance

The right-hand rail shows git position, agent metrics and a live preview for
the selected workspace without attaching. It is the per-workspace companion of
the [Activity Dashboard](features-activity.md). Every helper is best-effort: a
failure returns zero, not a blocked render loop.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-list.svg" alt="Grove TUI with the peek rail showing the summary card and the transcript tab" /></div>
  <figcaption class="ms-shot__body">The <em>summary</em> card carries branch, git stats, agent metrics and recent commits. The <em>preview</em> below it has transcript and terminal tabs.</figcaption>
</figure>

## What the rail shows

A summary card sits over a tabbed preview pane, one row per workspace property.

| Card row | What it carries |
|---|---|
| Git | Branch, ahead, behind, dirty count and age, coloured by polarity: green for work to push, amber for work to pull or clean, muted at zero |
| Agent | Model, turn and reply and tool-call counts, token usage and agent state, when Grove can read the session |
| Task phase | The [task phase](features-status.md#the-third-axis-task-phase) the agent reported, with its bounded todo progress |
| Commits | The three most recent commits |

**Transcript** groups every consecutive run of tool calls into one row and
never expands a row, since it stays a glance surface. Press ++s++ for
[the sessions browser](use-tui.md#the-sessions-browser).

**Terminal** mirrors the tmux pane on the agent window: output, ANSI colours
and cursor position, four times a second while RUNNING. The border turns clay
when the pane is live.

Transcript is the default once the agent has recorded turns, terminal
otherwise, and your choice sticks per workspace. Transcripts outlive their
worktrees, so a paused workspace with a recorded session still shows a
preview.

## Activity signal

Grove reads `tmux #{window_activity}` as the activity timestamp, updated
whenever any pane in a window emits output. Workspace age is
`now - window_activity`, flipping between ACTIVE and IDLE on
`tmux.activity_threshold_seconds`, default `30`.

`#{pane_activity}` stays unused on purpose. It arrived in tmux 3.4 and returns
an empty string on older versions, including the 3.2a Ubuntu 22.04 ships, and
empty coerces to `None`, which reads every workspace as IDLE. Grove runs one
pane per window, so both levels resolve the same way anyway.

## Pane-target resolution

[`WorkspaceManager.pane_target`](repo:src/grove/core/manager.py)
(`workspace_id`) reconciles the workspace fresh from the store, then picks the
pane the terminal tab captures.

| Order | Resolves to |
|---|---|
| 1 | `None` unless the workspace is live |
| 2 | For a [container workspace](features-containers.md), the agent's own session on the tmux inside the container |
| 3 | The configured `agent_window_name` (default `agent`) if that window exists |
| 4 | The first non-`shell` window otherwise |
| 5 | The `shell` window as last resort |
| 6 | `None` if the session reports no windows |

The fallbacks cover a workspace reorganized outside Grove, or an agent window
that closed with the agent process, both leaving an arbitrary layout.
Hard-coding `f"{session}:{agent}"` was the original peek-empty bug. A caller
holding a state already reconciled this tick uses `pane_target_for` instead,
since re-reconciling costs three redundant tmux forks per poll.

Grove captures a container pane inside the container, so the rail keeps
updating with nothing attached on the host. That target renders as
`container:<id>:<session>`, never a name your own tmux could attach to.

## Best-effort by contract

`peek()` never raises. It calls `git`, `tmux`, the state store and the
activity-age helper, and any of them can fail. Each helper catches its own
failure, logs once at debug, and returns the zero value for its slot: empty
string, zero count, or `None`. Render-path reads are bounded too: a container
tmux read gives up after 5 seconds, so a wedged docker costs one slow frame,
not a stall across every surface that lists workspaces.

The lifecycle methods (`create`, `pause`, `kill`) keep a loud failure surface
instead, since they are transactional and a partial failure must reach the
operator. Peek is observation. Lifecycle is action.

## Two refresh cadences

| Tick | Config, default | What it does |
|---|---|---|
| Fast pane tick | `peek_pane_refresh_seconds`, `0.25` | Calls `peek_pane()` only: one `tmux capture-pane` per running workspace, no git work and no store reads, spliced into the cached peek |
| Slow stats tick | `peek_stats_refresh_seconds`, `3.0` | Re-enumerates the workspace set and calls `peek()` end to end, since ahead, behind, dirty count, diff stats and activity age never move at sub-second granularity |

Every tick asks `_ticks_live()` first and skips while a modal owns the
foreground or the terminal is handed to an attach, since publishing into an
empty room is waste. Window focus is not part of that gate: an unfocused
Grove on a second monitor is still a live surface.

## See also

| Page | Covers |
|---|---|
| [Agent activity and sessions](features-activity.md) | The fleet-wide view. The rail watches one workspace |
| [Status semantics](features-status.md) | Activity age feeding ACTIVE and IDLE, plus the task-phase axis |
| [Workspace lifecycle](features-workspace-lifecycle.md) | RUNNING, PAUSED and ERROR |
