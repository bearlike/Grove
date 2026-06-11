# Grove Design System

> The codified visual + interaction language of the Grove TUI: tokens,
> layout, components, and the rules that make them feel like one product
> instead of a stack of widgets.

This document is the canonical reference for Grove's terminal UI. It
mirrors the structure of mainstream design systems
([Material Design 3](https://m3.material.io/foundations/design-tokens),
[GitLab Pajamas](https://design.gitlab.com/product-foundations/design-tokens/),
[Carbon](https://carbondesignsystem.com/elements/color/tokens/),
[Atlassian](https://atlassian.design/foundations/tokens/design-tokens/))
but adapts the vocabulary to a Textual / Rich TUI: characters and SGR
escapes, not pixels and shadows.

**Audience**

- Engineers adding or editing widgets in `src/grove/tui/`.
- Anyone writing a theme override (`*.toml` in
  `${user_config_dir}/grove/themes/`).
- Reviewers evaluating whether a change preserves Grove's visual
  contract.

**Source-of-truth files** — every claim in this document is
implementation-anchored. If they disagree, the code wins; please update
this doc in the same PR.

| Concern | Authoritative file |
|---|---|
| Color hex atoms + Theme objects | [`src/grove/tui/theme.py`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/theme.py) |
| Rich-side color accessors (Rich `Text` consumers) | [`src/grove/tui/_status.py`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/_status.py) |
| Shared card chrome (`.grove-card`) | [`src/grove/tui/app.py`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/app.py) |
| Modal chrome (`.grove-dialog`) | [`src/grove/tui/screens/_modal.py`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/screens/_modal.py) |
| Default keybindings + footer partitions | [`src/grove/tui/keys.py`](https://github.com/bearlike/Grove/blob/current/src/grove/tui/keys.py) |
| Status enum (persisted vs. computed) | [`src/grove/core/workspace.py`](https://github.com/bearlike/Grove/blob/current/src/grove/core/workspace.py) |
| Project rationale | [`CLAUDE.md`](https://github.com/bearlike/Grove/blob/current/CLAUDE.md) ("Session lessons (non-trivial)") |

---

## Table of contents

- [1. Philosophy](#1-philosophy)
- [2. Design principles](#2-design-principles)
- [3. Foundations](#3-foundations)
  - [3.1 Polarity (dark / light)](#31-polarity-dark--light)
  - [3.2 Tier model — inset wells on an ambient canvas](#32-tier-model--inset-wells-on-an-ambient-canvas)
  - [3.3 Color system](#33-color-system)
  - [3.4 Typography](#34-typography)
  - [3.5 Spacing & layout](#35-spacing--layout)
  - [3.6 Borders & focus chrome](#36-borders--focus-chrome)
  - [3.7 Glyphs](#37-glyphs)
- [4. Tokens](#4-tokens)
  - [4.1 Token tiers](#41-token-tiers)
  - [4.2 Surface tokens](#42-surface-tokens)
  - [4.3 Status tokens](#43-status-tokens)
  - [4.4 Ref / diff tokens](#44-ref--diff-tokens)
  - [4.5 Init-status tokens](#45-init-status-tokens)
  - [4.6 Chrome tokens](#46-chrome-tokens)
  - [4.7 How to consume each token](#47-how-to-consume-each-token)
- [5. Layout](#5-layout)
  - [5.1 Screen anatomy — list screen](#51-screen-anatomy--list-screen)
  - [5.2 Modal anatomy](#52-modal-anatomy)
  - [5.3 Width tiers](#53-width-tiers)
- [6. Components](#6-components)
  - [6.1 Header](#61-header)
  - [6.2 FilterBar](#62-filterbar)
  - [6.3 WorkspaceList](#63-workspacelist)
  - [6.4 WorkspaceCard](#64-workspacecard)
  - [6.5 PeekRail](#65-peekrail)
  - [6.6 StatusBar](#66-statusbar)
  - [6.7 ContextualFooter](#67-contextualfooter)
  - [6.8 Modals — Confirm, Create, Help](#68-modals--confirm-create-help)
- [7. Patterns](#7-patterns)
- [8. Interaction model](#8-interaction-model)
- [9. Theming & overrides](#9-theming--overrides)
- [10. Glossary](#10-glossary)
- [11. References & influences](#11-references--influences)

---

## 1. Philosophy

Grove is an **IDE-in-a-terminal**. The visual language treats the
screen as an editor workbench — a fixed set of named panels with strong
chrome boundaries, a status row that asserts identity and state, and a
contextual footer that shows the keys the user can press right now. The
inspirations are explicit:

- **lazygit** — focus is communicated by swapping the active panel's
  border to the brand color; everything else fades to a quiet inactive
  hue. ([Lazygit theme reference](https://github.com/jesseduffield/lazygit/blob/master/docs/Config.md))
- **VS Code workbench** — the bottom row is one whole-width status bar
  whose **background** asserts the application's state (clay normal,
  amber attention, neutral empty). Padding separates segments; we don't
  paint chip backgrounds on individual items. ([VS Code Status Bar UX](https://code.visualstudio.com/api/ux-guidelines/status-bar))
- **k9s** — keyboard-first navigation over a workspace list, with a
  command-hint footer that keeps the input alphabet visible.
- **claude-squad** — the immediate sibling tool; we borrowed the
  per-group footer divider (`│`) and the flash auto-clear cadence,
  diverged on chrome (claude-squad is flatter; we're tier-rotated).
- **bearlike/Assistant `Assistant console`** — the canonical clay palette
  ([`the bearlike/Assistant brand palette`](https://github.com/bearlike/Assistant)
  + `warm_terracotta.toml`). Every hex atom in `theme.py` originated
  there; theme overrides follow that file's TOML shape.

The product result: a user who has used any of those tools in the last
year should look at Grove and feel oriented in under five seconds.

---

## 2. Design principles

These rules govern judgment calls. Codify, don't argue. Each principle
ties back to a concrete decision in the codebase.

1. **One source of color truth.** Every hex literal lives in
   `theme.py`. TCSS reaches it via `$varname`; Rich-side widgets reach
   it via `_status.py` accessors keyed on `dark: bool`. Widgets never
   inline literal hex.
2. **Mechanism, not policy** — the user (or theme author) owns the
   policy via TOML overrides; the codebase owns the mechanism. A
   one-line override that just changes `primary` is the intended
   ergonomics.
3. **Focus chrome is CSS, never glyphs in body text.** A focused row
   is a row whose *border* swapped color, not a row whose body got a
   `▌` prefix prepended. Two sources of truth (CSS + body) drift; the
   render then differs from the assertable visual state.
4. **Three tiers per element: bold + colored values, bold default-fg
   counters, muted labels and connectives.** Never reach for terminal
   `dim` for muted text — its interpretation drifts across emulators
   and breaks dark/light parity. Use `chrome_color('muted')`.
5. **Polarity-aware semantics.** Counts that mean "all is well at
   zero" (ahead, behind, dirty) render muted at zero and promote to a
   semantic hue when nonzero. Label and value share the polarity hue
   so the pair reads as one chunk.
6. **Each panel has a unique role-noun title.** `workspaces ·
   summary · Live Workspace Preview` — never `workspaces` next to
   `workspace`. The preview card was previously titled `agent`; that
   was inaccurate because the captured window can host any process.
7. **Tier model = inset wells on an ambient canvas.** Panels
   (`$surface`) are darker than the screen root (`$background`). The
   highlighted row lifts to the lightest tier (`$panel`). Same axis in
   dark and light. This is **not** Material's "raised surface" — the
   panel sits *below* canvas, not above.
8. **Boring code beats clever code.** Reuse the pattern already in
   the project. Inline 5 lines of TCSS twice rather than hoist a base
   widget on first sight; promote when there's a third consumer.
9. **Width-responsive collapse drops the lowest-priority signal
   first.** Brand identity and count chips never drop. Theme indicator
   drops first; selection summary and filter chip drop next. The peek
   rail is the heaviest segment and is hidden entirely below the narrow
   threshold.
10. **Side effects belong at edges; pure renderers belong everywhere
    else.** `_render_card`, `_render_workspace`, `_render_pane_body`,
    and `StatusBar.render` are all pure — given the same inputs they
    produce the same `Text`. No `app.current_theme` reads inside
    render helpers; the calling widget reads `dark` once and forwards.

---

## 3. Foundations

### 3.1 Polarity (dark / light)

Polarity is a **keyed dimension** of every Rich-side lookup table. The
type is `bool` (`True` = dark) — Grove ships two built-in themes,
`grove-dark` (default) and `grove-light`, and any TOML override declares
its polarity in the file (`dark = true|false`). The polarity bit selects
which half of `STATUS_HEX`, `INIT_STATUS_HEX`, `REF_HEX`, and
`CHROME_HEX` is consulted.

Why a `bool`, not a literal: Textual's `Theme.dark` is the same shape;
we mirror it so `app.current_theme.dark` flows through unchanged.

**Theme cascade (lowest → highest precedence):**

1. Built-in: `GROVE_DARK` and `GROVE_LIGHT` registered in
   `register_themes()`.
2. User TOML overrides: every `*.toml` in
   `${user_config_dir}/grove/themes/` becomes one additional
   registered theme. Missing fields inherit from the matching-polarity
   built-in (so a one-line file changing only `primary` is the
   ergonomic minimum).
3. Selection: `cfg.ui.theme` chooses which registered theme is active
   at startup. Values: `auto` / `dark` (→ `grove-dark`), `light`
   (→ `grove-light`), or a custom name registered above.

Validation that the chosen name *exists* happens at app startup in
`resolve_theme_name`, **not** at config validation. This is intentional
— a saved `cfg.ui.theme = "midnight-clay"` shouldn't fail validation
just because the override file isn't on disk yet.

### 3.2 Tier model — inset wells on an ambient canvas

The most counter-intuitive thing about Grove's color system. Read this
section before touching `_DARK_BG`, `_DARK_SURFACE`, or `_DARK_PANEL`.

Mainstream UI frameworks (Material, Bootstrap) treat `background` as
the deepest tier and `panel`/`elevation` as the lightest — content
panels rise *above* a darker canvas like raised cards on a table.

**Grove inverts that.** Panels sit *below* the canvas tier; they are
**inset wells**.

| TCSS slot | Role | Dark hex | Light hex | Where it shows |
|---|---|---|---|---|
| `$background` | Ambient canvas (middle tier) | `#2d2d2b` | `#faf9f5` | `Screen` root, `ContextualFooter`, anywhere chrome bars touch the user's terminal bg |
| `$surface` | Panel-well tier (deepest) | `#0e0e0d` | `#a89f86` | Every `.grove-card` body — `WorkspaceList`, peek-rail summary card, peek-rail Live Workspace Preview card, modals (`.grove-dialog`), the empty-state banner |
| `$panel` | Highlight-lift tier (lightest) | `#363633` | `#ffffff` | `WorkspaceList:focus > WorkspaceCard.-highlight` only — the focused row |

Both polarities follow **the same axis**: `surface` is the deepest,
`background` is mid, `panel` is the lightest. The rotation is a 1:1
mental map across themes — moving dark→light only flips the absolute
luminances; the *relative* tiering is identical.

**Why this shape**

- A `tmux capture-pane` snapshot carries the agent terminal's own dark
  background in its SGR cells. The peek-rail strips those bg codes
  before rendering (`_strip_pane_bgcolors` in `peek_rail.py`) so the
  card's `$surface` shows through cleanly — fg/style attributes
  (color, bold, italic, underline) are preserved.
- The user's eye locks onto canvas as the ambient layer; panels read
  as inset content; the focused row is the one element that lifts
  above canvas. This is the lazygit affordance applied to a list.
- Contrast between `$background` and `$surface` is `~9:1` WCAG ratio
  (dark) / `~2.6:1` (light). Earlier passes used ~3 L* and ~8 L*
  deltas — both read as "nothing changed" on real-world displays, so
  the panel must sit near-black (dark) or in a deeper tan (light) to
  survive bit-depth quantization on cheap panels.

**Do not** restore the Material assumption that `$panel > $background >
$surface`. Tests pin the ordering; reordering will break visual
regression assertions.

> **Side note on `$boost`.** Textual exposes `$boost` as a fourth slot
> intended to be a colored alpha layer. Inspecting `ColorSystem.generate()`
> shows `$boost` is *always* transparent regardless of the value passed
> to `Theme(...)`. We don't reach for it as a fourth tier.

### 3.3 Color system

Grove's color tokens live in three families. Every family has a
dark/light pair and is exposed to both TCSS (via `$varname`) and Rich
(via accessor functions that take `dark: bool`).

#### Brand & state — Textual base slots

| Slot | Role | Dark | Light |
|---|---|---|---|
| `$primary` | Brand clay — focus, key hints, active border | `#d97757` | `#d97757` |
| `$secondary` | Inactive border, footer key separator | `#96938c` | `#858278` |
| `$accent` | Mirrors `$primary`; used by Textual button-primary | `#d97757` | `#d97757` |
| `$foreground` | Default text | `#fcfbf9` | `#0a0a0a` |
| `$background` | Canvas tier (see [§3.2](#32-tier-model--inset-wells-on-an-ambient-canvas)) | `#2d2d2b` | `#faf9f5` |
| `$surface` | Panel-well tier | `#0e0e0d` | `#a89f86` |
| `$panel` | Highlight-lift tier | `#363633` | `#ffffff` |
| `$success` | Generic OK | `#4d9900` | `#4a9331` |
| `$warning` | Generic attention (NOT errors) | `#b8860b` | `#926b00` |
| `$error` | Destructive / broken | `#e64c4c` | `#c03a3a` |

#### Status — workspace lifecycle

Two domains in one enum. Persisted intents (`RUNNING`, `PAUSED`,
`ERROR`) are written to disk; computed views (`ACTIVE`, `IDLE`,
`OFFLINE`, `ORPHANED`) are reconciled at read time and never persisted.
The reconciler (`WorkspaceManager._reconcile_status`) is the **single**
policy site that promotes a persisted intent to a displayed status.

| Status | Meaning | Glyph | Dark hex | Light hex | TCSS var |
|---|---|---|---|---|---|
| `ACTIVE` | Live signal — agent producing output (window activity within `cfg.tmux.activity_threshold_seconds`) | `●` | `#84cc16` (vibrant lime) | `#65a30d` (lime-600) | `$status-active` |
| `IDLE` | Alive but quiet — pane present, no recent output | `◐` | `#c2dcf7` (info cyan) | `#2f6cd9` | `$status-idle` |
| `OFFLINE` | tmux session vanished externally; worktree intact | `○` | `#96938c` (muted gray) | `#858278` | `$status-offline` |
| `PAUSED` | Deliberate teardown — worktree removed; branch retained | `‖` | `#96938c` | `#858278` | `$status-paused` |
| `ORPHANED` | Worktree directory gone — needs cleanup | `⊘` | `#b8860b` (amber) | `#926b00` | `$status-orphaned` |
| `ERROR` | Broken — last lifecycle op failed | `✗` | `#e64c4c` (destructive red) | `#a83232` | `$status-error` |
| `RUNNING` | Persisted intent (rarely seen post-reconciliation) | `●` | `#84cc16` | `#65a30d` | `$status-running` |

**ACTIVE is decoupled from `$success`.** The Textual `$success` slot
keeps its olive-green (`#4d9900` dark / `#4a9331` light) for buttons and
generic OK signals; ACTIVE took over a brighter lime (`#84cc16` /
`#65a30d`) so the leading status glyph reads at a glance against the
inset-well `$surface` background. The earlier alias produced ~3.5:1
contrast on dark cards — visible in isolation, dim in the row scan.
Lime gives ~10:1 against `$surface` (#0e0e0d) without crossing into
neon territory; the warm-clay theme tolerates a saturated lime because
lime+terracotta is a well-known complementary pair.

**Two non-obvious choices, pinned by tests:**

- `PAUSED` and `OFFLINE` share **muted gray**. Both mean "no live
  signal"; the *glyph* (pause-bars vs. empty-circle) is what
  distinguishes intent from accident. Don't repaint `PAUSED` amber —
  pause is deliberate, not a warning.
- `PAUSED` (gray, deliberate) and `STALE` legacy hue (amber, passive
  warning) must remain visually distinct in **both** modes. Pinned by
  `tests/tui/test_theme.py::test_paused_and_stale_have_distinct_colors_in_both_modes`.

**ACTIVE pulses; no other status does.** A screen-level clock at 4 Hz
walks a two-frame cycle so the live-signal glyph reads as a heartbeat
rather than a static dot. Frame 0 is the resting state (canonical
`●` + base active green); frame 1 is the swelled state (`◉` + a
mint-tinted hex). Glyph and color move in lockstep — the design rule
"glyph and label share the status color" means the line-2 `active`
label pulses with the line-1 glyph, reinforcing the cue. Other
statuses are intentionally NOT animated: pulsing IDLE would
contradict "alive but quiet", pulsing PAUSED would contradict
"deliberate teardown", and so on. Pinned by
`tests/tui/test_card_render.py::test_render_card_active_swells_glyph_and_color_with_pulse_frame`
and `…::test_render_card_non_active_ignores_pulse_frame`.

| Pulse frame | Glyph | Dark hex | Light hex | Source |
|---|---|---|---|---|
| 0 (rest) | `●` | `#84cc16` (lime-500) | `#65a30d` (lime-600) | `STATUS_HEX[dark][ACTIVE]` (single source of truth — same as the static lookup) |
| 1 (swell) | `◉` | `#bef264` (lime-300) | `#84cc16` (lime-500) | `ACTIVE_PULSE_TINT_HEX[dark]` |

Consumers reach both frames via the same accessor:

```python
from grove.tui._status import active_pulse
glyph, hex_ = active_pulse(frame, dark=dark)  # frame wraps modulo 2
```

Surfaces that pulse: each `WorkspaceCard` whose status is ACTIVE, and
the `StatusBar` selection-summary's status glyph + label when the
focused row is ACTIVE. Count chips (`● 3`) deliberately do **not**
pulse — counts read as steady reference data, and animating them
suggests the count itself is changing.

#### Ref accents — git refs and diff colors

| `RefKind` literal | Role | Dark hex | Light hex | TCSS var |
|---|---|---|---|---|
| `branch` | Branch names, ahead-counter title, "recent" header | `#26a69a` (teal) | `#1f8c7e` | `$ref` |
| `diff_add` | `+N` adds, `ahead` polarity hue, success flash | `#99d199` | `#3d7a00` | `$ref-add` |
| `diff_remove` | `-N` removes, error flash | `#e66666` | `#a83232` | `$ref-remove` |
| `info` | Agent name (cyan) — reused for "info" semantic slot | `#c2dcf7` | `#2f6cd9` | `$info` |

**Why two distinct hues for branch and agent.** The card renders
both — branch (teal) and agent (cyan) — bold. Two distinct slots for
two distinct facts let the eye separate "what" (branch) from "who"
(agent) without re-reading the labels. If a future "tooling label"
(e.g. lint status) lands, it gets a *third* ref slot; do not
overload `info` for two semantic surfaces.

#### Init-status — script outcome on workspace creation

| `InitStatus` | Meaning | Color slot |
|---|---|---|
| `OK` | Init script ran successfully | `ref_color('diff_add')` (text-only currently — slot exposed for future use) |
| `FAILED` | Init script failed; `init_log_path` is set | `init_status_color(FAILED)` — bold red |
| `SKIPPED` | Init disabled or no-op | muted |

The cards display only `! init failed` (line 2 trailing badge) and the
peek rail's summary card displays `✗ init failed` plus `log: <path>`.
OK / SKIPPED are not currently rendered with color — text-only — but
the slot is exposed in `INIT_STATUS_HEX` so a future renderer is a
one-liner.

#### Chrome — footer / status-bar accents

`ChromeKind = Literal["accent", "muted"]`. Used by the contextual
footer to color keys (clay accent) and separators / muted text
(`chrome_color('muted')`). Same dark/light shape as every other
Rich-side dict.

**Never use Rich `dim` for muted text.** Terminal interpretation drifts
across emulators (gnome-terminal, alacritty, kitty, iTerm2 all behave
differently) and breaks dark/light theme parity. Always reach for
`chrome_color('muted', dark=...)`.

### 3.4 Typography

Grove has **three typographic tiers per content surface**. A card body,
a peek-rail line, a footer entry — all of them obey the same rule:

| Tier | Style | Used for |
|---|---|---|
| **Tier 1** | `bold` + semantic color | Values the eye lands on first: status glyph, status label, branch name, agent name, diff `+N`/`-N`, ahead-counter, commit SHAs, footer key letters, affordance keys (`R`, `o`, `k` in inline prompts) |
| **Tier 2** | `bold` + default fg | Neutral bold counters: title (also `underline` for identity affordance), ahead/behind/dirty *values* when zero, generic flash messages |
| **Tier 3** | `chrome_color('muted')` (no bold) | Labels, connectives, age, commit timestamps, `·` separators, `│` group dividers, `log:` prefixes, "press X to Y" copy |

**Three reinforcement rules:**

- **Glyph and label share the status color.** A row's `●` and its
  `active` label both render in `status_color(ACTIVE)`. The same color
  reads twice — once on line 1, once on line 2 — reinforcing the
  lifecycle cue.
- **Title gets `bold underline`.** Underlines mark the row's identity
  the same way they mark hyperlinks in IDE file lists; this trains
  the user's "this is the thing" reflex. Never used elsewhere.
- **Polarity-aware stats: zero = muted, nonzero = semantic.** The
  three counters on the peek rail (`ahead`, `behind`, `dirty`) render
  their *label and value* in the same hue:
  - All zero → muted gray (no signal).
  - Nonzero → green for `ahead` (work to push), amber for `behind` and
    `dirty` (work to pull / clean). Same amber as `ORPHANED` —
    "needs attention", not "broken". Implementation:
    `_stat(label, value, active_hex)` in `peek_rail.py` so the three
    call sites cannot drift.

#### What gets `bold underline` — invariant

Exactly one slot per surface: the **identity** of the surface.

- `WorkspaceCard` line 1 — the workspace title.
- `StatusBar` filter chip — the active filter query.

Adding a third use of `bold underline` requires a design review. If
two things on the same screen are underlined, neither reads as the
identity any more.

### 3.5 Spacing & layout

Spacing is measured in **terminal cells**, not pixels. The codebase
uses three units:

| Unit | Pattern | Where |
|---|---|---|
| **0** | Edge-touching | Card body line breaks, default `margin` everywhere |
| **1** | One-cell breathing | `padding: 0 1` inside every `.grove-card`; left-column `padding-left: 1`; PeekRail outer `padding: 0 1`; modal button `margin: 0 1` |
| **2** | Two-cell breathing | `ContextualFooter padding: 0 2`; modal `padding: 1 2` |

Vertical rhythm:

| Concern | Rule |
|---|---|
| Card height | **Fixed at 4 rows** (1 border-top + 2 content + 1 border-bottom). Highlighting never reflows the layout — only borders swap. |
| StatusBar height | `1` cell, `dock: bottom`. **Never** add `border-top: solid` — see [§3.6](#36-borders--focus-chrome). |
| ContextualFooter height | `1` cell, `dock: bottom`, no border. |
| FilterBar height | `3` cells (border-top + Input + border-bottom), `display: none` until activated; `margin: 0 1` to align with the left-column padding. |
| Modal width | `width: 70` for ConfirmScreen / HelpScreen; `width: 80` for CreateWorkspaceScreen. `height: auto`. |
| Section gap inside modals | `margin-top: 1` on `.grove-dialog-section` and field labels |

**Stay layout-stable.** Every state change that swaps a visual cue
must keep the bounding box constant. Highlighting a card swaps two
border colors — it does not change the card's height or width. The
StatusBar swaps its background and foreground when entering
`-attention` — it does not change height. This is the rule that keeps
hovering through 30 rows from inducing visible reflow.

### 3.6 Borders & focus chrome

Every visible boundary in Grove is either:

1. A **`round` border** in a color drawn from `$secondary` (inactive)
   or `$primary` (active / focused).
2. A **transparent border** (`border: round $surface` against a
   `$surface` parent bg) used as a layout placeholder for
   highlight-state borders that *will* render. This keeps cards the
   same height regardless of focus.
3. A **`tall` border** (`border: tall $primary`) on modals
   (`.grove-dialog`) — the heavier weight is intentional; modals are
   modal.

**Focus chrome rules:**

- A focused panel takes a `round $primary` border. Defocusing returns
  it to `round $secondary`. Pinned by:
  - `WorkspaceList { border: round $secondary; } WorkspaceList:focus { border: round $primary; }`
  - `PeekRail #card-pane.-live { border: round $primary; }` (the
    "active" cue here is "live agent output", not "keyboard focus" —
    the rail is not focusable)
- A highlighted row inside the focused list takes a *full* `round
  $primary` border *and* swaps its bg to `$panel`. The bg swap is the
  a11y backstop — focus must not be carried by color alone.
  ```css
  WorkspaceList:focus > WorkspaceCard.-highlight {
      background: $panel;
      border: round $primary;
  }
  ```
- A defocused list never highlights any row in clay. The selector
  `WorkspaceList:focus > WorkspaceCard.-highlight` short-circuits
  when the list isn't focused, so while the FilterBar input has
  focus, no card pretends to be the active selection.

**Pitfall — `border-top: solid` + `height: 1`.** Textual lays this
out as `outer_size.height = 1` but `content_size.height = 0` because
the border consumes the only docked row. Symptom: the widget mounts,
queries resolve, the user sees blank chrome. The `region.height==1`
assertion does **not** catch it (region is the outer box). Pin
`widget.content_size.height` instead. Fix is one of: drop the border
(cleanest), set `height: 2`, or use `border-top: blank`. Same pitfall
applies to `border-bottom` on top-docked chrome.

This is why `ContextualFooter` and `StatusBar` have **no border** —
they're 1 cell tall and rely on the bg color (`$panel` for
ContextualFooter via the muted divider; `$primary` for StatusBar) to
provide separation against the inner `$surface` panels above them.

### 3.7 Glyphs

Grove's iconography is character-based — no Nerd Font or ligature
dependency. Icons are picked from Unicode blocks that render reliably
in terminal-supported fonts.

| Glyph | Unicode | Used as |
|---|---|---|
| `●` | U+25CF | Status: ACTIVE (filled — live signal) |
| `◐` | U+25D0 | Status: IDLE (half — alive but quiet) |
| `○` | U+25CB | Status: OFFLINE (empty — no live signal) |
| `‖` | U+2016 | Status: PAUSED (pause bars — deliberate) |
| `⊘` | U+2298 | Status: ORPHANED (circle-slash — stranded) |
| `✗` | U+2717 | Status: ERROR; init-failed badge |
| `⌂` | U+2302 | Repo identity (StatusBar left zone) |
| `⎇` | U+2387 | Branch (StatusBar selection summary) |
| `⌕` | U+2315 | Filter (StatusBar right zone) |
| `›` | U+203A | Selection prefix (StatusBar) |
| `│` | U+2502 | Group divider (StatusBar inner; ContextualFooter) |
| `·` | U+00B7 | In-group separator (footer; card body; rail stats) |
| `…` | U+2026 | Truncation suffix |

**Adding a glyph:** prefer single-char Unicode in the General
Punctuation, Geometric Shapes, or Miscellaneous Technical blocks.
Verify it renders in three reference terminals (alacritty, iTerm2,
Windows Terminal). Avoid emoji (variable width, sometimes color-only).
Pin the glyph as a module-level `Final = "..."` constant alongside its
peers — never inline a literal in a render function.

---

## 4. Tokens

### 4.1 Token tiers

Mainstream design systems organize tokens in three tiers
([Material 3](https://m3.material.io/foundations/design-tokens),
[Pajamas](https://design.gitlab.com/product-foundations/design-tokens/),
[Atlassian](https://atlassian.design/foundations/tokens/design-tokens/)).
Grove follows the same pattern with TUI-adapted naming:

| Tier | Mainstream name | Grove equivalent | Example |
|---|---|---|---|
| **Reference** | "primitive" / "atomic" | `_DARK_*` / `_LIGHT_*` module-level `Final` constants in `theme.py` | `_DARK_PRIMARY: Final = "#d97757"` |
| **System** | "semantic" / "alias" | TCSS variables (`$primary`, `$status-active`, `$ref`) and Rich-side dicts (`STATUS_HEX`, `REF_HEX`) | `$status-active`, `STATUS_HEX[True][ACTIVE]` |
| **Component** | "component-specific" | TCSS rules referencing system tokens (`.grove-card { background: $surface; }`) | `WorkspaceList:focus > WorkspaceCard.-highlight { border: round $primary; }` |

**Rule:** widgets and TCSS only ever reach into tier 2 or 3. The tier-1
hex constants are private (`_DARK_*` underscore prefix) — they're the
**single source of truth** but they are **never** imported by widgets.
Adding a new color means adding the hex atom AND the system-tier
exposure in the same change.

### 4.2 Surface tokens

| Token | Dark | Light | Slot semantics |
|---|---|---|---|
| `$background` | `#2d2d2b` | `#faf9f5` | Canvas tier. Screen, ContextualFooter. |
| `$surface` | `#0e0e0d` | `#a89f86` | Panel-well tier. Every `.grove-card`, modal `.grove-dialog`. |
| `$panel` | `#363633` | `#ffffff` | Highlight-lift tier. Focused row only. |
| `$primary` | `#d97757` | `#d97757` | Brand clay. Focus borders, key hints, status-bar default bg. |
| `$secondary` | `#96938c` | `#858278` | Inactive borders, footer separators. Mirrors `chrome_color('muted')`. |
| `$foreground` | `#fcfbf9` | `#0a0a0a` | Default text. |

### 4.3 Status tokens

See [§3.3 Color system § Status](#33-color-system) for the full table.
Quick reference for the consumption pattern:

```python
from grove.tui._status import status_color, status_glyph, status_label
from grove.core import WorkspaceStatus

dark = self.app.current_theme.dark   # read once per render
hex_  = status_color(WorkspaceStatus.ACTIVE, dark=dark)  # "#4d9900"
glyph = status_glyph(WorkspaceStatus.ACTIVE)             # "●"
label = status_label(WorkspaceStatus.ACTIVE)             # "active"
```

In TCSS:

```css
WorkspaceCard .indicator {
    color: $status-active;
}
```

### 4.4 Ref / diff tokens

```python
from grove.tui._status import ref_color
ref_color("branch", dark=dark)      # teal
ref_color("diff_add", dark=dark)    # green
ref_color("diff_remove", dark=dark) # red
ref_color("info", dark=dark)        # cyan (used for agent name)
```

In TCSS: `$ref`, `$ref-add`, `$ref-remove`, `$info`.

### 4.5 Init-status tokens

```python
from grove.tui._status import init_status_color
from grove.core import InitStatus
init_status_color(InitStatus.FAILED, dark=dark)  # destructive red
```

### 4.6 Chrome tokens

```python
from grove.tui._status import chrome_color
chrome_color("accent", dark=dark)  # clay — footer key hint color
chrome_color("muted", dark=dark)   # gray — separators, labels, dividers
```

No TCSS exposure — chrome accessors are Rich-only because they're used
inside `Text(...)` and `Text.from_markup(f"[{hex}]...")` calls in
widgets that render via `Widget.render()` rather than CSS rules.

### 4.7 How to consume each token

| Where you're writing | How to reach the token |
|---|---|
| TCSS in `DEFAULT_CSS` (rule body) | `$varname`. Examples: `background: $surface;` `border: round $primary;` |
| Rich `Text` style string | Hex from accessor. `Text("...", style=f"bold {status_color(s, dark=dark)}")` |
| Rich markup string | Hex from accessor wrapped in markup tags. `f"[bold {ref_color('branch', dark=dark)}]{branch}[/]"` |
| New token addition | (1) Hex atom in `theme.py` `_DARK_*` / `_LIGHT_*`. (2) Add to relevant lookup dict (`STATUS_HEX`, etc.). (3) If TCSS needs it, add to `_DARK_VARS` / `_LIGHT_VARS`. (4) Extend the typed `Literal` (`RefKind`, `ChromeKind`) so callers get static checking. |

**Rule:** widgets never read `app.current_theme.dark` from inside a
pure rendering helper. The widget's `render()` reads `dark` once and
forwards. This keeps render helpers testable without a Pilot.

### 4.8 Agent-state tokens

A **separate axis** from workspace-status tokens (§4.3): status colors
what the *workspace* is (ACTIVE / IDLE / PAUSED), agent-state colors what
the *agent session inside it* is doing (WORKING / WAITING / …). The
Activity Dashboard shows both at once — a workspace can be ACTIVE while
its agent is WAITING — so they are deliberately distinct maps over
distinct enums. The canonical dark hex map is the cross-client contract
`grove.core.contracts.agent_palette.DARK_AGENT_STATE_HEX` (the web client
reads the same file); `grove.tui.theme.AGENT_STATE_HEX` sources its dark
half straight from it (`dict(DARK_AGENT_STATE_HEX)`), so the two cannot
drift by construction.

```python
from grove.tui._status import agent_state_color, agent_state_glyph, agent_state_label
from grove.core.agents import AgentActivityState

agent_state_color(AgentActivityState.WORKING, dark=dark)  # lime (matches ACTIVE)
agent_state_glyph(AgentActivityState.WORKING)             # "▶"
agent_state_label(AgentActivityState.WORKING)             # "working"
```

| State | Glyph | Hue | Meaning |
|---|---|---|---|
| STARTING | `◌` | info cyan | session launched, transcript not yet on disk |
| WORKING | `▶` | lime (= ACTIVE) | in the tool loop / mid-response (pulses) |
| WAITING | `◑` | warning amber | turn ended, wants the human (attention) |
| BLOCKED | `⚠` | warning amber | explicit permission prompt (attention; hook-sourced) |
| IDLE | `○` | muted gray | alive but quiet |
| ERROR | `✗` | destructive red | failed run / unreadable transcript |
| UNKNOWN | `·` | muted gray | suppressed / no signal |

Glyphs are picked from the same terminal-safe blocks as the status
glyphs (§3.7) and stay visually distinct from them — a glance separates
"the workspace is live" (`●`) from "the agent is working" (`▶`). No Nerd
Font dependency.

---

## 5. Layout

### 5.1 Screen anatomy — list screen

Grove's primary screen is the workspace list. Layout, top to bottom:

```
┌────────────────────────────────────────────────────────────────────┐
│ Header — Grove · <repo-name>                            (clay bg)  │ ← `$primary` bg
├────────────────────────────────────────────────────────────────────┤
│ FilterBar (hidden by default — `display: none`; `-active` = block) │
├──────────────────────────────────┬─────────────────────────────────┤
│ #left-col                        │ PeekRail                        │
│ ┌──────────────────────────────┐ │ ┌─────────────────────────────┐ │
│ │ WorkspaceList (`workspaces`) │ │ │ #card-workspace (`summary`) │ │
│ │ ┌──────────────────────────┐ │ │ │  $surface bg                │ │
│ │ │ WorkspaceCard (4 rows)   │ │ │ │  $secondary border          │ │
│ │ │   line 1: ● title  · age │ │ │ └─────────────────────────────┘ │
│ │ │   line 2: branch · agent │ │ │ ┌─────────────────────────────┐ │
│ │ │           · status       │ │ │ │ #card-pane                  │ │
│ │ │                          │ │ │ │  (Live Workspace Preview)   │ │
│ │ └──────────────────────────┘ │ │ │  -live = $primary border    │ │
│ │ ...                          │ │ │  -hidden when not RUNNING   │ │
│ └──────────────────────────────┘ │ └─────────────────────────────┘ │
│   ↑ $surface bg, $secondary      │   ↑ same .grove-card chrome     │
│     border, becomes $primary     │                                 │
│     border on :focus             │                                 │
├──────────────────────────────────┴─────────────────────────────────┤
│ StatusBar — full-width bg ($primary clay default;                  │
│             $warning amber on -attention; $panel neutral on -empty)│
├────────────────────────────────────────────────────────────────────┤
│ ContextualFooter — globals │ selection-keys (muted dim if disabled)│
└────────────────────────────────────────────────────────────────────┘
```

**Width allocation.** Horizontal split is governed by:

- `WorkspaceList` — fluid (`width: 1fr` implicit), shrinks first.
- `PeekRail` — `width: 60%; min-width: 36`.
- `#left-col` — `padding-left: 1` so `WorkspaceList` reads as an inset
  panel on canvas, mirroring PeekRail's outer `padding: 0 1`. Without
  this, the left column fills edge-to-edge while the rail shows canvas
  around its cards — and the screen reads as two unrelated visual
  languages.

**Empty state.** When the manager returns zero workspaces, the screen
adds class `-empty` and:

- `WorkspaceList` is hidden (`display: none`).
- `#empty-wrap` becomes a centered area showing one
  `Static#empty-banner` with class `grove-card`. The empty banner is
  an inset `$surface`-bg card, italic, muted, centered: "no workspaces
  yet — press **n** to create one".

### 5.2 Modal anatomy

All modal screens extend `GroveModal[T]` and yield a `Vertical(classes="grove-dialog")`:

```
┌─ <ModalScreen, dim backdrop rgba(0,0,0,0.55), align center middle> ─┐
│                                                                     │
│       ┌── .grove-dialog (border: tall $primary, $surface bg) ──┐    │
│       │ Title (bold, margin-bottom: 1)                         │    │
│       │                                                        │    │
│       │ <body content per modal>                               │    │
│       │                                                        │    │
│       │   .grove-dialog-section (margin-top: 1)                │    │
│       │   .grove-detail (color: $text-muted)                   │    │
│       │                                                        │    │
│       │ .grove-dialog-buttons (height: 3, align: right middle) │    │
│       └────────────────────────────────────────────────────────┘    │
│  ContextualFooter (single group — no `│` divider)                   │
└─────────────────────────────────────────────────────────────────────┘
```

The class `grove-dialog` is the **seam** that picks up centered +
bordered chrome. Forgetting it means the modal renders unstyled —
tests querying `.grove-dialog` catch this loudly.

Modal widths: 70 columns (Confirm, Help), 80 columns (Create — wider
because it has form inputs).

### 5.3 Width tiers

Two width-responsive layers:

**Screen-level — `NARROW_THRESHOLD = 100`.** When the screen is
narrower than 100 columns, the screen adds class `-narrow` which:

- Hides `PeekRail` (`display: none`).
- Lets `WorkspaceList` take `width: 1fr`.

**StatusBar-level — three tiers** (column counts after subtracting
the `padding: 0 1` gutters):

| Tier | Threshold | Drops |
|---|---|---|
| `wide` | `>= 110` | (nothing — full bar visible) |
| `medium` | `>= 80` | Theme indicator (`dark` / `light` text on right) |
| `narrow` | `< 80` | Selection summary AND filter chip — only brand chip + count chips remain |

Brand identity (repo name) and count chips never drop. The bar always
fits its current width because each segment is truncated at its own
budget (`_REPO_TRIM=24`, `_TITLE_TRIM=28`, `_BRANCH_TRIM=32`,
`_FILTER_TRIM=20`).

---

## 6. Components

Each component below documents: anatomy, states, tokens consumed, and
when-to-use rules.

### 6.1 Header

Textual's stock `Header` widget. Application-styled in `GroveApp.CSS`:

```css
Header { background: $primary; color: $foreground; }
```

Identity: title (`Grove`) + subtitle (repo name). No clock. The bar is
the brand assertion of the screen — the only place we paint a full row
in `$primary` other than the StatusBar.

### 6.2 FilterBar

A `textual.widgets.Input` subclass that narrows the workspace list by
substring of `title`/`branch`/`agent_name`.

| State | Class | CSS |
|---|---|---|
| Hidden (default) | (none) | `display: none` |
| Active | `-active` | `display: block; height: 3; margin: 0 1` |

**Rules:**

- `/` key on the list screen activates and focuses the bar.
- `Esc` on the bar clears the input, removes `-active`, re-focuses the
  list.
- `Enter` on the bar keeps the filter, returns focus to the list.
- The bar is hidden but **mounted in the DOM from compose**, so global
  hotkeys (`r`, `n`, `k`, …) won't get eaten. The list screen
  explicitly calls `query_one(WorkspaceList).focus()` in `on_mount`
  so default focus lands on the list, not the hidden Input.

Placeholder copy: `filter — substring of title / branch / agent (esc to clear)`.

### 6.3 WorkspaceList

A `textual.widgets.ListView` rendering one `WorkspaceCard` per visible
state.

| State | Cue |
|---|---|
| Default | `$surface` bg, `round $secondary` border, title `workspaces` |
| Focused | Border swaps to `round $primary`; child highlighted card lifts to `$panel` bg with `round $primary` border |

**Public surface** (the test contract):

- `populate(states)` — cache and rebuild visible cards under current filter.
- `set_filter(query)` — case-insensitive substring filter.
- `filter_query` / `selected_id` / `states` / `visible_states` —
  introspection.
- `jump_to(index)` — 0-based cursor jump.

**Why ListView, not a hand-rolled VerticalScroll.** ListView extends
VerticalScroll with cursor management, `Highlighted`/`Selected` events,
and TCSS hooks for the `-highlight` class. Reimplementing all of that
would be 100+ lines of churn.

### 6.4 WorkspaceCard

One row, two lines, fixed `height: 4` (1 border + 2 content + 1 border).

```
╭──────────────────────────────────────────────────────────────────╮
│ ● my-feature-task                                · 3 minutes ago │   ← line 1
│ grove/feat-x  · claude  · active                                 │   ← line 2
╰──────────────────────────────────────────────────────────────────╯
↑ border: round $surface (transparent vs list bg) by default
  WorkspaceCard:hover                              → round $secondary (gray outline)
  WorkspaceList:focus > WorkspaceCard.-highlight   → round $primary + bg $panel
```

**Three-state border chrome (specificity climbs left → right):**

| State | Selector | Border | Bg |
|---|---|---|---|
| Default (no mouse, not selected) | `WorkspaceCard` | `round $surface` (transparent) | `$surface` (panel well) |
| Hover (mouse over, NOT keyboard-selected) | `WorkspaceCard:hover` | `round $secondary` (muted gray) | `$surface` |
| Selected (keyboard cursor or click in a focused list) | `WorkspaceList:focus > WorkspaceCard.-highlight` | `round $primary` (clay) | `$panel` (highlight tier) |

The selection rule out-specifies hover, so hovering the keyboard-selected card keeps its clay chrome rather than degrading to gray. Hover is required as an explicit rule because Textual's default `ListItem:hover` resolves to `$boost`, which is always transparent on Grove themes (see [§3.2](#32-tier-model--inset-wells-on-an-ambient-canvas) note on `$boost`).

**Line 1 anatomy:**

| Token | Style | Color |
|---|---|---|
| `●` glyph | bold | `status_color(state.status)` |
| Title (trimmed at 48 chars) | `bold underline` | default fg |
| `·` separator | (none) | `chrome_color('muted')` |
| Age (humanize, e.g. "3 minutes ago") | (none) | `chrome_color('muted')` |

**Line 2 anatomy:**

| Token | Style | Color |
|---|---|---|
| Branch | bold | `ref_color('branch')` (teal) |
| `·` separator | (none) | `chrome_color('muted')` |
| Agent name | bold | `ref_color('info')` (cyan) |
| `· ▶ working` agent-state glyph + label | bold | `agent_state_color(state)` (only when the screen's activity tick has resolved a session) |
| `·` separator | (none) | `chrome_color('muted')` |
| Status label (`active`/`idle`/`offline`/`paused`/`orphaned`/`error`) | bold | `status_color(state.status)` |
| `· root` | (none) | `chrome_color('muted')` (only if `placement == ROOT`) |
| `· ! init failed` | bold | `init_status_color(FAILED)` (only if `init_status == FAILED`) |

The agent-state segment is the *agent axis* (what the session is doing: `starting`/`working`/`waiting`/`blocked`/`idle`/`error` — see [§4.8](#48-agent-state-tokens)), a separate dimension from the workspace lifecycle status that follows it. It sits right after the agent name so "who · what they're doing" reads as one chunk, in the same bold-plus-semantic-color tier as the status label. Absence is the default (same convention as the `root` tag): a sessionless workspace — or one the slow activity tick hasn't covered yet — renders a byte-identical line 2 to the pre-agent card.

The `root` tag is a quiet qualifier, not a status token: muted and lowercase, it tells the user this workspace runs in the repo root with no isolated worktree. Worktree workspaces render nothing here, so the absence is the default. It sits after the status label and before any init-failed badge, so the badge stays the rightmost (most urgent) element on the row.

**Renderer purity.** `_render_card` is identical for highlighted and
unhighlighted cards. Adding a `focused: bool` parameter would
re-introduce a dual source of truth (CSS + body) and force a repaint
per cursor move. Focus is **only** in CSS; the renderer doesn't know.

### 6.5 PeekRail

Right-side rail. Two stacked Static cards inside a `Vertical` with
`padding: 0 1`.

- `#card-workspace` (title `summary`) — the summary card. Border stays
  `$secondary`. Carries: live diff stats, init-failure badge,
  paused / offline / orphaned affordance lines, recent commits.
- `#card-pane` (title `Live Workspace Preview`) — the live tmux pane
  mirror. Title is the user-facing role-noun (was `agent`, but the
  captured window may host any process — shell, htop, lazygit, an LLM
  agent — not strictly an LLM). Border swaps to `$primary` when the
  card has `-live` class (workspace is RUNNING / ACTIVE / IDLE — the
  `LIVE_STATUSES` set). Hidden via `-hidden` class when the workspace
  is not running. Captured SGR background codes from `tmux capture-pane`
  are stripped before render (`_strip_pane_bgcolors`) so the card's
  `$surface` shows through; fg/style attributes are preserved.

**Why two cards, not one.** Two cards have aligned paint cadences:

- Fast pane tick (~4 Hz, `cfg.peek_pane_refresh_seconds = 0.25 s`) —
  `peek_pane()` only, splices fresh tmux snapshot into cached peek,
  repaints **only** `#card-pane`.
- Selection-driven debounce (~80 ms) — full `peek()`, repaints both.
- Slow stats tick (`cfg.peek_stats_refresh_seconds = 3 s`) — full
  `peek()` (git ahead/behind/diff/dirty), repaints both.

All three are frozen on modal (`if self.app.screen is not self: return`).

**Summary card content (in order, conditional):**

1. **Stats line** — `+N / -M  ·  ahead K  ·  behind L  ·  dirty P`.
   Polarity-aware (zero = muted, nonzero = semantic, see [§3.4](#34-typography)).
2. **Agent metrics line** — `model  ·  12t/34r/87⚒  ·  412.0k↑ 38.0k↓  ·  working`.
   Only when the list screen's activity tick has a primary session for the
   selected row (`set_peek(peek, agent=...)`); skipped entirely otherwise.
   Sits directly under the stats line so the pair reads as one status
   block: git facts, then session facts. Model takes the agent hue
   (`ref_color('info')`, the same cyan as the row card's agent name);
   turns/replies/tools are bold default-fg counters; tokens are humanized
   (the dashboard's `_human_tokens` formatter — one formatter, two
   surfaces) and muted; the state label takes `agent_state_color`
   ([§4.8](#48-agent-state-tokens)), mirroring the row card's segment.
3. **Description** — only if the workspace has one. Plain default-fg
   text, trimmed at 200 chars with an ellipsis. Skipped entirely when
   empty (no `(no description)` placeholder — visual noise on every
   workspace). Lives on the rail (not the row card) because the row
   card's fixed `height: 4` is glance-scan affordance, while the rail
   is the read-deeply affordance — and a free-form note belongs in the
   read-deeply zone. Markup characters in user input are rendered as
   literals (we use `Text.append`, not `Text.from_markup`).
4. **Init failure** — only if `init_status == FAILED`. Two lines:
   `✗ init failed` (bold red) and `log: <path>` (muted).
5. **Affordance line** — exactly one of:
   - `‖ paused  press R to resume` (paused color = gray, bold key).
   - `○ offline  press o to respawn` (offline color = gray, bold key).
   - `⊘ worktree missing on disk  press k to clean up` (orphaned amber).
   - `error: <error_detail>` (when ERROR + has detail).
6. **Recent commits** — `recent` heading (teal, bold) and a list of
   `  <SHA[:8]>  <subject>  <age>`. Subject is trimmed at 56 chars.
   SHA + heading share the branch hue (teal) so the eye groups them as
   one column. Subject = default fg; age = muted.

**Pane card content.**

- `Text.from_ansi(snapshot)` of the tail (`_PANE_TAIL_LINES = 30`) —
  tmux's `capture-pane -e -p -J` produces SGR-only output.
- `text.no_wrap = True` — lines wider than the card crop locally
  rather than wrapping; the source tmux pane is **never** resized to
  fit (that would mutate a session the user might be attached to
  elsewhere).
- Empty capture → `[dim](no output)[/]` placeholder.

### 6.6 StatusBar

VS Code-style workbench bar. One full-width row at `dock: bottom`,
above ContextualFooter.

| State | Class | Background |
|---|---|---|
| Default | (none) | `$primary` (clay) |
| Attention | `-attention` (any ORPHANED or ERROR in fleet) | `$warning` (amber) |
| Empty | `-empty` (no workspaces) | `$panel` (neutral) |

The **whole-row bg** asserts the application's state (the analogue of
VS Code's blue/orange/purple). Segments are separated by **padding
alone** — no `·` between count chips. A muted vertical bar (`│`) splits
sub-groups *within* an alignment zone (e.g. count chips vs. selection
summary on the left).

**Layout zones (VS Code's two-zone idiom):**

| Zone | Content (in order, conditional) |
|---|---|
| Left | Brand chip (`⌂ <repo>` bold), count chips (one per non-zero status, glyph + bold count), `│` divider, selection summary OR flash |
| Right | Filter chip (`⌕ "<query>"` bold underline) when filter active, `dark` / `light` indicator (muted) — `wide` tier only |

**Selection summary** (when a row is selected, no flash active):

```
›  <title>  ⎇  <branch>  ●  active
   ↑ bold default fg
              ↑ branch in $ref-teal bold
                            ↑ status glyph + label, both in status hex, both bold
```

**Flash messages** — take over the selection-summary slot for 3 seconds
via a single shared timer. Three levels:

| Level | Color |
|---|---|
| `info` | bold default fg |
| `success` | `bold ref_color('diff_add')` (green) |
| `error` | `bold ref_color('diff_remove')` (red) |

Replacing an active flash cancels the prior timer so the new message
gets a full window. The 3-second cadence matches claude-squad's ErrBox.

**Width tiers** — see [§5.3](#53-width-tiers).

**Hex isolation.** Every color in the StatusBar flows from
`grove.tui.theme` via the `_status` accessors. No literal hex anywhere
in the module — adding a new chip means picking an existing semantic
slot, not introducing a new constant.

### 6.7 ContextualFooter

Single-line footer at `dock: bottom`. Replaces Textual's stock Footer
because we want **selection-only keys dimmed when nothing is selected**.

Format inside a group: `<bold $primary>key</bold> label · <bold $primary>key</bold> label …`
joined by ` · ` (muted `·`). Multiple groups joined by ` │ ` (muted
`│` — same idiom claude-squad uses to separate logical groups).

| Method | Use |
|---|---|
| `set_keys(keys)` | Single flat group — modal screens |
| `set_groups(groups)` | Multiple groups — list screen (globals + selection-keys) |

`set_keys` is a one-liner that calls `set_groups([keys])`. Empty groups
are dropped at render time so callers can pass conditional groups
without filtering at the call site.

**FooterKey shape:**

```python
@dataclass(frozen=True, slots=True)
class FooterKey:
    key: str         # "enter,a" — comma-separated alternatives shown joined by `/`
    label: str       # "Attach"
    available: bool  # True = bold accent + label; False = full-text dim
```

**Footer key gating** is data, not branches:
`_AVAILABLE_KEYS_BY_STATUS` in `screens/list.py` maps each status to
the set of keys that apply (e.g. `{ACTIVE → {enter,a, p, k}, PAUSED →
{R, k}, OFFLINE → {o, k}, ORPHANED → {k}, ERROR → {k}}`). One dict
lookup per render; adding a new status = one line.

**The key set varies by placement, too.** `_key_available(key, status,
placement)` first consults `_KEYS_REMOVED_BY_PLACEMENT` (`{ROOT → {p,
R}}`) and drops any key that placement strips, then falls through to
the status table. A root workspace reconciles to ACTIVE/IDLE/OFFLINE
like any other, but the engine refuses pause and resume for it (no
worktree to free or rebuild), so the footer dims `p` and `R` for a root
selection no matter its status. Still data, not branches: a new
placement constraint is one more `_KEYS_REMOVED_BY_PLACEMENT` entry, and
the empty-set default leaves WORKTREE workspaces untouched.

**On the list screen, the footer carries two groups:**

- Globals: `q · n · r · / · ?` — always available.
- Selection: `enter/a · p · R · o · k` — dimmed individually based on
  the selected row's status and placement (root drops `p` / `R`).

### 6.8 Modals — Confirm, Create, Edit, Help

All extend `GroveModal[T]` for centered + bordered + dimmed-backdrop
chrome (see [§5.2](#52-modal-anatomy)).

**ConfirmScreen** — generic yes/no with optional details block. Used
by `pause` (warns on dirty worktree) and `kill` (lists what gets
deleted; sets `danger=True` to switch the confirm button to error
variant).

| Key | Action |
|---|---|
| `y` / `enter` | Confirm |
| `n` / `escape` | Cancel |

**CreateWorkspaceScreen** — agent + title + a `RadioSet` branch picker
with **live preview** of the derived branch and tmux session names
(re-renders on every `Input.Changed` event). Preview uses
`ref_color('branch')` so the user sees the slug colored as a branch
name.

The `RadioSet` has five options, one per branch-source variant: Auto,
New, Existing, Remote, and **Root** (`work in the repo root (no
worktree, current branch)`). Each option mounts a hidden `_BranchBlock`
in `#branch-blocks`; selecting a radio reveals its block. The Root block
is read-only: a muted explanation plus the detected current branch. Its
preview names the current branch with an `(in place)` suffix and points
the worktree line at the repo root path, flagged as having no worktree.

Below the branch blocks sits a `Checkbox` (`#skip-init`, label `Skip
init script`). Default unchecked. Selecting the Root radio auto-checks
it (the init script is built for a fresh worktree and is risky in the
real repo root) as a one-way nudge: the user can still uncheck it, and
switching to another mode never forces it back off. Its value flows into
`CreateWorkspaceRequest.skip_init` at submit.

**Checkbox state visual (all modal checkboxes).** Checked vs unchecked
must read as **filled box vs empty box**, not as a color shift of an
always-present mark. Textual's stock `ToggleButton` renders its inner
glyph in every state and conveys on/off only by the glyph's color, which
on Grove's warm-dark palette left the off mark as a near-black `X` that
still reads as "ticked." `GroveModal` overrides the toggle button so the
**unchecked** state hides the mark (painted `$panel`, the pill's own
background, so the box looks empty) and the **checked** state fills the
whole pill with `$success` (a clearly filled box). One rule in
`GroveModal`, so every modal checkbox inherits it: `#skip-init` here and
`#delete-branch` in the kill-confirm modal.

| Key | Action |
|---|---|
| `escape` | Cancel |
| `ctrl+s` | Submit |
| `enter` (in any input) | Submit |

Default focus on `#title` input.

**EditWorkspaceScreen** — rename title + edit description. Two single-
line `Input` fields, both pre-filled with the current state. Pressing
`Enter` in either input or `Ctrl-S` submits; empty title bells without
dismissing (matches CreateWorkspaceScreen). Returns
`UpdateWorkspaceRequest | None` — `None` on cancel.

| Key | Action |
|---|---|
| `escape` | Cancel |
| `ctrl+s` | Save |
| `enter` (in any input) | Save |

The `e` key on the list screen opens this modal for the selected
workspace; available in every status except ORPHANED (matches the
engine's `ensure_can_update` rule).

**HelpScreen** — context-aware key list, generated from
`DEFAULT_BINDINGS` + the selection partition. Selection-only entries
dim when nothing is selected (`has_selection=False` flag passed in by
the caller).

| Key | Action |
|---|---|
| `escape` / `q` / `?` | Close |

### 6.9 Activity Dashboard — DashboardScreen, DashboardGrid, DashboardCard

The cross-project activity wall (`screens/dashboard.py`,
`widgets/dashboard_grid.py`). Where the list screen shows *one* repo's
workspaces, this shows *every* workspace across *every* repo as a wall of
agent-activity tiles grouped by project — "what is every agent doing
right now" at a glance. Opened from the list screen on `d`; `escape` /
`d` / `q` pop back. Data is the engine's `ActivityService` consumed
in-process (the same source the daemon serves over SSE).

**Screen anatomy.** `Header` (no clock) · `VerticalScroll #dashboard-body`
· `ContextualFooter`. The title is `Grove — Activity`, the subtitle shows
the active lens. The body holds, per project group: a `.project-header`
band (`$primary`, bold, `repo-name (N)`) then a `DashboardGrid`. When a
lens empties the wall, a centered muted `#dashboard-empty` message
replaces the grid and points at the key to widen.

**Lens (status filter).** Cycled with `l`: **all** (default — the whole
point is "see everything at once"; never open to an empty wall), **needs
attention** (sessions in WAITING / BLOCKED / ERROR — `needs_attention`),
**active** (any live/pending agent state). `g` toggles group-by-project
off into one flat "all workspaces" grid. A closed tuple drives the cycle.

**DashboardGrid.** A Textual `Grid` built to *fill* the terminal, not float a
small square wall in empty space. **Column count is width-driven:** one column
per `_MIN_TILE_WIDTH` (36) cells, capped at `_MAX_COLUMNS` (6) and at N; reflows
on resize. (This replaced `ceil(sqrt(N))`, which gave a 200-cell screen three
columns — the "too spaced out" bug.) **Row model:** a row track is
`_GRID_ROW_UNIT` (5) cells tall and `grid-rows: 5` matches it. A **compact** tile
spans one track (`is_promoted` False — idle / offline / starting / untracked) and
renders exactly three rows — an exact fit, no wasted space. A **promoted** tile
spans two tracks (working / waiting / blocked / error) and **fills the extra rows
with a live tmux pane tail** instead of leaving them blank — the whole point of
the redesign. `is_promoted(activity)` is the single promotion rule (grid span +
card shape + screen capture all read it, so they can't drift). Cards are created
eagerly in `compose()` (not a post-mount `mount_all`) to dodge the async-mount
race a caller hits when it mounts the grid and immediately queries it.

**DashboardCard** (one tile, a single `Static` via Rich `Text` — same
one-widget-per-tile discipline as `WorkspaceCard`; whole body is `no_wrap` so a
long line crops rather than stealing a row from the pane-fill math). Border:
`round $surface` (transparent), `round $primary` on `:focus`, `round $warning` on
`.-attention`. A root-placement workspace carries a quiet muted `root` tag after
the age (the metadata seam). Two shapes:

- **compact** (3 rows): glyph (state color, §4.8) · **bold-underlined** title · muted age — then branch (teal) · agent (cyan) · agent-state label — then `+X / -Y` numstat · `↑ahead ↓behind` · `Nt Nr N⚒` counts (muted).
- **promoted** (8 rows): glyph · title · **state label** · age · `root` — then branch · agent · model — then the agent's own one-line summary (`interpreted_status` first once the LLM interpreter (#20) fills it, else ai-title, else current task; the row is omitted, not blank-filled, when absent) — then the stat line plus `↑in ↓out` token usage — then a live, fit-to-cell `Text.from_ansi` pane tail (SGR backgrounds stripped like PeekRail, `no_wrap`) sized to the remaining rows, or a quiet `· · ·` placeholder until the screen captures it.

WORKING pulses the line-1 glyph (`▶` ↔ `▷`) on the screen's ~4 Hz
heartbeat — same one-screen-clock discipline as the list screen, color
held constant so the semantic is stable. Focus chrome is TCSS-only; the
renderer (`_render_card_body`) is pure (identical bytes regardless of
focus) and diff-guarded, so an idle wall costs ~zero repaints per tick.

**Tick cadence** (reuses the list screen's budget, frozen on modal): slow tick
(`cfg.peek_stats_refresh_seconds`, 3 s) refreshes **every promoted tile's** live
pane tail (bounded by `_MAX_LIVE_CAPTURES` per tick, focused tile first) then
drives `ActivityService.poll_once()` → `session_activity` deltas → re-render; fast
tick (`cfg.peek_pane_refresh_seconds`, 0.25 s) advances the pulse and re-captures
**only** the focused tile (the one the user is watching stays the most live).
Pane snapshots cache by workspace id so a wall rebuild re-applies them without a
blank flash. Lifecycle changes (create / kill / …) arrive promptly via the
service's bridged manager bus, no waiting for the next poll.

---

## 7. Patterns

Recurring solutions to recurring problems.

### Focus = brand-color border (lazygit pattern)

Every focusable container takes `border: round $secondary` by default
and `border: round $primary` on `:focus`. Used by `WorkspaceList`,
implicitly by Textual's `Input` (which has its own border-focused
behavior), and by the `-live` class on `PeekRail #card-pane` (where
"focus" means "live agent output", not keyboard focus).

### Highlight = inset row that lifts above its panel

Inside a focused list, the highlighted row swaps both bg and border:
`bg: $panel; border: round $primary`. The bg lift is the a11y backstop
— focus must not be color-only.

### Polarity-aware stat

```python
def _stat(label: str, value: int, active_hex: str) -> str:
    color = active_hex if value > 0 else muted_hex
    weight = "bold " if value > 0 else ""
    return f"[{color}]{label}[/] [{weight}{color}]{value}[/]"
```

Used by the peek rail for `ahead`, `behind`, `dirty`. Three call sites
share one helper so they cannot drift.

### Twice-read color

A row's status glyph and status label render in the same color. The
hue reads twice — once on line 1, once on line 2 — reinforcing the
lifecycle cue without adding a new visual element.

### Whole-row background asserts state (VS Code pattern)

`StatusBar` paints the entire row in `$primary` by default and swaps
the bg to `$warning` (`-attention`) or `$panel` (`-empty`) on state
class changes. No per-segment chip backgrounds — padding alone does
the work of separating segments. This is a direct port of VS Code's
`statusBar.background` shifts.

### Group divider in chrome rows

Within a chrome row that has multiple zones (StatusBar left zone;
ContextualFooter when `set_groups` has multiple groups), separate sub-
groups with a muted `│`. Within a group, separate items with a muted
`·`. Padding alone is sub-optimal here because zones can be visually
similar in width and the eye loses the boundary.

### Affordance line — colored key + muted prose

When the peek rail wants the user to press a specific key:

```
[paused-gray]‖ paused[/]  [muted]press[/] [bold paused-gray]R[/] [muted]to resume[/]
```

The key letter shares the affordance's hue (so it groups visually
with the status it acts on); the prose is muted (it doesn't compete
with the key). Same pattern for offline (`o`), orphaned (`k`).

### Flash auto-clear (claude-squad ErrBox pattern)

`StatusBar.flash(message, level)` writes a transient message to the
selection-summary slot and arms a 3-second timer. Replacing an active
flash cancels the prior timer (new message gets a full window).
Empty message clears immediately.

### Diff-guard before repaint

Every component that updates on a tick caches its last rendered plain
string and short-circuits when an identical successive frame would
be painted. `WorkspaceCard._refresh_body`, both peek-rail cards, and
`StatusBar` (via reactives' `always_update=True` discipline) all
follow this rule. An idle agent costs ~one capture-pane call per tick
and zero `Static` repaints.

### Pure render helpers + `dark` argument

Render helpers (`_render_card`, `_render_workspace`,
`_render_pane_body`, `StatusBar.render`) take `dark: bool` rather than
reading `self.app.current_theme.dark` themselves. Calling widgets read
`dark` once per `render()` and forward. This keeps helpers testable
without a Pilot.

---

## 8. Interaction model

### Default keybindings (list screen)

Defined in `src/grove/tui/keys.py` (`DEFAULT_BINDINGS`).

| Key | Action | Group | Available when |
|---|---|---|---|
| `q` | quit | global | always |
| `r` | refresh | global | always |
| `n` | new workspace | global | always |
| `/` | focus filter | global | always |
| `?` | help | global | always |
| `enter` / `a` | attach | selection | status ∈ {ACTIVE, IDLE, RUNNING} |
| `p` | pause | selection | status ∈ {ACTIVE, IDLE, RUNNING} |
| `R` | resume | selection | status = PAUSED |
| `o` | respawn | selection | status = OFFLINE |
| `k` | kill | selection | always (when row selected) |
| `1`-`9` | jump to row N | hidden | always — `show=False` keeps them out of the footer |

**Order in the footer**: attach (most common), pause/resume (lifecycle
pair), respawn (recovery for offline), kill (destructive — last).

### Focus chain

1. `compose()` yields `FilterBar` early (so its DOM position is fixed
   regardless of when it activates), but the bar is `display: none`
   by default.
2. `on_mount` explicitly calls `query_one(WorkspaceList).focus()` so
   default focus lands on the list, not on the hidden Input. Without
   this, every global hotkey (`r`, `n`, `k`, …) gets typed into the
   hidden filter and silently filters the table — symptom: rows
   vanish on `r`.

### Modal lifetime

While any modal is on top of the list screen:

- The slow stats tick and fast pane tick both check
  `if self.app.screen is not self: return` and skip. This keeps user
  typing in the create dialog snappy and avoids spurious git/tmux
  calls when the rail isn't visible.
- Modals receive their own footer via `ContextualFooter.set_keys`.

### Peek tick cadences (live data without burning IO)

| Cadence | Trigger | What it does |
|---|---|---|
| Selection-debounce | `~80 ms` after cursor move | Coalesces rapid j/k into one `peek()`. |
| Fast pane tick | `cfg.peek_pane_refresh_seconds` (default `0.25 s`) | `peek_pane()` only — one tmux `capture-pane` subprocess; splices snapshot into cached full peek; repaints `#card-pane` only. |
| Slow stats tick | `cfg.peek_stats_refresh_seconds` (default `3 s`) | Full `peek()` — git ahead/behind/diff/dirty + tmux. Repaints both cards. |

All three frozen on modal. The split is what keeps the rail "live"
without burning git IO at 4 Hz.

### Activity signal

`ACTIVE` vs `IDLE` is decided by `tmux #{window_activity}` (NOT
`#{pane_activity}` — which is empty on tmux ≤3.3). Threshold is
`cfg.tmux.activity_threshold_seconds` (default 5 s). Future timestamps
and non-numeric output coerce to `None` → reconciler treats unknown
age as IDLE (fallback, not signal).

### Flash messages

Triggered by:
- Lifecycle events from the manager (`created`, `paused`, `resumed`,
  `respawned`, `killed`) → success.
- `error` / `offline_detected` / `orphaned_detected` events → error.
- User-action errors (`pause failed: ...`) → error.
- Friendly nudges (`nothing selected`) → info.

3-second auto-clear via shared timer.

---

## 9. Theming & overrides

### Built-ins

`grove-dark` (default) and `grove-light` are registered automatically.
Selection via `cfg.ui.theme`:

| Setting | Resolves to |
|---|---|
| `auto` (default) | `grove-dark` |
| `dark` | `grove-dark` |
| `light` | `grove-light` |
| `<custom-name>` | Whatever TOML override registers `<custom-name>` |

### Writing an override

Drop a `*.toml` file into `${user_config_dir}/grove/themes/`. Shape:

```toml
name = "midnight-clay"
dark = true                       # required: gates which built-in
                                  # supplies inherited fields

[colors]                          # optional; any subset of slots
primary = "#e08060"
background = "#1a1a18"

[variables]                       # optional; merges over base variables
status-running = "#5fa800"
ref-add = "#8fc28f"
```

Schema (`ThemeOverride`, Pydantic, `extra='forbid'`):

| Field | Required | Default |
|---|---|---|
| `name` | yes | — |
| `dark` | yes | — |
| `colors.primary` | no | base `$primary` |
| `colors.secondary` | no | base `$secondary` |
| `colors.accent` | no | base `$accent` |
| `colors.foreground` / `.background` / `.surface` / `.panel` | no | base equivalent |
| `colors.success` / `.warning` / `.error` | no | base equivalent |
| `variables` | no | empty (merges over base) |

**Inheritance**: missing fields fall through to the matching-polarity
built-in. A one-line override that just changes `primary` is the
intended ergonomics.

**Validation**: malformed files raise `ConfigError` at app startup
with the file path in the message. Unknown top-level keys or unknown
`[colors]` slots are rejected (`extra='forbid'`).

### Adding a new token (codebase change)

When the design needs a token that doesn't exist:

1. Add hex atoms in `theme.py`: `_DARK_FOO` / `_LIGHT_FOO`.
2. If TCSS needs it: add to `_DARK_VARS` / `_LIGHT_VARS` (becomes
   `$foo`).
3. If Rich needs it: extend the relevant lookup dict (`STATUS_HEX`,
   `REF_HEX`, `CHROME_HEX`, …) and update the `Literal` type
   (`RefKind`, `ChromeKind`) so callers get static checking.
4. If it's a chrome accent (footer / status-bar surface), extend
   `CHROME_HEX[True]` / `CHROME_HEX[False]` plus the `ChromeKind`
   literal — do **not** introduce a parallel lookup module.
5. Add a test case to `tests/tui/test_theme.py` if the token has a
   semantic invariant (e.g. "must be visually distinct from X in
   both modes").

---

## 10. Glossary

The vocabulary used throughout this doc and the codebase.

**Affordance** — a UI cue that a specific action is available. In
Grove this is usually a colored key letter inline in the peek rail
(`press [bold]R[/] to resume`). The key shares the hue of the status
it acts on so the eye groups them.

**Agent** — the AI process running inside a workspace's tmux session
(Claude Code, Aider, Codex, plain shell, …). Each workspace has
exactly one agent, named by `cfg.agents[].name`.

**Ambient canvas** — the screen-root background tier (`$background`).
Panels sit *below* it as inset wells. See [§3.2](#32-tier-model--inset-wells-on-an-ambient-canvas).

**Card** — a bordered, `$surface`-bg surface with a left-aligned title
in `$primary`. Implemented as the `.grove-card` class on whichever
widget yields it. Three consumers today: PeekRail's two cards, the
empty-state banner, and the `WorkspaceList` container.

**Chrome** — non-content UI surfaces: borders, separators, status
bars, footers, modal frames. Distinct from "content" (titles, branch
names, agent output). Chrome accents are reached via
`chrome_color('accent')` / `chrome_color('muted')` so they track the
active theme rather than the terminal's own dim interpretation.

**Computed status** — a workspace status that is reconciled at read
time and never persisted: `ACTIVE`, `IDLE`, `OFFLINE`, `ORPHANED`. See
**Persisted status**.

**Diff-guard** — caching the last rendered plain text and
short-circuiting when an identical successive frame would be painted.
Every tick-driven component does this so an idle agent costs zero
Static repaints.

**Empty state** — the screen state when the manager returns zero
workspaces. `WorkspaceListScreen` adds class `-empty`; the list is
hidden and `#empty-banner` (a `grove-card` Static) becomes visible.

**Flash** — a transient message in the StatusBar's selection slot,
auto-cleared after 3 seconds. Three levels: `info` / `success` /
`error`.

**Grove-card** — the shared card chrome class
(`background: $surface; border: round $secondary; border-title-color:
$primary; border-title-align: left; padding: 0 1`). Every panel-shaped
content surface yields it.

**Grove-dialog** — the shared modal-content class
(`background: $surface; border: tall $primary; padding: 1 2; height:
auto; width: 70`). Every modal yields a `Vertical(classes="grove-dialog")`.

**Highlight-lift** — the lightest tier (`$panel`). Used **only** for
the focused row inside the focused list. See [§3.2](#32-tier-model--inset-wells-on-an-ambient-canvas).

**Inset well** — a panel that sits darker than its parent canvas. See
**Tier model**.

**Live status** — the set `{ACTIVE, IDLE, RUNNING}` (in
`grove.core.workspace.LIVE_STATUSES`). When the selected workspace's
status is in this set, the peek rail's `#card-pane` is shown with
the `-live` class.

**Pane** — a tmux window's single pane in Grove's layout. Each
workspace's tmux session has at most two windows (`shell`, `agent`),
each with one pane. The peek rail shows the *agent* pane.

**Panel-well** — the tier that holds content (`$surface`). Every
panel sits at this tier. See [§3.2](#32-tier-model--inset-wells-on-an-ambient-canvas).

**Peek** — a snapshot of a workspace's live state: status, branch
counts (ahead/behind), diff (added/removed), dirty files, agent
snapshot, recent commits. Returned by `WorkspaceManager.peek(id)`.
Peek is **best-effort by contract** — it never raises; helpers that
fail return zeros / empty rather than break the render loop.

**Persisted status** — a workspace status that lives on disk:
`RUNNING`, `PAUSED`, `ERROR`. The reconciler promotes it to a
**computed status** at read time.

**Polarity** — dark or light theme (`bool`). Every Rich-side lookup
table is keyed by polarity.

**Polarity-aware stat** — a counter that renders muted at zero and
promotes to a semantic hue when nonzero, with label and value sharing
the hue. Used for `ahead` / `behind` / `dirty` on the peek rail.

**Rail** — the right-side vertical column on the list screen. See
**PeekRail** (component).

**Reconciler** — `WorkspaceManager._reconcile_status`. The single
policy site that promotes persisted intents to displayed statuses
(e.g. `RUNNING` → `ACTIVE` / `IDLE` / `OFFLINE`).

**Ref** — a git-related accent (branch, diff_add, diff_remove, info).
See [§3.3 § Ref accents](#33-color-system).

**Slot** — a named position in the design system that maps to a
specific token. E.g. `$primary` is the "brand-clay" slot; whichever
hex it resolves to depends on the active theme.

**Status (workspace)** — the lifecycle state of one workspace. See
[§3.3 § Status](#33-color-system) for the full enumeration.

**Tier (typographic)** — one of three weights: bold-colored values,
bold-default counters, muted labels/connectives. See [§3.4 Typography](#34-typography).

**Tier (color)** — one of three depth levels: `$background` (canvas),
`$surface` (panel-well), `$panel` (highlight-lift). See [§3.2](#32-tier-model--inset-wells-on-an-ambient-canvas).

**Token** — a named design value: a color hex, a glyph, a spacing
unit. Tokens are defined once in `theme.py` and consumed everywhere
else through type-safe accessors.

**Twice-read color** — using the same color on two visual elements
that describe the same fact, so the cue reinforces itself. Status
glyph and status label both render in `status_color(state.status)`.

**Width tier** — one of `wide` / `medium` / `narrow`, picked by
StatusBar based on the rendered content width. Drops segments
progressively as the terminal narrows.

**Window (tmux)** — a tmux session can have multiple windows. Grove's
sessions have at most two: `shell` (for user commands) and the agent
window (named per `cfg.agents[].name`).

**Workspace** — Grove's atomic unit: one git worktree paired with one
tmux session running one agent. State = `WorkspaceState`. Live snapshot
= `WorkspacePeek`.

---

## 11. References & influences

External design systems and projects whose decisions shaped Grove's
visual language. Cited inline where relevant; collected here for
linkability.

**Token model**

- [Material Design 3 — Design tokens](https://m3.material.io/foundations/design-tokens) — three-tier
  taxonomy (reference / system / component) is the model Grove
  follows.
- [GitLab Pajamas — Design tokens](https://design.gitlab.com/product-foundations/design-tokens/) —
  semantic naming conventions.
- [Atlassian Design — Tokens](https://atlassian.design/foundations/tokens/design-tokens/) —
  same three-tier shape, with explicit polarity (light/dark) keying.
- [Naming Tokens in Design Systems](https://medium.com/eightshapes-llc/naming-tokens-in-design-systems-9e86c7444676) —
  Nathan Curtis on token naming taxonomy.

**Visual language**

- [VS Code Status Bar UX guidelines](https://code.visualstudio.com/api/ux-guidelines/status-bar) — the two-zone layout, restraint
  with color, and whole-row state-indicating bg are direct ports.
- [VS Code Theme Color reference](https://code.visualstudio.com/api/references/theme-color) — `statusBar.background`,
  `statusBar.debuggingBackground`, `statusBar.noFolderBackground`
  inspired the `default` / `-attention` / `-empty` state classes.
- [lazygit Config (theme)](https://github.com/jesseduffield/lazygit/blob/master/docs/Config.md) — `activeBorderColor` /
  `inactiveBorderColor` is the model for Grove's
  `border: round $primary` (focus) vs `round $secondary` (idle)
  pattern, and the per-row variant on `WorkspaceCard`.
- [k9s](https://k9scli.io/) — keyboard-first navigation, command-hint footer,
  list-of-resources screen shape.
- [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) — sibling tool; group
  divider (`│`) in footer, flash auto-clear cadence.

**Framework**

- [Textual Themes](https://textual.textualize.io/guide/design/) — slot semantics and the `dark: bool`
  shape of `Theme.variables`.
- [Textual CSS](https://textual.textualize.io/guide/CSS/) — TCSS rule reference.

**Palette**

- [bearlike/Assistant — `Assistant console`](https://github.com/bearlike/Assistant) (`the bearlike/Assistant brand palette`,
  `warm_terracotta.toml`) — every hex atom in `theme.py` originated
  here. Theme overrides follow this file's TOML shape.

---

## Appendix A — Quick reference card

For when you're inside a widget and need the right one-liner.

**Read polarity once per render:**
```python
dark = self.app.current_theme.dark
```

**Status color + glyph + label:**
```python
from grove.tui._status import status_color, status_glyph, status_label
hex_  = status_color(state.status, dark=dark)
glyph = status_glyph(state.status)
label = status_label(state.status)
```

**Ref / chrome / init colors:**
```python
from grove.tui._status import ref_color, chrome_color, init_status_color
ref_color("branch", dark=dark)            # teal
ref_color("diff_add", dark=dark)          # green
ref_color("diff_remove", dark=dark)       # red
ref_color("info", dark=dark)              # cyan (agent name)
chrome_color("accent", dark=dark)         # clay (footer key)
chrome_color("muted", dark=dark)          # gray (separators)
init_status_color(InitStatus.FAILED, dark=dark)
```

**TCSS — common patterns:**
```css
/* Card surface */
.my-thing {
    background: $surface;
    border: round $secondary;
    border-title-color: $primary;
    border-title-align: left;
    padding: 0 1;
}

/* Focus border swap */
.my-thing:focus { border: round $primary; }

/* Highlight lift */
.my-thing:focus > .my-row.-highlight {
    background: $panel;
    border: round $primary;
}

/* Chrome row — never put `border-top: solid` on a 1-cell-tall widget */
.my-bar {
    height: 1;
    dock: bottom;
    background: $primary;
    color: $foreground;
    padding: 0 1;
}
```

**New status** (4 lines):
1. Extend `WorkspaceStatus` enum.
2. Add to `STATUS_HEX[True]` and `STATUS_HEX[False]`.
3. Add to `STATUS_GLYPH` and `STATUS_LABEL` in `_status.py`.
4. Extend `_AVAILABLE_KEYS_BY_STATUS` in `screens/list.py`.
5. Extend `_reconcile_status` in `manager.py`.

**New theme variable** (3 lines):
1. Add hex atom to `theme.py` (`_DARK_FOO`, `_LIGHT_FOO`).
2. Add to `_DARK_VARS` and `_LIGHT_VARS` (becomes `$foo`).
3. Use `$foo` in TCSS or `chrome_color`/etc. accessor in Rich.
