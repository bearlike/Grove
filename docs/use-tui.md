# TUI tour

## Screen anatomy

<video preload="auto" poster="../img/posters/terminal-still.png">
  <source src="../videos/1-grove-terminal.mp4" type="video/mp4" />
</video>

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-list.svg" alt="Grove TUI showing four workspaces in mixed states with a live peek rail" /></div>
  <figcaption class="ms-shot__body">Header, filter bar (slash-toggled), workspace list left, peek rail right, status bar and footer at bottom.</figcaption>
</figure>

- **Header.** Repo name, count chip.
- **Filter bar.** Hidden until ++slash++, narrows title, branch, agent.
  ++esc++ clears.
- **Workspace list.** Row: status glyph, title, agent, branch, stat strip,
  state (`▶ working`, `◑ waiting`), and
  [task phase](features-status.md#the-third-axis-task-phase).
- **Peek rail.** Summary, then linked tickets, then transcript and terminal
  tabs ([detail](features-peek.md)).
- **Status bar.** Fleet left, workspace right, coloured by state.
- **Footer.** Global and selection keys.

## Keybindings

Global keys work anytime. Selection keys act on the highlighted row.

| Key | Action |
|-----|--------|
| ++n++ | Create a workspace. |
| ++d++ | Open the [Activity Dashboard](features-activity.md). |
| ++shift+p++ | Switch project. |
| ++r++ | Refresh. |
| ++slash++ | Filter. Type to narrow, ++esc++ clears. |
| ++question++ | Help modal, key reference. |
| ++q++ | Quit. |

| Key | Action |
|-----|--------|
| ++enter++ / ++a++ | Attach. |
| ++m++ | Steer without attaching. |
| ++e++ | Edit title and description. |
| ++s++ | Browse recorded sessions. |
| ++x++ | Remap to a live or recovered session, e.g. after `/clear` rotates the id ([`remap_session.py`](repo:src/grove/tui/screens/remap_session.py)). |
| ++p++ | Pause, removes worktree, keeps branch. |
| ++shift+r++ | Resume, recreates worktree, restarts tmux. |
| ++o++ | Respawn an OFFLINE workspace. |
| ++k++ | Kill, removes worktree and tmux, deletes Grove-created branches. |

PAUSED dims attach and pause, OFFLINE lights respawn, ORPHANED leaves only
kill ([`list.py`](repo:src/grove/tui/screens/list.py)).

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-filter.svg" alt="Filter bar narrowed to workspaces matching "auth"" /></div>
  <figcaption class="ms-shot__body">Filter bar in action, matched substring-style across title, branch, and agent.</figcaption>
</figure>

## Activity Dashboard

Press ++d++: every workspace, one tile each, grouped by project.

- Tiles size by urgency: quiet is compact, working, waiting, blocked, or
  erroring gets a taller tile with a live terminal tail.
- Each tile: branch, agent, model, diff, ahead-behind, tokens, summary.

| Key | Action |
|-----|--------|
| ++l++ | Cycle lens: all, needs attention, active. |
| ++g++ | Toggle grouping: by project, or flat. |
| ++r++ | Refresh. |
| ++d++ / ++esc++ / ++q++ | Back to the list. |

Tile meaning: [activity and sessions](features-activity.md).

## Project switcher

Press ++shift+p++ to hop repositories.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-project-switcher.svg" alt="Project switcher listing two repositories with workspace counts and a current tag" /></div>
  <figcaption class="ms-shot__body">Project switcher. Type to narrow, arrows to move, <code>Enter</code> to switch, current repo tagged.</figcaption>
</figure>

- Lists every repository with its workspace count. Filter holds focus,
  ++enter++ switches, ++esc++ cancels.
- A cheap, instant chooser, not a dashboard. ++d++ for live status.

## Steering an agent

Press ++m++, type a follow-up, ++enter++ to steer.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-steer.svg" alt="Send-message modal with a follow-up typed to a running agent" /></div>
  <figcaption class="ms-shot__body">Steer modal. A quick redirect to a running agent, no attach required.</figcaption>
</figure>

- Delivery follows the agent: local gets it typed into tmux, remote over
  the remote API.
- ++m++ lights up only for a running agent, else Grove flashes why.

## The sessions browser

Press ++s++ to browse recorded sessions.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-sessions.svg" alt="Sessions browser with a session list on the left and turn history on the right" /></div>
  <figcaption class="ms-shot__body">Sessions browser: sessions left, selected session's turns right.</figcaption>
</figure>

- Each row: state, turn count, model, who started the agent.
- Highlight a session: its turns fill the right pane, tool calls collapsed
  into groups.

| Key | Action |
|-----|--------|
| ++t++ | Toggle tool detail, collapsed or expanded. |
| ++r++ | Refresh the session list. |
| ++esc++ / ++q++ | Back to the list. |

Recorded history reads from disk, not live: transcripts outlive their
worktrees.

## Modals

### Create

++n++ opens an eight-step modal.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-create-modal.svg" alt="Create workspace modal showing branch source variants and agent picker" /></div>
  <figcaption class="ms-shot__body">Create modal. Branch source on the left, agent picker, title input.</figcaption>
</figure>

1. **Agent.** Radio list of agents the cascade resolved.
2. **Runtime.** Host or Container (`container.enabled` default).
3. **First-turn brief.** Cascade default, On, or Off (`--brief` on
   [CLI](use-cli.md)).
4. **Model** (blank = default). Free text like `sonnet`, unvalidated,
   forwarded verbatim ([`create.py`](repo:src/grove/tui/screens/create.py)).
5. **Title.** Free text, pre-fills from branch name.
6. **Branch source.** *Auto*, *New named*, *Existing local*, *Track remote*,
   or *Root*: [root workspaces](features-workspace-lifecycle.md#root-workspaces),
   [branch provenance](features-branch-provenance.md).
7. **Skip init script.** Skips the [init script](configure-init-scripts.md),
   default checked under *Root*.
8. **Confirm** with ++enter++, ++esc++ cancels.

### Edit

++e++ edits title and description, metadata only.

### Kill confirmation

++k++ opens a confirm modal with a `branch_provenance` deletion toggle:
GROVE_CREATED deletes, USER_ATTACHED keeps, either flippable. Remote
deletion needs `git push --delete`.

### Pause confirmation

++p++ opens a smaller confirm modal naming the retained branch.

### Help

++question++ opens a read-only key reference. Any key dismisses.

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

A new [web dashboard](use-webapp.md) pairing request pops a modal with a
device label and code. Approve with ++a++, deny with ++d++. See
[authentication & pairing](use-auth.md).

## Theme

Grove ships `dark`, `light`, and `auto` (follows the terminal's polarity).
Overrides land at `${user_config_dir}/grove/themes/<name>.toml`, referenced
by `ui.theme`.

## Mouse vs keyboard

Both work. Clicking selects a row, hover outlines it, every action has a
keybinding.
