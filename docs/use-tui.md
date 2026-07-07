# TUI tour

The TUI is the primary way to drive Grove. This page names every region
and lists every key.

<video autoplay muted loop playsinline preload="auto" poster="../img/posters/terminal-still.png" style="width:100%;max-width:960px;height:auto;display:block;margin:0 auto 1.75rem;">
  <source src="../videos/1-grove-terminal.mp4" type="video/mp4" />
</video>

## Screen anatomy

The list screen has three vertical zones with a status bar and contextual
footer along the bottom.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-list.svg" alt="Grove TUI showing four workspaces in mixed states with a live peek rail" /></div>
  <figcaption class="ms-shot__body">Header (top), filter bar (slash-toggled), workspace list (left), peek rail (right), status bar and contextual footer (bottom).</figcaption>
</figure>

- **Header** carries the repo name and a count chip.
- **Filter bar** stays hidden until you press `/`. Type to narrow by title,
  branch, or agent. `Esc` clears.
- **Workspace list** is the left zone. Each row is a card with status glyph,
  title, agent, branch, and a stat strip (ahead, behind, dirty) with
  polarity-aware colours. When Grove can read the agent's session, the card
  also shows the agent's state, glyph and label, such as `▶ working` or
  `◑ waiting`.
- **Peek rail** sits on the right. The *summary* card carries branch,
  stats, and age, plus an agent metrics line when a session is live: the
  model, turn and reply and tool-call counts, token usage, and the
  agent's state. It also lists recent commits. Below it, a *preview* pane
  has two tabs. The *transcript* tab shows the agent's recent turns with
  tool calls grouped into single rows. The *terminal* tab mirrors the
  live tmux pane, refreshed at four ticks per second. See
  [the peek rail](features-peek.md) for the full breakdown.
- **Status bar** sums the fleet on the left and shows the selected workspace
  on the right. The bar's background changes with state: clay by default,
  amber when any workspace needs attention, neutral when the fleet is empty.
- **Contextual footer** lists the keys that apply right now. Global keys on
  the left, selection keys on the right, separated by a muted divider.

## Keybindings

The footer splits into two groups. **Global keys** apply at any time,
whatever row is selected. **Selection keys** act on the highlighted
workspace, and the ones that do not fit its current status render dimmed.

Global keys:

| Key | Action |
|-----|--------|
| `n` | Create a new workspace. |
| `d` | Open the [Activity Dashboard](features-activity.md), the cross-project wall. |
| `P` | Switch project. Jump to another repository the daemon knows about. |
| `r` | Refresh the list and the peek rail. |
| `/` | Filter. Type to narrow, `Esc` clears. |
| `?` | Help modal. On-screen reference for every key. |
| `q` | Quit. |

Selection keys:

| Key | Action |
|-----|--------|
| `Enter` / `a` | Attach to the selected workspace. |
| `m` | Send a message. Steer the running agent without attaching. |
| `e` | Edit the selected workspace's title and description. |
| `s` | Browse the workspace's recorded agent sessions. |
| `x` | Remap session. Re-point the workspace at a live or recovered agent session, e.g. after `/clear` rotated the id. See [`screens/remap_session.py`](repo:src/grove/tui/screens/remap_session.py). |
| `p` | Pause. Removes the worktree, keeps the branch. |
| `R` | Resume. Recreates the worktree from the branch and restarts tmux. |
| `o` | Respawn an OFFLINE workspace whose tmux session vanished. |
| `k` | Kill. Removes the worktree and tmux session. Deletes the branch by default for Grove-created branches. |

The footer adapts to the selected row. Global keys stay on the left.
Selection keys sit on the right, and the ones that do not apply to the
row's current status render dimmed. A PAUSED row dims attach and pause,
an OFFLINE row lights respawn, an ORPHANED row leaves only kill. The
availability rule lives in one function in [`src/grove/tui/screens/list.py`](repo:src/grove/tui/screens/list.py), so the
footer never drifts from what is actually runnable.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-filter.svg" alt="Filter bar narrowed to workspaces matching "auth"" /></div>
  <figcaption class="ms-shot__body">Filter bar in action. Values are matched substring-style across title, branch, and agent.</figcaption>
