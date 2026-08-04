# TUI tour

## Every region and key

The TUI is the primary way to drive Grove. This page names every region and
key.

<video preload="auto" poster="../img/posters/terminal-still.png">
  <source src="../videos/1-grove-terminal.mp4" type="video/mp4" />
</video>

## Screen anatomy

The list screen has three vertical zones, a status bar, and a contextual
footer.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-list.svg" alt="Grove TUI showing four workspaces in mixed states with a live peek rail" /></div>
  <figcaption class="ms-shot__body">Header, filter bar (slash-toggled), workspace list left, peek rail right, status bar and footer at bottom.</figcaption>
</figure>

- **Header.** Repo name, count chip.
- **Filter bar.** Hidden until ++slash++, narrows by title, branch, or agent.
  ++esc++ clears.
- **Workspace list.** Left zone, one card per row: status glyph, title,
  agent, branch, a stat strip (ahead, behind, dirty) in polarity-aware
  colours, and, when readable, the agent's state (`▶ working`,
  `◑ waiting`) and reported [task phase](features-status.md#the-third-axis-task-phase).
- **Peek rail.** Right zone: a summary card (branch, stats, age, commits,
  and when live, model, turn/reply/tool-call counts, tokens, agent state)
  above a two-tab preview, transcript (grouped turns) and terminal (live
  tmux, four ticks a second). Detail on [the peek rail](features-peek.md).
- **Status bar.** Fleet left, workspace right. Colour tracks state: clay
  default, amber on attention, neutral when empty.
- **Contextual footer.** Global keys left, selection keys right.

## Keybindings

Global keys apply at any time. Selection keys act on the highlighted
workspace and dim when they do not fit its status.

Global keys:

| Key | Action |
|-----|--------|
| ++n++ | Create a workspace. |
| ++d++ | Open the [Activity Dashboard](features-activity.md), the cross-project wall. |
| ++shift+p++ | Switch project, jump to another repository the daemon knows. |
| ++r++ | Refresh the list and peek rail. |
| ++slash++ | Filter. Type to narrow, ++esc++ clears. |
| ++question++ | Help modal, an on-screen reference for every key. |
| ++q++ | Quit. |

Selection keys:

| Key | Action |
|-----|--------|
| ++enter++ / ++a++ | Attach to the selected workspace. |
| ++m++ | Send a message, steer the running agent without attaching. |
| ++e++ | Edit the workspace's title and description. |
| ++s++ | Browse the workspace's recorded agent sessions. |
| ++x++ | Remap session. Re-point at a live or recovered session, e.g. after `/clear` rotates the id. See [`screens/remap_session.py`](repo:src/grove/tui/screens/remap_session.py). |
| ++p++ | Pause, removes the worktree, keeps the branch. |
| ++shift+r++ | Resume, recreates the worktree from the branch and restarts tmux. |
| ++o++ | Respawn an OFFLINE workspace whose tmux session vanished. |
| ++k++ | Kill, removes the worktree and tmux session, deletes the branch by default for Grove-created branches. |

A PAUSED row dims attach and pause, OFFLINE lights respawn, ORPHANED leaves
only kill. The rule lives in one function in
[`src/grove/tui/screens/list.py`](repo:src/grove/tui/screens/list.py), so the footer never drifts from what
is runnable.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-filter.svg" alt="Filter bar narrowed to workspaces matching "auth"" /></div>
  <figcaption class="ms-shot__body">Filter bar in action, matched substring-style across title, branch, and agent.</figcaption>
</figure>

## The Activity Dashboard screen

Press ++d++ for the Activity Dashboard: every workspace across every
repository the daemon knows, one tile each, grouped by project.

- Tiles size by urgency: quiet is a compact three-row tile, working,
  waiting, blocked, or erroring gets a taller tile with a live terminal tail.
- Each tile carries branch, agent and model, diff and ahead-behind counts,
  turn and token totals, and the agent's one-line summary.

| Key | Action |
|-----|--------|
| ++l++ | Cycle the lens: all, needs attention, active. |
| ++g++ | Toggle grouping: by project, or one flat wall. |
| ++r++ | Refresh. |
| ++d++ / ++esc++ / ++q++ | Back to the list. |

Tile meaning and attention sorting are on
[agent activity and sessions](features-activity.md).

## The project switcher

Press ++shift+p++ to hop repositories without leaving the TUI.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-project-switcher.svg" alt="Project switcher listing two repositories with workspace counts and a current tag" /></div>
  <figcaption class="ms-shot__body">Project switcher. Type to narrow, arrows to move, <code>Enter</code> to switch, current repo tagged.</figcaption>
</figure>

- Lists every known repository with its workspace count, current one
  tagged.
- Filter input holds focus, arrows move the highlight, ++enter++ switches,
  ++esc++ cancels.
- A cheap read keeps it instant across many repos. Navigation chooser, not
  the dashboard. For live status, ++d++.

## Steering an agent

Press ++m++, type a follow-up, and press ++enter++ to steer the selected
workspace's agent without attaching.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-steer.svg" alt="Send-message modal with a follow-up typed to a running agent" /></div>
  <figcaption class="ms-shot__body">Steer modal. A quick redirect to a running agent, no attach required.</figcaption>
</figure>

- Delivery follows the agent: local agents get it typed into their tmux
  pane, remote agents such as Mewbo get it over the remote API. Either way
  you stay on the list.
- ++m++ lights up only for a running agent, and Grove flashes the reason
  rather than failing silently if it cannot take the message.

## The sessions browser

Press ++s++ for a workspace's recorded sessions on the left and
turn-by-turn history on the right.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-sessions.svg" alt="Sessions browser with a session list on the left and turn history on the right" /></div>
  <figcaption class="ms-shot__body">Sessions browser: sessions left, selected session's turns right.</figcaption>
</figure>

- Each row carries state, turn count, model, and whether Grove or you
  started the agent.
- Highlight a session and its turns fill the right pane, tool calls
  collapsed into single grouped rows.

| Key | Action |
|-----|--------|
| ++t++ | Toggle tool detail: collapsed into one row, or expanded per call. |
| ++r++ | Refresh the session list. |
| ++esc++ / ++q++ | Back to the list. |

Recorded history does not stream, so the screen has no live tick. It reads
straight from disk, so transcripts outlive their worktrees, readable even
for a paused or orphaned workspace.

## Modals

### Create

++n++ opens an eight-step modal.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-create-modal.svg" alt="Create workspace modal showing branch source variants and agent picker" /></div>
  <figcaption class="ms-shot__body">Create modal. Branch source on the left, agent picker, title input.</figcaption>
</figure>

1. **Agent.** Radio list of every agent the cascade resolved.
2. **Runtime.** Host or Container, marking the cascade default
   (`container.enabled`).
3. **First-turn brief.** Cascade default, On, or Off, same as `--brief` on
   the [CLI](use-cli.md). On briefs the agent toward `working-in-grove`.
4. **Model** (blank = agent default). Free text, e.g. `sonnet` or `gpt-5.5`,
   unvalidated and forwarded verbatim, same as `grove create --model`. See
   [`screens/create.py`](repo:src/grove/tui/screens/create.py).
5. **Title.** Free text, pre-fills from the chosen branch name.
6. **Branch source.** *Auto* (Grove names it), *New named* (you type it),
   *Existing local* (from your branches), *Track remote* (a remote-only
   branch, tracked locally), or *Root* (no worktree, repo root, current
   branch). See [root workspaces](features-workspace-lifecycle.md#root-workspaces)
   and [branch provenance](features-branch-provenance.md).
7. **Skip init script.** Skips the [init script](configure-init-scripts.md)
   for this create, checked by default under *Root* since init scripts
   bootstrap fresh worktrees. Uncheck it if not.
8. **Confirm** with ++enter++. ++esc++ cancels.

### Edit

++e++ edits a workspace's title and description, metadata only, so the
worktree directory and tmux session keep their original names.

### Kill confirmation

++k++ opens a confirm modal with a branch-deletion toggle, defaulted from the
workspace's `branch_provenance`: GROVE_CREATED deletes it, USER_ATTACHED keeps
it, either flippable. Grove never touches remote branches, so remote deletion
needs `git push --delete` from your shell.

### Pause confirmation

++p++ opens a smaller confirm modal, naming the branch retained and warning
if uncommitted changes would block the pause.

### Help

++question++ opens a read-only key reference grouped by zone, any key dismisses.

<div class="swiper ms-shots">
  <div class="swiper-wrapper">
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-edit-modal.svg" alt="Edit workspace modal with title and description fields">
        <figcaption>Edit modal (<code>e</code>): title and description are metadata, worktree path and session name stay fixed.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-kill-confirm.svg" alt="Kill confirm modal">
        <figcaption>Kill confirm (<code>k</code>): checkbox default reflects whether Grove created the branch.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-pause-confirm.svg" alt="Pause confirm modal">
        <figcaption>Pause confirm (<code>p</code>): Grove refuses a dirty worktree, commit or stash first.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-help.svg" alt="Help modal">
        <figcaption>Help modal (<code>?</code>): pulled from the same <code>DEFAULT_BINDINGS</code> tuple the footer reads.</figcaption>
      </figure>
    </div>
  </div>
  <div class="swiper-pagination"></div>
  <div class="swiper-button-prev"></div>
  <div class="swiper-button-next"></div>
</div>

### Pairing

With the [web dashboard](use-webapp.md), a new browser's pairing request
pops a modal showing the device label and a code to confirm. Approve with
++a++, deny with ++d++. Full handshake and the `grove auth` commands for
headless hosts are on [authentication & pairing](use-auth.md).

## Theme

Grove ships three built-in themes: `dark`, `light`, and `auto` (follows
Textual's reported terminal polarity). User overrides land at
`${user_config_dir}/grove/themes/<name>.toml`, referenced by name in
`ui.theme`.

## Mouse vs keyboard

Both work. Clicking selects a row, hover outlines it, and every action also
has a keyboard binding.
