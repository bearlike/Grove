# TUI tour

The TUI is the primary way to drive Grove. This page names every region
and lists every key.

## Screen anatomy

The list screen has three vertical zones with a status bar and contextual
footer along the bottom.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-list.png" alt="Grove TUI showing four workspaces in mixed states with a live peek rail" /></div>
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
- **Peek rail** has two cards on the right. The *summary* card carries
  branch, stats, and age, plus an agent metrics line when a session is
  live: the model, turn and reply and tool-call counts, token usage, and
  the agent's state. The *agent* card mirrors the live tmux pane,
  refreshed at four ticks per second.
- **Status bar** sums the fleet on the left and shows the selected workspace
  on the right. The bar's background changes with state: clay by default,
  amber when any workspace needs attention, neutral when the fleet is empty.
- **Contextual footer** lists the keys that apply right now. Global keys on
  the left, selection keys on the right, separated by a muted divider.

## Keybindings

| Key | Action |
|-----|--------|
| `n` | Create a new workspace. |
| `d` | Open the [Activity Dashboard](features-activity.md), the cross-project wall. |
| `Enter` / `a` | Attach to the selected workspace. |
| `e` | Edit the selected workspace's title and description. |
| `p` | Pause. Removes the worktree, keeps the branch. |
| `R` | Resume. Recreates the worktree from the branch and restarts tmux. |
| `o` | Respawn an OFFLINE workspace whose tmux session vanished. |
| `k` | Kill. Removes the worktree and tmux session. Deletes the branch by default for Grove-created branches. |
| `r` | Refresh the list and the peek rail. |
| `/` | Filter. Type to narrow, `Esc` clears. |
| `?` | Help modal. On-screen reference for every key. |
| `q` | Quit. |

The footer adapts to the selected row. Global keys stay on the left.
Selection keys sit on the right, and the ones that do not apply to the
row's current status render dimmed. A PAUSED row dims attach and pause,
an OFFLINE row lights respawn, an ORPHANED row leaves only kill. The
availability rule lives in one function in `screens/list.py`, so the
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

## Modals

### Create

`n` opens a five-step modal.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-create-modal.png" alt="Create workspace modal showing branch source variants and agent picker" /></div>
  <figcaption class="ms-shot__body">Create modal. Branch source on the left, agent picker, title input.</figcaption>
</figure>

1. **Branch source.** Pick *Auto* (Grove names the branch), *New named* (you
   type the name), *Existing local* (pick from your repo's branches),
   *Track remote* (pick a remote-only branch and create a tracking local),
   or *Root* (no worktree at all; the workspace runs in the repo root on
   your current branch). Each variant carries its own form. All are mounted
   in the DOM with the inactive ones hidden, so values persist across mode
   switches. Root workspaces have their own rules; see
   [root workspaces](features-workspace-lifecycle.md#root-workspaces).
2. **Agent.** Radio list of every agent the cascade resolved.
3. **Title.** Free text. Pre-fills with the chosen branch name when one is
   available.
4. **Skip init script.** A checkbox that skips the
   [init script](configure-init-scripts.md) for this one create. Picking
   *Root* checks it for you, since init scripts are built to bootstrap
   fresh worktrees. You can uncheck it.
5. **Confirm** with `Enter`. `Esc` cancels.

The branch-source plumbing is documented in
[branch provenance](features-branch-provenance.md).

### Edit

`e` opens the edit modal to change a workspace's title and description. Both
are metadata only. The worktree directory and tmux session keep their
original names, so attached clients and your muscle memory are never
disrupted.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-edit-modal.png" alt="Edit workspace modal with title and description fields" /></div>
  <figcaption class="ms-shot__body">Edit modal. Title and description are metadata; the worktree path and session name stay fixed.</figcaption>
</figure>

### Kill confirmation

`k` opens a confirm modal that doubles as a branch-deletion toggle. The
checkbox default is driven by the workspace's `branch_provenance`.
GROVE_CREATED defaults to "delete the branch". USER_ATTACHED defaults to
"keep the branch". You can flip either way. Grove never touches remote
branches. Remote deletion requires `git push --delete` from your shell.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-kill-confirm.svg" alt="Kill confirm modal" /></div>
  <figcaption class="ms-shot__body">Kill confirm. The checkbox default reflects whether Grove created the branch.</figcaption>
</figure>

### Pause confirmation

`p` opens a smaller confirm modal that names the branch retained and warns
when there are uncommitted changes that would block the pause.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-pause-confirm.svg" alt="Pause confirm modal" /></div>
  <figcaption class="ms-shot__body">Pause confirm. Grove refuses to pause a dirty worktree. Commit or stash first.</figcaption>
</figure>

### Help

`?` opens a read-only key reference grouped by zone. Press any key to
dismiss.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-help.svg" alt="Help modal" /></div>
  <figcaption class="ms-shot__body">Help modal. Pulled from the same <code>DEFAULT_BINDINGS</code> tuple the contextual footer reads.</figcaption>
</figure>

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