</figure>

## The Activity Dashboard screen

The list screen shows one repository. Press `d` and the screen flips to
the Activity Dashboard: every workspace across every repository the
daemon knows about, one tile per workspace, grouped by project.

Tiles size themselves by urgency. A quiet workspace gets a compact
three-row tile. A workspace whose agent is working, waiting, blocked,
or erroring gets a taller tile with a live tail of the agent's
terminal. Each tile carries branch, agent and model, diff and
ahead-behind counts, turn and token totals, and the agent's one-line
summary of what it is doing.

| Key | Action |
|-----|--------|
| `l` | Cycle the lens: all, needs attention, active. |
| `g` | Toggle grouping: by project, or one flat wall. |
| `r` | Refresh. |
| `d` / `Esc` / `q` | Back to the list. |

What the tiles mean, which signals feed them, and how attention
sorting works is on the
[agent activity and sessions](features-activity.md) page.

## The project switcher

The list screen shows one repository at a time. Press `P` to hop to
another without leaving the TUI. A small picker opens with every
repository the daemon knows about, each row showing the repo name and how
many workspaces it holds. The one you are looking at carries a *current*
tag.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-project-switcher.svg" alt="Project switcher listing two repositories with workspace counts and a current tag" /></div>
  <figcaption class="ms-shot__body">Project switcher. Type to narrow, arrows to move, <code>Enter</code> to switch. The current repo is tagged.</figcaption>
</figure>

The picker borrows the command-palette feel: a filter input holds focus,
so you type to narrow and the arrow keys move the highlight. `Enter`
switches to the highlighted repo, `Esc` cancels. The counts come from a
cheap read, so the picker opens instantly even across many repos. It is a
navigation chooser, not the dashboard. For live cross-repo agent status,
reach for `d` instead.

## Steering an agent

Press `m` to send a follow-up to the selected workspace's agent without
attaching. Type your message, press `Enter`, and Grove delivers it.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-steer.svg" alt="Send-message modal with a follow-up typed to a running agent" /></div>
  <figcaption class="ms-shot__body">Steer modal. A quick redirect to a running agent, no attach required.</figcaption>
</figure>

Delivery follows the agent. For a local agent, Grove types the message
into its tmux pane, the same keystrokes you would send by hand. For a
remote agent such as Mewbo, Grove sends it over the remote API. Either
way you stay on the list. The `m` key lights up only for a running agent,
and if the agent cannot take the message right now, Grove flashes the
reason instead of failing silently.

## The sessions browser

Press `s` to read a workspace's recorded agent sessions. The screen
splits in two: a list of sessions on the left, the turn-by-turn history
on the right.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-sessions.svg" alt="Sessions browser with a session list on the left and turn history on the right" /></div>
  <figcaption class="ms-shot__body">Sessions browser. Sessions on the left, the selected session's turns on the right.</figcaption>
</figure>

Each session row carries its state, turn count, model, and whether Grove
started it or you launched the agent by hand. Highlight a session and the
right pane fills with its turns. Tool calls collapse into single grouped
rows so the conversation stays readable.

| Key | Action |
|-----|--------|
| `t` | Toggle tool detail: collapse tool runs into one row, or expand each call. |
| `r` | Refresh the session list. |
| `Esc` / `q` | Back to the list. |

Recorded history does not stream, so the screen has no live tick. It
reads sessions straight from disk, which means transcripts outlive their
worktrees. You can still read the history of a paused or even orphaned
workspace.

## Modals

### Create

`n` opens a six-step modal.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-create-modal.svg" alt="Create workspace modal showing branch source variants and agent picker" /></div>
  <figcaption class="ms-shot__body">Create modal. Branch source on the left, agent picker, title input.</figcaption>
</figure>

