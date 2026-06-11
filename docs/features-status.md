# Status semantics

A workspace's status is two things at once. It is a *persisted intent*
(what Grove was last told to make true) and a *computed view* (what is
actually true right now). Reconciliation happens at one site. This page
names every status and walks the recovery decision tree.

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

## See also

- [Workspace lifecycle](features-workspace-lifecycle.md): what each lifecycle op writes.
- [The peek rail](features-peek.md): how the activity signal feeds ACTIVE and IDLE.
- [Agent activity and sessions](features-activity.md): agent state, the other dimension. A workspace can be ACTIVE while its agent is WAITING.
- [Daily workflow](use-workflow.md): recovering from a vanished session.