1. **Agent.** Radio list of every agent the cascade resolved.
2. **Model** (blank = agent default). Free text, e.g. `sonnet`/`opus`/`haiku`
   for claude, `gpt-5.5` for codex. The placeholder hints at the union of
   every configured agent's resolved model catalog, but the field never
   validates against it: whatever you type is forwarded to the agent
   verbatim, same as `grove create --model` on the CLI. See
   [`screens/create.py`](repo:src/grove/tui/screens/create.py).
3. **Title.** Free text. Pre-fills with the chosen branch name when one is
   available.
4. **Branch source.** Pick *Auto* (Grove names the branch), *New named* (you
   type the name), *Existing local* (pick from your repo's branches),
   *Track remote* (pick a remote-only branch and create a tracking local),
   or *Root* (no worktree at all; the workspace runs in the repo root on
   your current branch). Each variant carries its own form. All are mounted
   in the DOM with the inactive ones hidden, so values persist across mode
   switches. Root workspaces have their own rules; see
   [root workspaces](features-workspace-lifecycle.md#root-workspaces).
5. **Skip init script.** A checkbox that skips the
   [init script](configure-init-scripts.md) for this one create. Picking
   *Root* checks it for you, since init scripts are built to bootstrap
   fresh worktrees. You can uncheck it.
6. **Confirm** with `Enter`. `Esc` cancels.

The branch-source plumbing is documented in
[branch provenance](features-branch-provenance.md).

### Edit

`e` opens the edit modal to change a workspace's title and description. Both
are metadata only. The worktree directory and tmux session keep their
original names, so attached clients and your muscle memory are never
disrupted.

### Kill confirmation

`k` opens a confirm modal that doubles as a branch-deletion toggle. The
checkbox default is driven by the workspace's `branch_provenance`.
GROVE_CREATED defaults to "delete the branch". USER_ATTACHED defaults to
"keep the branch". You can flip either way. Grove never touches remote
branches. Remote deletion requires `git push --delete` from your shell.

### Pause confirmation

`p` opens a smaller confirm modal that names the branch retained and warns
when there are uncommitted changes that would block the pause.

### Help

`?` opens a read-only key reference grouped by zone. Press any key to
dismiss.

<div class="swiper ms-shots">
  <div class="swiper-wrapper">
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-edit-modal.svg" alt="Edit workspace modal with title and description fields">
        <figcaption>Edit modal (<code>e</code>). Title and description are metadata; the worktree path and session name stay fixed.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-kill-confirm.svg" alt="Kill confirm modal">
        <figcaption>Kill confirm (<code>k</code>). The checkbox default reflects whether Grove created the branch.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-pause-confirm.svg" alt="Pause confirm modal">
        <figcaption>Pause confirm (<code>p</code>). Grove refuses to pause a dirty worktree. Commit or stash first.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/tui-help.svg" alt="Help modal">
        <figcaption>Help modal (<code>?</code>). Pulled from the same <code>DEFAULT_BINDINGS</code> tuple the contextual footer reads.</figcaption>
      </figure>
    </div>
  </div>
  <div class="swiper-pagination"></div>
  <div class="swiper-button-prev"></div>
  <div class="swiper-button-next"></div>
</div>

### Pairing

When the [web dashboard](use-webapp.md) is in use, the TUI also surfaces
device pairing. A request from a new browser pops a modal on top of whatever
screen you are on, showing the device label and a code to confirm. Approve
with `a`, deny with `d`. The full handshake, and the `grove auth` commands
for headless hosts, are on the [authentication & pairing](use-auth.md) page.

## Theme

Grove ships three built-in themes: `dark`, `light`, and `auto` (which
follows the terminal polarity reported by Textual). User overrides land at
`${user_config_dir}/grove/themes/<name>.toml`. Reference one by name in
`ui.theme` and Grove resolves it at startup.

## Mouse vs keyboard

Both work. The cursor selects rows on hover, and clicking a row selects.
The selected row carries a clay border. Hovered rows that aren't selected
get a muted gray outline so the mouse position is visible. Every action
has a keyboard binding.
