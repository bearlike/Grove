---
version: "2.0"
name: Grove-webapp-design-system
description: "A calm agent-development-environment: a persistent left session rail, a centered composer-hero landing, and a three-zone session page (rail · assistant-ui transcript · tabbed work panel) — the mental model is 'work in a session, switch sessions in a rail', not 'monitor a fleet of cards'. Built on a DETUNED near-neutral 'space-black' canvas (#171613, hue ~45, barely warm) and a warm-neutral light theme (#faf9f5), with terracotta (#d97757) as the single chromatic accent, split into three AA-safe roles (brand mark / text / CTA fill). Every text pair is WCAG-computed and enforced by `tests/unit/contrast.test.ts` — AA 4.5:1 floor for all text (no large-text discount), AAA 7:1 for chat prose, 3:1 for non-text indicators. Display type is Geist Sans; lucide icons carry a single app-wide 1.75 stroke; a global `scrollbar-color` rule themes every native scroll surface. Persistent surfaces separate by tone and space — hairlines only where tone cannot, and transient overlays keep their chrome. Terracotta appears on the brand mark, the `you` speaker label, and the primary CTA fill — never decoratively. Grove's visual language still traces to Linear's marketing canvas; the operating philosophy is calm-first (see Design philosophy)."

colors:
  # Accent — terracotta (Grove brand). THREE roles: the brand mark is never a
  # text-bearing fill (white-on-#d97757 is only 3.12:1, fails AA).
  primary: "#d97757"          # brand mark ONLY — logo, dark-mode focus ring
  primary-foreground: "#ffffff"
  primary-fg: "#e08a6e"       # terracotta AS TEXT (links; intended for the `you` label too — RoleLabel ships bare `--primary` instead, see Contrast contract) — dark theme, 6.0:1
  primary-strong: "#b95230"   # CTA fill (send/create buttons) — white label 4.8:1 AA, both themes
  ring: "#d97757"             # dark-theme focus ring (5.9:1 on canvas)
  # Foreground (text)
  ink: "#f6f5ef"
  ink-muted: "#b0aca2"        # muted-foreground — 6.0:1 worst-case, incl. 12-13px meta
  # Canvas & surfaces — DETUNED near-neutral (hue ~45) "space black"
  canvas: "#171613"
  sidebar: "#1c1b17"          # chrome: header · rail · rail footer
  card: "#232320"
  elevated: "#2b2a26"
  muted: "#2f2e2a"
  hairline: "#38352f"         # decorative, WCAG-exempt — see Contrast contract
  code: "#e9e7df"
  code-well: "#201f1c"
  # Semantic (Python-mirrored, drift-tested — unchanged by the ADE detune)
  semantic-success: "#84cc16"
  semantic-warning: "#b8860b"
  semantic-destructive: "#e64c4c"
  semantic-info: "#c2dcf7"
  # Ref / git
  ref-branch: "#26a69a"
  ref-add: "#99d199"
  ref-remove: "#e66666"
  ref-info: "#c2dcf7"

# Light theme — warm-neutral off-white. Documented once here since
# `defaultTheme="system"` makes both themes equally primary; see the CSS
# Variable table below for the full pair.
colors-light:
  primary-fg: "#b4491f"       # 5.2:1
  primary-strong: "#b95230"
  ring: "#b95230"             # 4.6:1 on canvas
  ink: "#1b1a17"
  ink-muted: "#6b6559"
  canvas: "#faf9f5"
  sidebar: "#f3f1ea"
  card: "#ffffff"
  elevated: "#ffffff"
  muted: "#efece4"
  hairline: "#e6e2d9"
  code: "#26241f"
  code-well: "#f3f1ea"

typography:
  # The scale actually shipped in components (verified against globals.css +
  # every consumer — session rail, context bar, work panel, chat). A DORMANT
  # named scale (§4.3 tokens) also lives in globals.css's `@theme` block for
  # future phases; the chat transcript already consumes `--text-prose`.
  page-title:
    fontFamily: Geist Sans
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.4
    usage: Route/dialog titles, prominent stat values
  body:
    fontFamily: Geist Sans
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.43
    usage: Body copy, controls, buttons, form inputs
  chat-prose:
    fontFamily: Geist Sans
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.6
    usage: "Assistant + user transcript prose — the text-prose reading scale (--text-prose token)"
  dense-row:
    fontFamily: Geist Sans
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
    usage: Identity-popover mono values, ALL code (inline + fenced)
  meta:
    fontFamily: Geist Sans
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
    usage: Relative times, byline copy, secondary info
  section-label:
    fontFamily: Geist Sans
    fontSize: 11px
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: 0.08em
    usage: "Uppercase panel/section headers (SUMMARY, IDENTITY, TIMELINE) — the terminal-feel cue"
  micro-label:
    fontFamily: Geist Sans
    fontSize: 10px
    fontWeight: 500
    letterSpacing: 0.08em
    usage: Stat units, inline state labels beside a glyph
  mono:
    fontFamily: Geist Mono
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
    usage: Branches, SHAs, agent/model identifiers, fenced + inline code

rounded:
  # Derived from `--radius: 0.5rem` (8px) via the shadcn formula in globals.css
  # `@theme inline`. `rounded-2xl`/`rounded-3xl`/`rounded-full` are Tailwind's
  # own untouched defaults (the formula only remaps sm/md/lg/xl).
  sm: 4px      # rounded-sm  — calc(radius - 4px)
  md: 6px      # rounded-md  — calc(radius - 2px) — ALL buttons, badges, inputs
  lg: 8px      # rounded-lg  — = radius — cards, panels, dialogs
  xl: 12px     # rounded-xl  — calc(radius + 4px)
  xxl: 16px    # rounded-2xl — Tailwind default, unused today
  composer: 24px  # rounded-3xl — Tailwind default; the ONE surface that opts up: the create-composer hero card + the steer composer well
  pill: 9999px # rounded-full — status pills, toggle pills

spacing:
  # 4px grid. No shipped surface uses a marketing-page spacing scale — `/` IS
  # the composer-hero landing, there is no separate marketing page.
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px

components:
  button-primary:
    backgroundColor: "{colors.primary-strong}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: 8px 14px
  button-secondary:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: 8px 14px
    border: "1px {colors.hairline}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: 8px 14px
  workspace-card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.xl}"          # rounded-xl (12px), the calm card treatment
    border: "1px {colors.hairline}"  # border-border/60 in code
  status-badge:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  session-rail:
    backgroundColor: "{colors.sidebar}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"       # text-sm title line; meta line is 11px
    width: 280px                          # hides entirely (w-0) when collapsed — no icon strip
  header:
    backgroundColor: "{colors.sidebar}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    height: 52px
  composer-hero:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.composer}"
    padding: 12px
  identity-trigger:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"   # text-sm font-semibold (a heading role, not 16px)
    # The header's state-led title trigger (ContextBar) — opens the session
    # popover; no background of its own, rides the shared header slot.
  work-panel:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "0"
    border: "none"
  terminal-pane:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    fontFamily: "Geist Mono / JetBrains Mono Nerd"
    rounded: "0"
    border: "none"
  text-input:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    border: "1px {colors.hairline}"
    padding: 8px 12px
---

## Overview

Grove's webapp is an **agent development environment (ADE)**, not a dashboard you monitor from a distance. Every route shares ONE persistent shell: a 52px header and a 280px left **session rail** — Grove's "thread list," a single flat cross-project list sorted by recency (latest first), with per-row provenance (project · branch · ±changes) and attention signaled inline rather than a pinned group. The landing route (`/`) defaults to a centered **composer-hero** (a persisted `Hero | Overview` toggle keeps a rich card grid one click away for power users). The session route (`/w/[id]`) is a **three-zone layout**: the shared rail, an `@assistant-ui/react`-driven transcript column (~704px, centered), and a tabbed **work panel** (Terminal · Diff · Info) that can split side-by-side with the transcript or collapse to full-screen. The catalog route (`/sessions`) is the host-wide session archive — every session on the machine, grouped by project, each openable read-only.

The canvas is a **detuned near-neutral "space black"** — `{colors.canvas}` (`#171613`, hue ~45, barely warm). The blue cast of a cooler hue would fight the warm terracotta accent; the detune keeps the deep, dark-first identity while removing that discord. The light theme is a warm-neutral off-white (`#faf9f5`). `defaultTheme="system"` — following the OS is itself the familiar behavior.

The single chromatic accent is **terracotta**, split into **three AA-safe roles**: `{colors.primary}` is the brand mark ONLY (logo, dark-mode focus ring — never a text-bearing fill, since white-on-`#d97757` is only 3.12:1 and fails AA); `{colors.primary-fg}` is terracotta AS TEXT (links; the design intent for the `you` speaker label too, though `RoleLabel` currently ships bare `--primary` — a KNOWN, uncorrected component-class gap, see below); `{colors.primary-strong}` is the deepened CTA fill for send/create buttons (white label clears AA in both themes). Semantic colors (`--status-*`/`--agent-*`/`--ref-*`) are unchanged and still Python-mirrored, drift-tested, and used only for workspace/agent-state signals — never decoratively.

**Implementation:** All tokens map to Tailwind v4 CSS variables in `app/globals.css`. The Tailwind `@theme inline` block bridges these to utility classes (`bg-card`, `border-border`, `text-muted-foreground`, etc.). Never inline a raw hex — reach for the closest CSS variable first. **Every neutral + brand text pair is enforced by `tests/unit/contrast.test.ts`**, not eyeballed (see Contrast contract below) — a token edit that quietly drops below floor fails the build.

**Key characteristics:**
- **Rail-first IA.** A persistent, collapsible session rail on every route (it hides entirely when collapsed — modern-chat behavior, no icon strip) — the fleet is one glance away instead of a wall of cards.
- **Detuned space-black canvas** (`{colors.canvas}`) — deep and dark-first, but near-neutral rather than cool-blue.
- **Terracotta accent**, three roles (mark / text / CTA fill) — see above.
- Surface ladder (canvas → sidebar/chrome → card → elevated → muted) carries hierarchy without shadow.
- **1.75 lucide stroke, one global rule** — no per-instance overrides.
- **Global thin scrollbars** (`scrollbar-width: thin`) — no `ScrollArea` wrapper, no `::-webkit-scrollbar`.
- **No atmospheric gradients. No spotlight cards.** No drop shadows except floating overlays (Popover/Dialog/Sheet/Tooltip/Dropdown).

---

## Design philosophy — calm by default, focus-first

Grove's ADE is **calm technology** (after Weiser & Brown): the interface asks for attention only when it has something worth saying, and otherwise recedes. On the session page, the transcript is the **one focal surface**; the rail, header, and work panel exist to frame it, never to compete with it.

Four rules follow from that stance, and they govern every layout decision below:

1. **One focal surface.** Each screen has a single thing the user came to watch — on `/w/[id]` it's the transcript (or the terminal when the work panel is the active pane). Chrome frames it and gets out of the way.
2. **Signal-to-noise discipline.** Every persistent line, border, badge, and label spends attention, so it has to earn that cost by carrying state or structure needed *at rest*. Decoration that carries nothing is noise — remove it.
3. **Progressive disclosure.** Detail is opt-in, and each datum has exactly one named home: session switch/track lives in the rail rows, the full diff + commit list in the work panel's Diff tab, metrics/timeline in the Info tab, and git identity + lifecycle verbs one click behind the title in the session identity popover (Identity · Changes · Actions · Danger zone) — one hop away, not spread flat across the header. Nothing lost, everything paced.
4. **Density is a byproduct, not the goal.** A dense screen is what you get *after* removing chrome and deferring detail — never bought by shrinking type. The rail's calm single-line 32px rows and the transcript's 24px inter-message gap keep readability first; density is what's left when chrome is removed.

**Lineage vs. philosophy.** The *visual language* — the near-black palette, the surface ladder, the single accent — still traces to Linear's marketing canvas (see root `DESIGN.md`). The *operating philosophy* above is calm-first and is Grove's own; where the two pull apart, calm-first wins. Persistent surfaces separate by **tone and space, not lines** — transient overlays (Popover/Dialog/Dropdown/Sheet/Tooltip) are the deliberate exception, since a floating surface leaving the page plane needs an edge to read against whatever it covers.

---

## CSS Variable → Design Token Mapping

> Every design token maps to one of Grove's Tailwind v4 CSS variables, defined once in `app/globals.css`'s `:root` (light) and `.dark` blocks. This table is the implementation bridge — both themes are documented since `defaultTheme="system"` makes them equally primary.

| Design token | CSS variable | Dark value | Light value | Purpose |
|---|---|---|---|---|
| `{colors.canvas}` | `--background` | `#171613` (hsl 45 10% 8%) | `#faf9f5` (hsl 48 33% 97%) | Page canvas — deepest surface |
| `{colors.sidebar}` | `--sidebar` | `#1c1b17` (hsl 48 10% 10%) | `#f3f1ea` (hsl 47 27% 94%) | Persistent chrome: header, rail, rail footer |
| `{colors.card}` | `--card` | `#232320` (hsl 60 4% 13%) | `#ffffff` | Default card/panel background |
| `{colors.elevated}` | `--elevated` | `#2b2a26` (hsl 48 6% 16%) | `#ffffff` | Raised wells, popovers |
| `{colors.muted}` | `--muted` | `#2f2e2a` (hsl 48 6% 17%) | `#efece4` (hsl 44 26% 92%) | Footer strips, well fills |
| `{colors.hairline}` | `--border` | `#38352f` (hsl 40 9% 20%) | `#e6e2d9` (hsl 42 21% 88%) | Decorative hairline — WCAG-exempt, see Contrast contract |
| `{colors.ink}` | `--foreground` | `#f6f5ef` (hsl 51 28% 95%) | `#1b1a17` (hsl 45 8% 10%) | Body text, headlines — AAA on every surface |
| `{colors.ink-muted}` | `--muted-foreground` | `#b0aca2` (hsl 43 8% 66%) | `#6b6559` (hsl 40 9% 38%) | Secondary text, captions, meta, placeholder |
| `{colors.code}` / `{colors.code-well}` | `--code` / `--code-well` | `#e9e7df` / `#201f1c` | `#26241f` / `#f3f1ea` | Fenced/inline code — a distinct surface from `--muted` |
| `{colors.primary}` | `--primary` | `#d97757` | `#d97757` | Brand mark ONLY — never a text-bearing fill (KNOWN uncorrected gap: `RoleLabel`'s `you` tag ships bare `text-primary`, see the terracotta-roles note + Contrast contract) |
| `{colors.primary-fg}` | `--primary-fg` | `#e08a6e` | `#b4491f` | Terracotta AS TEXT (design intent: `you` label, links) |
| `{colors.primary-strong}` | `--primary-strong` | `#b95230` | `#b95230` | CTA fill (send/create) — white label 4.8:1 |
| `{colors.ring}` | `--ring` | `#d97757` | `#b95230` | Focus ring |
| `--scrollbar-thumb` | `--scrollbar-thumb` | `rgba(255,255,255,.18)` | `rgba(0,0,0,.20)` | Global scrollbar thumb (§ Scrollbars) |
| `{colors.semantic-destructive}` | `--destructive` | `#e64c4c` | `#991b1b` | Destructive actions, error status |

**Using tokens in Tailwind utilities:**

```tsx
<div className="bg-background">          // canvas
<div className="bg-sidebar">             // chrome (header/rail)
<div className="bg-card">                // default card
<div className="bg-elevated">            // raised well
<div className="border border-border">   // hairline
<p className="text-foreground">          // ink
<p className="text-muted-foreground">    // ink-muted
<p className="text-primary-fg">          // terracotta as text
<button className="bg-primary-strong text-primary-foreground"> // CTA fill
<button className="ring-2 ring-ring">    // focus ring
```

---

## Colors

### Brand & Accent — three terracotta roles, not one

Terracotta splits into three tokens because white-on-`#d97757` (`{colors.primary}`) is only 3.12:1 — it fails AA for a 14px label:

- **`{colors.primary}` (`--primary`, `#d97757`)** — the brand MARK only: the logo, the dark-theme focus ring. Never a text-bearing fill.
- **`{colors.primary-fg}` (`--primary-fg`)** — terracotta AS TEXT: links. `#e08a6e` dark (6.0:1), `#b4491f` light (5.2:1). This is also the *intended* role for the `RoleLabel` `you` speaker tag, but see the gap below.
- **`{colors.primary-strong}` (`--primary-strong`, `#b95230`)** — the CTA FILL for send/create buttons. White label clears 4.8:1 AA in both themes; the fill itself clears 3:1 against canvas (the non-text component-boundary floor).

**One-accent rule, unchanged.** Terracotta (in whichever of its three roles) is the only chromatic accent on chrome surfaces. The ref colors (teal branches, blue agent info) live *inside* content — those are semantic data, not decoration.

**KNOWN, uncorrected gap — `RoleLabel`'s `you` tag ships bare `--primary` (`text-primary`), not `--primary-fg`.** Mirrors the `Button` `default`-variant gap: token definition is deliberately decoupled from component classes — fix it by swapping `RoleLabel`'s `you` branch to `text-primary-fg` when that component is next restyled. Until then, treat every "`you` = `--primary-fg`" statement below as design intent, not shipped behavior.

### Canvas & Surfaces

The dark canvas IS the whitespace — now detuned to near-neutral (hue ~45) rather than the earlier cool-blue (hue ~224). Four steps, climbing in lightness so depth reads without borrowing chroma:

| Level | Token | CSS Var | Use |
|---|---|---|---|
| 0 — Canvas | `{colors.canvas}` | `--background` | Screen root, terminal well, deepest surface |
| 1 — Chrome | `{colors.sidebar}` | `--sidebar` | Header, session rail, rail footer — ONE tone unifying the persistent frame |
| 2 — Card | `{colors.card}` | `--card` | Cards, dialogs, popovers, list panels |
| 3 — Elevated / Muted | `{colors.elevated}` / `{colors.muted}` | `--elevated` / `--muted` | Raised wells, the composer plane, the work-panel tab strip, the view-switcher tone well |

### Text Tiers

| Token | CSS Var | Use |
|---|---|---|
| `{colors.ink}` | `--foreground` | All headlines and body type — 16.4:1+ (AAA) on every surface, both themes |
| `{colors.ink-muted}` | `--muted-foreground` | Secondary metadata, captions, labels, placeholder — 6.0:1 dark / 4.9:1 light (danger-zone 12-13px meta, still AA) |

### Semantic

- **Lime** (`{colors.semantic-success}` `#84cc16`): Active/working status indicators.
- **Amber** (`{colors.semantic-warning}` `#b8860b`): Orphaned, waiting, attention-needed states.
- **Red** (`{colors.semantic-destructive}` `#e64c4c`): Error, destructive actions.
- **Cyan** (`{colors.semantic-info}` `#c2dcf7`): Agent name, info slot, idle status.

Semantic colors live on status badges and state glyphs — never as section backgrounds or card fills. Mirrored from `grove/core/contracts/status_palette.py` / `agent_palette.py`; the drift tests (`status-tokens.test.ts`, `agent-state-tokens.test.ts`) fail the build if a hex here diverges from Python. **One known pre-existing gap, inherited (not fixed) by the ADE detune:** light-mode `--status-active` lime (`#65a30d` as a glyph reference) drops below 3:1 on some light surfaces — since the hue is frozen (Python-owned), Grove compensates by never signaling ACTIVE/working with the glyph alone: a foreground text label and the `grove-pulse` motion cue always ride with it.

### The three status axes, and why phase looks different

A workspace carries THREE orthogonal status axes, and each must be readable **wherever the workspace is shown** — grid card, session rail row, header identity cluster, work-panel Info tab. An axis that appears on only one surface is a bug: an operator who opens a workspace to look at it must not lose a signal the wall already gave them.

| Axis | Wire | Register | Rendered by |
|---|---|---|---|
| Lifecycle | `WorkspaceStatus` | categorical pill, shown only when it IS the signal | `StatusBadge` |
| Agent activity | `AgentActivityState` | categorical glyph/dot, always leading | `AgentStateMark`, rail `StateDot` |
| Task phase | `TaskPhase` + `index`/`total` | **ordinal — progress, not category** | `PhaseBadge` (glance) · `PhaseMeter` (read) |

**`provisioning` is the one lifecycle value that carries a live readout, not
just a pill.** A container workspace exists for 49 s (warm) to 6.5 min (cold
image build) before its container does, and that window used to read `offline` —
neutral gray, motionless, and advertising `respawn`, the one verb that destroys
the build in flight. Its hue is IDLE's info cyan **by alias** (alive, not yet
ready — never a seventh hue, and never the muted gray), its glyph pulses on the
`grove-pulse` cadence like ACTIVE, and the card's CONTEXT slot swaps to
`ProvisionLine` / the session page gains `ProvisionPanel`. Those carry three
signals and deliberately no fourth: an **indeterminate** `Skeleton` bar (a build
has no percentage — deriving one from BuildKit's output format would report
confidently wrong numbers), an **elapsed clock** counted client-side from
`provision_started_at`, and the provisioner's **last written line** verbatim. The
build log itself sits behind a fold, rendered by `TerminalView`. Lifecycle-wise
the state offers **kill and nothing else** — abandoning a build stays possible,
respawning one does not.

A workspace also carries a fourth, non-status axis that follows the same
everywhere-or-nowhere rule: **runtime** (`Runtime`, host vs container) — see the
Runtime mark section below. It is listed apart because it says nothing about how
the work is going; it says what the agent can reach.

**Phase is the only ORDINAL axis, so it is the only one that must not be a pill.** Its palette (`--phase-*`, mirrored from `phase_palette.py`) is a single-hue lime ramp, not six independent hues, and it deliberately avoids amber and red — those already mean "attention" and "error" on the activity axis, and reusing them would make step 4 of 6 read as trouble. Rendering ordinal data as a categorical badge throws the ordering away, so phase renders in two registers of the same axis:

- **`PhaseBadge`** — dense chrome (card header, rail meta line, identity trigger). A fill-stage glyph (`○ ◔ ◑ ◕ ● ✓`, so progress survives grayscale) + the bare `n/6` fraction; name and note live in the tooltip. Same "unit in the tooltip, not the row" rule as `Stat`.
- **`PhaseMeter`** — detail surfaces with room (identity popover Task section, work-panel Info Task section). The shadcn/Radix `Progress` primitive with the ramp hue on the indicator, plus phase name, `n/6`, the optional note, and **when the phase was last reported** — a phase is a claim the agent made, not an observation, so "verifying, reported 40 minutes ago" is the stalled-run tell.

They ship as two modules (`phase-badge.tsx` / `phase-meter.tsx`), not one: the badge is a dependency-free leaf the always-mounted session rail needs, and only the meter may pull the `Progress` vendor dep — see the shell-import lesson in `webapp/CLAUDE.md`.

**Both render NOTHING at `phase === null`.** The bar is never the sole carrier — glyph, name, and fraction all state it in text — which is why the ramp's pale early steps failing a 3:1 non-text read costs no information.

**Linked refs (`state.ticket_refs`) sit beside phase and follow the same rule.** The row reads INPUT → OUTCOME: issue(s) in plain muted text (context, no hue earned), the PR as the row's one colored, actionable token (`prStateVar` — open/merged/closed reuse `--ref-info` / `--phase-done` / `--status-error`, no new hue). **The row mounts whenever ANY ref exists** — an issue-only workspace (most of a workspace's life, since an orchestrator attaches the issue at create and the PR lands near the end) must show linkage in the webapp the same as the CLI, MCP, and HTTP do. "Absence is not a state" means *zero refs renders nothing*; it never means "an input is not a state."

### Ref colors (git / agent)

- **Teal** (`{colors.ref-branch}` `#26a69a`): Branch names, git refs. The card meta row and the identity popover render the branch as a teal mono VALUE behind a matching teal `GitBranch` glyph; light-mode branch-teal-as-text sits at the same frozen-hue gap as the lime note above (`#1f8c7e` ≈ 3.9:1, outside `contrast.test.ts`'s neutral-only scope) — an accepted trade for mono-ref legibility, not a clean avoidance.
- **Green** (`{colors.ref-add}` `#99d199`): `+N` additions, ahead count, success flash.
- **Red** (`{colors.ref-remove}` `#e66666`): `-N` deletions, behind count, error flash.
- **Blue** (`{colors.ref-info}` `#c2dcf7`): Agent identifier, the `agent` speaker label.

---

## Contrast contract

**Every neutral + brand text pair is WCAG-computed and enforced by `tests/unit/contrast.test.ts`, mirroring the `status-tokens.test.ts` drift-test pattern.** The test parses the real `:root`/`.dark` HSL tokens straight out of `globals.css`, computes contrast ratios, and fails the build if any pair drops below floor — so a future edit that lightens a token can't ship unnoticed.

| Floor | Applies to |
|---|---|
| **AA 4.5:1** | ALL text, including 12-13px metadata/captions/placeholders — no large-text discount |
| **AAA 7:1** | Chat prose (`--foreground` on background/card/elevated/muted) — the surface read for minutes |
| **3:1** | Non-text indicators: focus ring, CTA-fill component boundary |

**Hairline borders are exempt from 3:1, by correct WCAG scoping, not hand-waving.** `--border` lands ~1.3-1.5:1 in both themes (same order as Linear's own hairline). WCAG 1.4.11 requires 3:1 only for graphics *required to identify* a component or state — Grove identifies controls by fill + focus ring (the "tone over lines" rule + filled input wells), so a resting hairline isn't the sole indicator and doesn't need to clear it. The 3:1 obligation is met by the focus ring and CTA-fill boundary instead, both asserted directly.

The scope is deliberately narrow: only the free-to-retheme **neutral ramp + terracotta trio**. The frozen `--status-*`/`--agent-*`/`--ref-*` hues are Python-mirrored and owned by their own drift tests (`status-tokens.test.ts`, `agent-state-tokens.test.ts`) — `contrast.test.ts` doesn't touch them, since their hue can't move regardless of what the ratio says (see the light-mode lime gap noted above).

**A second known, uncorrected gap: `RoleLabel`'s `you` tag ships bare `--primary` (`text-primary`), not `--primary-fg`.** The token itself is AA-clean in its intended TEXT role (`--primary-fg`, 6.0:1 dark / 5.2:1 light) — the gap is that the component never switched to it, the same class of gap as `Button`'s `default` variant (token definition is deliberately decoupled from a component-class restyle). `contrast.test.ts` asserts the token pair, not this component's class list, so it doesn't catch the mismatch — don't cite `RoleLabel`'s `you` label as proof `--primary-fg` renders anywhere until this is fixed.

---

## Typography

### Font Families

- **Geist Sans** (`--font-sans`): All UI chrome, headings, body, buttons, captions, session-rail rows. Self-hosted via `next/font` in `app/fonts.ts`.
- **Geist Mono** (`--font-mono`): Branches, base branches, agent/model identifiers, SHAs, and **all code** (inline + fenced) at the dense-row 13px scale.
- **JetBrains Mono Nerd Font** (`--font-terminal` → `--font-jetbrains-nerd`): Scoped ONLY to the terminal pane subtree (never `<html>`) so its full glyph range doesn't bleed into app chrome. Falls back to Geist Mono when absent.

### Applied type scale (what components actually use)

| Tier | Class | Use |
|---|---|---|
| `{typography.page-title}` | `text-base` (16px) `font-semibold` | Route/dialog titles, prominent stat values |
| `{typography.body}` | `text-sm` (14px) | Body copy, controls, buttons, inputs, session-rail rows |
| `{typography.chat-prose}` | `text-prose` (16px / 1.6) | Assistant + user transcript prose — the reading surface, consumes `--text-prose` |
| `{typography.dense-row}` | `text-[13px]` | Identity-popover mono values, ALL code |
| `{typography.meta}` | `text-xs` (12px) `text-muted-foreground` | Relative times, byline copy, secondary info |
| `{typography.section-label}` | `text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground` | Panel section headers (Work panel's "ACTIVITY"/"IDENTITY"/"TIMELINE") |
| `{typography.micro-label}` | `text-[10px] uppercase tracking-wider` | Stat units, inline state labels |
| `{typography.mono}` | `font-mono text-[13px]` | Branches, SHAs, agent/model ids, fenced + inline code |

**Chat prose reads at the `--text-prose` scale (design-direction §4.3):** both the assistant markdown (`AssistantTextPart`) and the user bubble (`CollapsedUserText`) in `components/chat/chat-message.tsx` render at the `text-prose` utility — 16px / 1.6, the `--text-prose` `@theme` token — the one surface deliberately looser and larger than the 14px chrome because it is read for minutes. The user bubble's `USER_MESSAGE_CLAMP_HEIGHT` derives its 6-line collapse cap from the matching 1.6rem line-height (9.6rem = 153.6px), which `tests/e2e/chat.spec.ts` pins.

### Dormant named scale (`globals.css` `@theme`, design-direction.md §4.3)

`--text-prose` is live (the chat transcript — see above); the remaining tiers exist in `globals.css` `@theme` but are not yet consumed — reserved for a future phase to adopt as `text-page-title`, `text-card-title`, `text-caption`, `text-code`, `text-micro` utilities:

| Token | Size / line-height | Tracking |
|---|---|---|
| `--text-page-title` | 20px / 1.3 | −0.01em |
| `--text-card-title` | 16px / 1.4 | −0.005em |
| `--text-prose` | 16px / 1.6 | 0 |
| `--text-caption` | 13px / 1.4 | 0 |
| `--text-code` | 13px / 1.5 | 0 |
| `--text-micro` | 11px / 1.3 | **+0.04em** |

Note the micro-label tracking mismatch: the dormant token specs `+0.04em`, but every shipped uppercase micro-label (`SECTION_LABEL` in `work-panel.tsx`, the rail filter's `DropdownMenuLabel`s — "Show states", "Projects") uses `tracking-[0.08em]` directly. Don't "fix" one to match the other without checking both call sites — they're independently correct today, just not yet unified.

### Typography principles

- **Single weight voice: 600 for titles/section-labels, 500 for micro-labels, 400 for body.** Grove resists 700+ weights everywhere.
- **Mono only in code/data contexts** — git refs, code, terminal, model/agent ids. UI chrome is always Geist Sans.
- **`letter-spacing: 0.01em` on `body`** restores air at dense scale — a measured departure from the strict per-tier values; don't fight it.
- **Chat prose reads at `text-prose` (16px / 1.6)** — assistant markdown AND the user bubble, the one surface read for minutes (design §4.3). The user bubble's `USER_MESSAGE_CLAMP_HEIGHT` tracks the matching 1.6rem line-height (9.6rem for 6 lines) so the collapse clamp math stays exact — keep the two in lockstep if either changes.

---

## Layout

### The persistent shell

Every non-login route renders through `app/(shell)/layout.tsx` — ONE header (52px, `h-13`) and ONE persistent left session rail (280px, hidden entirely at `w-0` when collapsed — no icon strip), including `/w/[id]`. The header's middle is a generic slot: pages that need to fill it (today, only the session page's identity trigger) portal a `ReactNode` into it via `HeaderSlotContext` rather than passing a prop up through the layout tree — the portaled subtree re-renders with the page, so live session data stays fresh without pushing an ever-changing node through React state.

The viewport-fill magic number is **`3.25rem` (52px, `h-13`) — the header alone.** There is no separate status bar; its former content (daemon health, uptime, version/update nudge, workspace count, GitHub link) lives in the session rail's footer (`RailFooter` in `workspace-sidebar.tsx`). Change `3.25rem` / `top-13` together if the header height ever moves — they are the only two references left.

### Spacing system

Base unit: 4px.

| Token | Value | Use |
|---|---|---|
| `xxs` | 4px | Tight internal gaps, icon margins |
| `xs` | 8px | Chip padding, icon-to-label gaps |
| `sm` | 12px | Input inner padding (horizontal) |
| `md` | 16px | Default card/panel inner padding |
| `lg` | 24px | Card interior padding, transcript inter-message gap |
| `xl` | 32px | Panel internal breathing |

### Landing (`/`) — persisted Hero | Overview

`app/(shell)/page.tsx` reads one persisted `ui-store` slice, `landingView` (`"hero" | "overview"`, alongside `sidebarCollapsed` — zero new machinery):

- **Hero** (default): a centered composer (`max-w-3xl`) — type a task, press Enter, a workspace is created — plus a quiet "Recent" strip of the most-recently-active workspaces. The fleet lives in the rail, one glance away, never hidden.
- **Overview**: today's rich repo-grouped card grid, verbatim, plus the Live focused-pane toggle — the power-user's whole-wall view.

The toggle itself (`landing-view-toggle`) is a quiet two-button segmented control on a `bg-muted/60` well — never terracotta (reserved for the composer's send CTA).

### Session page (`/w/[id]`) — three zones

`rail (shared shell) | transcript column (704px in split, 1280px single-pane, centered) | work panel (Terminal · Diff · Info, resizable + collapsible + full-screen)`.

- The session's **identity cluster** (`ContextBar` — a state-led title trigger opening the session popover) is built by the page and portaled into the shared header's middle slot at every breakpoint — exactly one `context-bar` mount, no separate below-`lg` band. Its `session-state-live` sr-only `aria-live` region announces the state WORD on change only, never the raw task/prompt text, so the panes get the full column below the header.
- The **work panel** provides three tabs — Terminal (the live tmux pane, permanent live-pulse dot on its tab), Diff (StatTrio + line-delta + full commit list), Info (metrics one-liner, agent/model identity, placement, created/paused timestamps). Full-screen is a plain CSS overlay, not a portal, so it composes with the resizable split underneath.
- The transcript/work-panel **split is reachable, never the default**: `react-resizable-panels` with `autoSaveId="grove-detail-split"`, toggled on via the `ViewSwitcher`, lg-only. Every breakpoint OPENS single-pane tabs (`Transcript`/`Terminal`) — transcript-first once a session resolves, else terminal-only — and an explicit tab/view choice wins from there.

### Session catalog (`/sessions` · `/sessions/[id]`)

The host-wide archive: every agent session on the machine, grouped by project, and any one of them opened **read-only**. Both routes ride the shared shell (header + rail), so the only new chrome is one quiet `History` link in the header's `<nav>` — the first route link the header has carried, and it stays a ghost button with the label hidden below `sm`.

- **The list is a two-line row on the rail's grammar, at page width** (`max-w-[72rem]` centered): line 1 = the live cue + the label + a right-aligned relative time; line 2 = a `MetaRow` of agent kind · teal-glyph branch · subdirectory · `grove` provenance. Groups separate by a `{typography.section-label}` header and **space** (`gap-6`), never a drawn line — the tone-over-lines rule.
- **A catalog row carries no agent state, and must not pretend to.** The host scan never parses a transcript, so there is no `AgentStateMark` here — the ONE state glyph system stays exclusive to surfaces that actually measured a state. The only signal is `live` ("an agent is running in this directory"), a `--status-active` dot on the `grove-pulse` cadence with an **sr-only "live"** beside it, so colour is never the sole cue.
- **Honest degradation is visible, not hidden.** A session with no recorded working directory cannot be opened, so its row renders inert at `opacity-55` with `location unknown` in the meta line — the same dimmed-and-inert treatment the rail gives an unmapped row. A session outside any git repo gets its own "No git repository" group, placed by recency like every other.
- **The detail route is the transcript and nothing else.** Same assistant-ui thread as `/w/[id]`, at the wide single-pane measure (`--thread-max-width` 80rem, since no work panel shares its row), under a one-line identity heading and a back arrow to `/sessions`. **No composer, no interrupt, no identity popover, no lifecycle verbs** — read-only is expressed by their absence, never by a disabled control.

### Grid & container

- Landing content: `max-w-[100rem]` (ultrawide-friendly), rail flush-left, full-bleed shell.
- Session catalog: `max-w-[72rem]` centered — a list of two-line rows reads worse at ultrawide than a card grid does.
- Workspace grid (Overview): fluid columns, `minmax(22rem, 1fr)`.
- Transcript column: `max-w-[var(--thread-max-width)]`, shared by transcript content, the notice row, and the composer via one `CHAT_COLUMN` constant in `chat-panel.tsx` so the three never drift. The token is 44rem (704px, the assistant-ui/Claude reading measure) while the transcript shares the row with the work panel in split view; single-pane (no work panel on screen) it widens to 80rem (1280px) so the column fills the freed-up width instead of framing it in empty gutters — set via `ChatPanel`'s `wide` prop, which `AgentWorkspace` derives from `!showSplit`.
- User bubbles cap at `max-w-[85%]`.

---

## Elevation & Depth

| Level | Treatment | CSS | Use |
|---|---|---|---|
| 0 (flat) | No border, canvas bg | `bg-background` | Page body, terminal well |
| 1 (chrome) | Sidebar bg (tone only) | `bg-sidebar` | Header, rail, rail footer — borderless, tone alone separates |
| 2 (card lift) | Card bg + hairline border | `bg-card border border-border` | Cards, dialogs, popovers |
| 3 (elevated/well) | Elevated or muted bg | `bg-elevated` / `bg-muted/40` | Composer plane, work-panel tab strip, the view-switcher tone well |
| 4 (focus ring) | 2px terracotta ring | `ring-2 ring-ring` | Focused inputs, focused buttons |

Depth is carried by the **surface tone ladder**; hairline borders are a last resort on persistent surfaces. **No drop shadows on dark surfaces.** Two sanctioned exceptions: floating overlays (Popover/Dialog/Sheet/Tooltip/Dropdown) keep `shadow-lg` at low opacity in both themes because a surface leaving the page plane needs an edge against whatever it covers; and the steer composer's floating-plane `--composer-shadow` is a subtle LIGHT-mode-only lift (dark stays shadowless — the ladder carries depth), a per-theme var, not the `dark:` variant.

**The session page is fully frameless.** `detail-panel` is a bare full-bleed flex column — no lift gutter, no bordered card, no terminal frame. The terminal pane's title bar separates from its grid by tone alone (`bg-muted/40` vs `bg-background`), matching the header's own tone-only separation from the canvas.

---

## Iconography

**lucide-react**, one global stroke rule (design-direction.md §4.5): `svg.lucide { stroke-width: 1.75 }` in `globals.css`'s `@layer base` — lucide has no theme provider, so this CSS selector (targeting the `lucide` class every icon carries) is the ONE seam for an app-wide stroke tune. **Never pass `strokeWidth` on an individual `<Icon>`** to work around it; 1.75 (lucide's default is 2) is the measured register that survives Grove's 14px dense rows without going wispy at exactly-AA `muted-foreground` contrast.

- **Size ramp:** chrome/action **16px** (`size-4`) · dense identity/work-panel row **14px** (`size-3.5`) · inline micro glyphs (rail `StateDot`, delta dots) **6px** (`size-1.5`). SVG sizing lives per-component-size, not in a base default.
- **Color:** `text-muted-foreground` default → `text-foreground` on hover/active → a semantic hue only for state. Never a saturated icon as decoration.
- **One glyph per concept, app-wide:** tool = `Wrench` (the "Used N tools" trigger; each revealed digest row leads with the success `CircleCheck` instead, since Grove's wire emits a tool call only post-hoc), question = `CircleHelp`, agent/response = `Sparkles`, you = `User`, success = `CircleCheck`, error = `TriangleAlert`, expand/collapse = `ChevronDown` + `rotate-180` — **except on chat surfaces**, where the tool/notification/show-more expanders rotate `-90-when-closed` (the modern-chat-native template convention, a named divergence). Agent-state marks (`▶ ◑ ⚠ ○ ◌ ✗ ·`) stay custom text glyphs — they're the semantic state system (`AgentStateMark`), drift-synced with the TUI, not part of the lucide set. The runtime marks (`■` host / `▣` container, `RuntimeMark`) are the same kind of thing and for a stronger reason: their character IS the cross-client contract (`runtime_palette.py`), so a lucide glyph could not be shared with the terminal at all.

---

## Scrollbars

**One app-wide mechanism, the standard CSS properties, applied globally** (design-direction.md §4.7):

```css
@layer base {
  * { scrollbar-width: thin; scrollbar-color: var(--scrollbar-thumb) transparent; }
}
```

`scrollbar-width`/`scrollbar-color` is Baseline (Safari since Dec 2024, Firefox/Chromium long-standing), theme-aware via `--scrollbar-thumb`, and reserves no gutter by default. This also themes `@assistant-ui/react`'s `Thread.Viewport` for free — it ships bare `overflow-y-scroll` with no scrollbar CSS of its own.

**`::-webkit-scrollbar` is banned, not just discouraged.** Current Chrome deprioritizes the webkit pseudo-elements in favor of the standard props, and setting any `::-webkit-scrollbar` size **force-renders a classic always-visible bar, killing the native macOS overlay auto-hide** this rule relies on.

**`ScrollArea` is retired app-wide.** Every former consumer (`CommitList`, the session rail body, `TurnsView`) moved to a plain `overflow-y-auto` div under the global rule; the `components/ui/scroll-area.tsx` primitive + its `@radix-ui/react-scroll-area` dependency are deleted now that no consumer remains. Reach for `ScrollArea` again only if a single surface needs genuine JS-driven idle-hide (none do today).

---

## Motion

Calm, short, ease-out (150-250ms), nothing bounces, everything honors `prefers-reduced-motion` (design-direction.md §5).

- **`grove-pulse`** (existing) — the 4Hz stepped blink for ACTIVE/RUNNING status marks and the transcript's "agent working" dot (`chat-shimmer` in `chat-panel.tsx`) — a terminal-cursor-like blink, not a smooth spinner ease, so both readings share one cadence.
- **`grove-shimmer`** — the ONE loading skeleton (`components/ui/skeleton.tsx`), reused everywhere data loads (rail, transcript, peek, grid): a ~5%-opacity terracotta band translated across the muted base. `motion-safe:` only — reduced motion gets a static `bg-muted` block.
- **`grove-ring`** — the one-shot "just landed" ring (`components/shared/landing-ring.tsx`) on a freshly-created card/row: an INSET box-shadow ring (so `overflow-hidden` doesn't clip it) that decays to transparent within ~900ms and never persists. Rendered as an absolutely-positioned sibling, never touching the host's own className, so the host's `ring-*`/`border-*` test contracts stay intact.

Both `grove-shimmer` and `grove-ring` are the ONLY two places terracotta *moves* across a whole surface — both decay within ~1s so neither becomes a steady-state fill (accent scarcity holds).

**Transcript rhythm — three-tier, line-free** (`GroveMessage` in `chat-message.tsx`, mirroring design-direction.md §4.6): a top margin keyed to position — **56px** (`mt-14`) before a turn head (a new user prompt or a continuation marker), **24px** (`mt-6`) before an agent reply, **20px** (`mt-5`) before an intra-turn part (tool group, note, notification, question, **file-edit diff card**). The turn boundary stays ≥2× the inter-message gap so the transcript reads as distinct exchanges without a drawn divider — carried by space + the `RoleLabel`, never a line. (The intra-turn tier was raised 12→20px in the transparent-edits follow-up: diff cards read cramped at 12px, and 12px let the agent reply's hover copy button overlap the next part — paired with taming that copy button's reserve pull-back to `-mb-5`; see webapp/CLAUDE.md.)

**Press tactility:** the composer send buttons (both create and steer) carry `active:scale-[0.97] motion-reduce:active:scale-100`, transform listed explicitly — never `transition-all`.

---

## Shapes

### Border Radius Scale

Derived from `--radius: 0.5rem` (8px, both themes) via the shadcn formula in `globals.css`'s `@theme inline` — `radius-lg = var(--radius)`, `radius-md = radius - 2px`, `radius-sm = radius - 4px`, `radius-xl = radius + 4px`. `rounded-2xl`/`rounded-3xl`/`rounded-full` are Tailwind's own untouched defaults, not remapped by the formula.

| Token | Value | Tailwind class | Use |
|---|---|---|---|
| `{rounded.sm}` | 4px | `rounded-sm` | Small chips |
| `{rounded.md}` | 6px | `rounded-md` | ALL buttons, badges, form inputs |
| `{rounded.lg}` | 8px | `rounded-lg` | Cards, dialogs, popovers, list panels |
| `{rounded.xl}` | 12px | `rounded-xl` | Overview cards, the user bubble, fenced-code wells |
| `{rounded.composer}` | 24px | `rounded-3xl` | The create-composer hero card + the steer composer well — the ONE surface that opts up (design §4.8) |
| `{rounded.pill}` | 9999px | `rounded-full` | Status pills, toggle pills, and the composer's circular send / Stop CTAs (the named exception below) |

---

## Components

### Buttons

**`button-primary`** — CTA. `bg-primary-strong text-primary-foreground` (never bare `bg-primary` — see the terracotta-roles note above), `rounded-md`, weight 500. Focus ring: `ring-2 ring-ring ring-offset-2 ring-offset-background`.

**`button-secondary`** — `bg-card text-foreground border border-border`, same geometry.

**`button-ghost`** — `bg-transparent text-foreground`, same geometry.

**Rule:** Never pill-round CTAs (`rounded-full` on a `<button>`) — reserved for status/toggle pills. TWO deliberate composer exceptions: the create/steer composer card is `rounded-3xl` (24px, not a pill), and the steer composer's **circular** send + Stop buttons are genuinely `rounded-full` (icon-only, single-glyph — the modern-chat-native send affordance).

### Status Badges

`bg-elevated text-muted-foreground`, `rounded-full`, `text-xs`, `px-2 py-0.5`. Active state colors only the glyph and text via the status CSS variable — never fills the badge background with the status hue. Colors are inherited from the Python TUI contract, drift-tested.

### Workspace Cards (Overview grid, `components/workspace/card.tsx`)

The same calm `state · title · time` vocabulary as a rail row, in TWO visual tiers separated by **space, not lines or a tinted well**. `rounded-xl bg-card` on a `border-border/60` hairline; hover is a tonal shift + border-strengthen — **no shadow, no translate lift, no full-card status glow**. Three regions:

- **HEADER** — the canonical `AgentStateMark` glyph (the same state atom the identity trigger wears — NOT a second badge) · title link (one line, truncate) · the `PhaseBadge` · a right-aligned `RelativeTime`. The lifecycle `StatusBadge` returns to this line ONLY when it is itself the signal (no agent session, or an `orphaned`/`error` lifecycle); every healthy card wears zero pills.
- **TICKETS** — `TicketLinkage`, mounted whenever any ref exists, directly under the header beside the phase it correlates with.
- **CONTEXT** — "happening now" (`AgentLiveStatus.taskLine`), `line-clamp-1`; error detail wins the slot while erroring, keeping the `· N bg` suffix. A `provisioning` lifecycle wins it outright (`ProvisionLine`): there is no agent session during a build, so the honest alternative would be "no agent session" held for six minutes.
- **META** — TWO aligned `Stat` rows on one 11px `h-5` baseline grid (shared `gap-3`, so card heights stay uniform across the grid), then the one prose last-commit line. Row 1 = provenance (teal `GitBranch` + mono branch · ahead · behind · dirty · the always-present `RuntimeMark` · placement-when-root); row 2 = activity (turns · tool calls · tokens in/out) with the WORKING-gated Live toggle pinned right. Every count is an icon + tabular value via the shared **`Stat`** atom (`components/shared/stat.tsx`): the unit lives in the tooltip/aria-label, `tone` colors the value only (`--ref-add` ahead, `--ref-remove` behind), and a **zero renders NOTHING** — no "0 ahead 0 behind" noise. Those atoms keep their other homes too — the Diff/Info tabs, the identity popover. No footer well — tone breaks belong to the page, not every card. **The rail's meta line deliberately does NOT share this atom:** it stays an icon-free quiet `MetaRow` with `+N/−M` line-changed sigils and a plain-muted (not teal) branch — same `--ref-add`/`--ref-remove` tone grammar, different register + fields, so the atom is shared in spirit, not forced across both.

Attention (waiting/blocked/error) keeps a single thin LEFT `border-l-2` accent bar in the tier accent var — the only on-card hue. Live focus is a quiet terracotta ring. Zero-loss: every datum the card carries has a visible home (nothing moved behind a tooltip).

### The session rail (`components/layout/session-rail.tsx`)

Grove's "thread list" — the fleet moved out of the center card grid into a persistent left rail, the fastest surface in the app:

- **Row shape:** TWO lines on a fixed rhythm (`leading-5` head / `leading-4` meta, `gap-0.5`) so every row is structurally identical. Line one — a quiet 6px `StateDot` (the `session-rail-dot` atom, in the state's `--agent-*` hue) · the title (`text-sm`, truncate, first-class) · the created-ago, right-aligned + muted (the card header's own "state · title · time" rhythm; a static `relativeTimeLabel`, not a per-row ticking interval — the rail's poll/SSE re-renders keep it fresh). Line two — the provenance meta, `runtime · project · branch · +N/−M`: the `RuntimeMark` leads it (`shrink-0`, so truncation can never eat the isolation boundary) and is present on every MAPPED row — a history-only row has no live workspace, so its runtime is genuinely unknown and the slot is absent rather than defaulted; project carries a subtle dotted underline (`decoration-dotted decoration-muted-foreground/40 underline-offset-2`) as the quiet cue separating it from branch (both are muted, so tone alone can't), and never fully vanishes (`shrink-0 max-w-[45%]`); branch is plain muted mono — deliberately NOT the teal `--ref-branch` token, so the `±` change stats carry the row's only hue, and shrinks first when tight (`min-w-0`). `+N`/`−M` come from the live workspace's `diff_added`/`diff_removed` (`WorkspaceActivityView`), zero-suppressed (no change data → nothing renders, blank beats noise); the `PhaseBadge` closes the line as one more text sigil (the no-icons rule below is about lucide chrome, and a fill glyph reads like `±`, not like an icon). Truncation priority: title first, then branch, project never vanishes. **Deliberately no icons** on either line — the dotted underline already does the separating job, and a glyph on an already-dense two-line row would violate the rail's quiet contract. Color is never the only signal — the dot always rides with a `title`/aria-label carrying `<title> — <state>`. Seams: `session-rail-age`, `session-rail-project-name`.
- **Ordering (flat):** ONE cross-project list sorted `modified_at` DESC — no project sections, no date buckets, no attention pin. Attention (`needs_attention`) signals INLINE instead: the dot's per-state color plus a faint `--agent-waiting`-tinted row background, never a separate group.
- **Actionable-only default:** a row is "mapped" when it carries a `workspace_id` that's present in the live snapshot. Unmapped rows (no workspace, or a workspace the daemon isn't tracking) are HIDDEN by default behind a quiet "N unmapped session(s) hidden" note at the list end that flips on `showUnmapped` (persisted) — the debugging escape hatch, not the default read.
- **New session** ghost row at the rail top — the modern-chat new-thread affordance; links to `/` (the hero composer IS the create surface).
- **Hover `⋯` menu** (`session-rail-row-menu`): a sibling of the row link (never nested), revealed on hover/focus (always visible below `lg`), carrying "Make primary" (repins the tracked session via `useRemapSession`).
- **Live overlay:** a row's displayed state comes from the live `/activity` snapshot (by session id) when the daemon is tracking it, else from the recorded session summary — the rail and cards never disagree.
- **Metadata-only rows** (`workspace_id === null`): dimmed, "history only," NOT navigable — a hard wire-visible rule, not a heuristic, and folded into the same "unmapped" hidden-by-default set above. Copy never promises "every session ever."
- **The filter menu is the single organizing instrument (`SidebarFilter`):** one quiet dropdown covering state visibility (each row leads with its `AgentStateMark` glyph), "Needs attention only" (`BellRing`), per-project checkboxes, and "Show unmapped sessions" — `hiddenStates`/`hiddenProjects`/`attentionOnly`/`showUnmapped` all persist, since this menu is the one reachable clear-path. The filtered-empty state ("No sessions match the active filters") carries its own inline "Clear filters" affordance for the same reason.
- **Footer (`RailFooter`):** carries daemon health + live uptime, version + update nudge, workspace count, then a user-identity row + GitHub link.
- **Collapse is a shell concern:** the whole rail hides (`w-0`, modern-chat behavior) — there is NO collapsed icon-strip variant; the header toggle and `[` reopen it. Mobile = a left `Sheet` drawer.

### The runtime mark (`components/shared/runtime-mark.tsx` + `components/workspace/runtime-badge.tsx`)

The isolation axis — whether the agent runs on this host or inside a container — rendered in **two registers**, exactly like agent state's `StateDot`/`AgentStateMark` pair:

- **`RuntimeMark`** — the bare colored glyph, for the dense surfaces (card META row, rail meta line). `role="img"` + an `aria-label`, because the glyph alone is the signal there.
- **`RuntimeBadge`** — glyph + word, for the identity surfaces with room for one (work-panel Info, the `ContextBar` popover). It also carries the two degradation tones: an amber `fallback` badge (the workspace wanted a container, got the host, and its isolation contract is voided for life — never folded into the plain host mark) and the neutral "default container" notice naming `grove init devcontainer`.

**Both runtimes are marked, and that deliberately reverses `PlacementBadge`'s "silence is the signal" rule.** Placement's default is a boring implementation detail worth staying silent about; runtime is the isolation boundary — whether the agent can reach the host filesystem, the host network and the user's credentials — so an unmarked workspace would be indistinguishable from one whose badge failed to render, for the one fact that says what the agent can touch. Silence stays right for placement; do not "fix" this one back to it.

**The glyph is a shared contract, not a mirrored convention.** `■` (host — the work, no boundary drawn) and `▣` (container — the same square inside a boundary) come from `lib/grove/runtime-tokens.ts`, which mirrors `grove/core/contracts/runtime_palette.py` — the file the TUI *imports* — with `tests/unit/runtime-tokens.test.ts` reading that Python for glyph AND label AND hex. So a user moving between the terminal and the browser mid-task sees the same character mean the same thing, enforced rather than remembered. This is why the mark is a text glyph and not a lucide `Box`: an icon set that only this client can render cannot be a shared vocabulary. Colors reuse the existing atoms (`--runtime-host` = the muted neutral, `--runtime-container` = the info blue), so no new semantic hue was minted, and amber/red stay reserved for the degradations above.

### Session identity + control popover (`ContextBar`, `components/workspace/context-bar.tsx`)

The session page's ONE identity + control surface, portaled into the shared header — a **state-led title trigger opening a single popover**. Everything the header cluster needs lives one click behind the title — the calm-chrome default — rather than spread flat across the header.

- **Trigger (`identity-trigger`):** a leading `AgentStateMark` (a SIBLING of the button — the mark is block-ish) · the truncated title · the `PhaseBadge` (the third axis at a glance, identical to the card's) · a short inline label kept ONLY for `blocked`/`error` (color is never the sole signal) · the amber `branch-delta-dot` (`--status-orphaned`, shown only when the branch has any ahead/behind/dirty delta) · `ChevronDown`. An sr-only `aria-live` region (`session-state-live`) beside the mark announces the state WORD on change — never the raw task/prompt text.
- **Popover (`branch-summary`), Separator-divided sections, each `SECTION_LABEL` + `space-y-2 px-4 py-3`:** **Task** (the `PhaseMeter`; first, because a state-led trigger promises "where is this work" — self-hides with no phase) · **Links** (`TicketLinkage`, self-hides at zero refs) · **Identity** (branch → base · agent/model · placement — the branch pair has no other home) · **Changes** (StatTrio + ±diff-line summary, a glance echo of the Diff tab) · **Actions** (the reversible verbs pause/resume/respawn — fire DIRECTLY, and the open popover re-renders the swapped verb IN PLACE as the status flips) · **Danger zone** (destructive `kill`, tinted `--status-error`, opening the `KillConfirmDialog` rendered as a SIBLING of the Popover so it survives the popover closing under the modal's focus grab).
- **Deliberately NOT here** (zero-loss relocations, not drops): the full **CommitList** lives in the work panel's Diff tab (the Changes section's ±summary is the glance); **session switch/track** lives in the rail — the dead-pointer recovery picker lives on as the transcript's own empty-state picker, mounting only when no session resolves.
- The `Separator` hairlines inside are the deliberate **overlay exception** to the tone-over-lines rule: a floating Popover already draws its own edge, so an internal hairline is legible.

### Work panel (`components/workspace/work-panel.tsx`)

The tabbed surface filling the session page's right pane: **Terminal** (the live tmux pane, permanent live-pulse dot), **Diff** (StatTrio + line delta + full commit list — the CommitList home), **Info** (Task `PhaseMeter` · Links `TicketLinkage` · metrics one-liner · agent/model identity, placement, runtime · timeline — Task and Links lead because they answer what the work IS and what it's FOR, and both self-hide). `bg-muted/40` tab strip, full-screen via a plain `fixed inset-0` overlay (composes with the resizable split, no portal/focus-trap fight).

### Composer

**Create composer (`components/composer/composer.tsx`)** — the landing Hero's create surface. `rounded-3xl` (24px) card; the textarea IS the create surface (Enter submits, title auto-derives from the first line); a quiet chip row (agent ▾ · model ▾) plus a fullscreen `Maximize2` toggle opening a Write/Preview Markdown dialog (Preview lazy-loads streamdown). The send button is the one filled `bg-primary-strong` CTA in the view, with `active:scale-[0.97]` press tactility.

**Steer composer (`components/chat/chat-panel.tsx`)** — a **floating input plane**: the textarea over an action row, at the 24px `--composer-radius` on the muted-tinted `--composer-bg`, riding `ComposerPrimitive`. Enter sends, Shift+Enter newlines. Send is a **circular** terracotta CTA (`ArrowUp`, `bg-primary-strong` via the default variant); the WORKING-gated **Interrupt** is a SEPARATE **circular** Stop alongside it (never a toggle that hides Send). **Steering rule: the composer NEVER disables while the agent runs** — steering a working agent with a follow-up IS the product, so `adapter.isRunning` stays unset. The plane carries the ONE sanctioned transcript shadow — `--composer-shadow`, LIGHT-mode only (dark stays shadowless; the surface ladder carries depth), a per-theme var, not the `dark:` variant. **Interrupt** is the sole emergency affordance (no lifecycle menu) — always one click while WORKING.

### Chat transcript (assistant-ui, modern-chat-native)

The transcript runs on `@assistant-ui/react` headless primitives (`ThreadPrimitive`/`MessagePrimitive`/`ComposerPrimitive`) over Grove's own `useExternalStoreRuntime` — never a client LLM call. Rendering adopts the styled assistant-ui/Claude template anatomy verbatim (see the template-adoption rule below):

- **Assistant** = full-width plain prose, no bubble, no avatar; a `RoleLabel agent` tag above it and a reserve-height hover action bar below (a copy affordance that reserves its own height via the template's `-mb-7.5/min-h-7.5` trick, so revealing it on hover never shifts the transcript). Markdown reads at `text-prose` (16px / 1.6) with the template's inline-code pill / fenced-code + table well classes (streamdown's native code/table chrome, flattened to one frame each — see the streamdown lesson in `webapp/CLAUDE.md`); links ride `--primary-fg`, never bare `--primary`.
- **User** = a small right bubble (`bg-muted rounded-xl`) placed in the template's `grid-cols-[minmax(72px,1fr)_auto]` (a spacer column keeps it off the left margin without a hard `%` cap), clamped to 6 lines behind a mask fade + "Show more".
- **Tool runs** = BORDERLESS "Used N tools" **ghost expanders** (a `Wrench` + count trigger, collapsed by default), no bordered card. Each revealed digest row leads with a **success `CircleCheck`** (not `Wrench`) — Grove's wire emits a tool call only post-hoc, so every call reads as completed. Notes / notifications / questions / continuations render as quiet event rows via a `data.by_name` registry, never speech bubbles.
- **The plan card** (`components/chat/todo-list-view.tsx`) is the ONE card pinned above the composer rather than living in the scroll — the agent's current todo list, a `rounded-xl` hairline card on `bg-muted/30`. **Collapsed by default:** it sits in the composer's FIXED chrome, so an expanded 12-item plan would permanently eat the transcript's viewport; collapsed it is a single quiet line. Its header is the disclosure trigger (`ListChecks` + the `PLAN` section label + `done/total` + chevron) and the **`done/total` count stays in the header in BOTH states** — collapsing hides the item text, never the progress signal. Rows use the shared status vocabulary: `CircleCheck`/`--ref-add` (done) · a pulsing `CircleDot`/`--status-active` (in progress) · muted `Circle` (pending).

**Chat-surface chevron divergence:** the tool/notification/show-more/plan expanders rotate `-90-when-closed` → `0` open (the template convention), a NAMED exception to the app-wide `ChevronDown + rotate-180`. See Motion for the turn-rhythm margins.

### The template-adoption rule

Hand-styling over assistant-ui's headless primitives re-creates the old flattened dashboard (a lifecycle-menu-and-strip, a bordered-card transcript). Instead, Grove **adopts the styled templates' anatomy + classes verbatim** — thread-list rows, the borderless tool-fallback expander, the thread/composer tokens (`--thread-max-width` 44rem, `--composer-bg`, `--composer-radius`) — and **forks ONLY where Grove's model genuinely requires it**: the steering composer that never disables; digest-only tool leaves (one collapse level, since Grove's wire emits a completed digest, not an args/result payload). The rail converged back to the template's plain `modified_at` DESC recency sort (see The session rail above) rather than a status-first fork. Cite the upstream templates so nobody re-researches: `packages/ui/src/components/assistant-ui/{thread,thread-list,tool-fallback,markdown-text}.tsx`. **Intent: match the template, diverge on purpose, name the divergence** — never restyle from scratch.

### Terminal Pane

`bg-background font-terminal`, frameless — no border, no rounding, no traffic-light dots. The title bar separates from the terminal grid below it by tone alone (`bg-muted/40` vs `bg-background`), the same convention the app header uses to separate from the canvas. `whitespace-pre` + `overflow-auto` — a wide capture scrolls horizontally, never wraps.

### Inputs & Forms

`bg-elevated border border-input rounded-md text-foreground`, `px-3 py-2`. Focus ring: `focus:ring-2 focus:ring-ring focus:ring-offset-0` (no offset — the elevated background is the inset cue). Placeholder: `placeholder:text-muted-foreground/60`.

### Shared Presentational Atoms (`components/shared/`)

- **`AgentStateMark`** (`state-mark.tsx`): THE agent-state glyph+color system — `▶ ◑ ⚠ ○ ✗`. Uses `--agent-*`. The **full-density** state tier — worn on rich surfaces (the card header, the identity trigger).
- **`MetaRow`** (`meta.tsx`): THE middot-separated meta row (branch · agent · age).
- **`Stat`** (`stat.tsx`): THE single countable-metric leaf — a lucide glyph + compacted tabular value at the parent's meta tier. Zero-suppresses (0/null → nothing), unit in the `title`/aria-label ("<value> <label>"), `tone` (`add`/`remove`) colors the value only. Font-size inherits, so a surface adopts it at its own tier. The card's whole META grid is built from it; reach for it before hand-rolling any `icon + number` stat.
- **`RoleLabel`** (`role-label.tsx`): THE transcript speaker label — lowercase `you`/`agent`, micro-label tier, `you` = `text-primary` clay (ships bare `--primary`, NOT `--primary-fg` — see the terracotta-roles gap above), `agent` = `--ref-info` blue. Shared with the TUI's transcript labels so the two read as siblings.
- **`RelativeTime`** (`relative-time.tsx`): Relative timestamp rendering, used across the rail, work panel, and cards. Also exports **`relativeTimeLabel(iso)`** — the plain string form, for surfaces where the time rides a native `title` tooltip instead of a visible element (the rail row).
- **`StateDot`** (`session-rail-dot`, in `session-rail.tsx`): the **minimal** state tier — a 6px `--agent-*` dot, the calm thread-list cue that replaces the glyph on the rail row. Pure per-state color, no attention override — attention rides the row's background tint instead, never the dot. (See the two-tier state vocabulary below.)
- **`LandingRing`** (`landing-ring.tsx`): The one-shot "just landed" ring — see Motion above.
- **`Dot`** (`dot.tsx`): The middot separator primitive `MetaRow` and other inline meta rows compose.

- **`RuntimeMark`** (`runtime-mark.tsx`): THE isolation-axis glyph — `■` host / `▣` container, `--runtime-*`. Always renders (no absent state). Its labeled register is `RuntimeBadge`.

**Two-tier state vocabulary.** ONE color system (`--agent-*`), TWO densities: the minimal `StateDot` on the thread-list rail (a calm 6px dot), the full `AgentStateMark` glyph on rich surfaces (card header, identity trigger). Reach for the tier the surface's density calls for — never invent a third state renderer. The card wears the bare `AgentStateMark`, no pill. Don't reintroduce a `CountChip`-style pattern without checking whether a `Badge` composition covers the case first.

**Never hand-roll these patterns in a component.** Import the shared atom.

---

## Do's and Don'ts

### Do

- Reserve `{colors.canvas}` as the system's anchor surface — near-neutral, barely warm, not pure black.
- Use terracotta in its correct role: `{colors.primary}` for the mark only, `{colors.primary-fg}` for text, `{colors.primary-strong}` for CTA fills. Never bare `bg-primary` under a text label.
- Use the surface ladder (canvas → sidebar → card → elevated/muted) for hierarchy. Avoid skipping levels.
- Separate persistent surfaces with tone and spacing; reach for a hairline only when tone can't do it (transient overlays keep their chrome).
- Apply the single global lucide stroke rule — never `strokeWidth` on an individual icon instance.
- Let the global `scrollbar-color` rule theme new scroll surfaces — don't add `::-webkit-scrollbar` or a fresh `ScrollArea` wrapper.
- Source status and agent colors from the CSS variables (`--status-*`, `--agent-*`) — never inline the hex.
- Use the `components/shared/` atoms rather than re-implementing the same patterns.
- Check `tests/unit/contrast.test.ts` before shipping a neutral or terracotta token edit — it's the gate, not a launch-day audit.

### Don't

- Don't reintroduce the cool-blue hue-224 canvas — the detune to near-neutral (hue ~45) is deliberate (design-direction.md ruling 1.3).
- Don't use bare `{colors.primary}` as a text-bearing fill (white-on-`#d97757` fails AA) — use `{colors.primary-strong}`.
- Don't add a second chromatic accent for chrome decoration.
- Don't add atmospheric gradients or spotlight/glow cards.
- Don't pill-round buttons (`rounded-full` on a `<button>`) — the sanctioned exceptions are the composer plane (`rounded-3xl`, not a pill) and the steer composer's circular icon-only send + Stop CTAs; nothing else.
- Don't define a new status color inline — extend `globals.css` and the Python contract together.
- Don't re-implement `AgentStateMark`, `MetaRow`, or `RoleLabel` inline in a component.
- Don't render the task phase as a categorical pill, and don't add a surface that shows one status axis without the others — see "The three status axes" above.
- Don't use `box-shadow` on dark surface cards — use the surface ladder. The only exception is floating overlays.
- Don't draw borders on resting transcript blocks — tool rows, question cards, and the composer are tone fills, not outlined boxes.
- Don't add decorative divider lines on persistent chrome — separate by tone and spacing instead.

---

## Responsive Behavior

### Breakpoints (Tailwind defaults)

| Name | Width | Key Changes |
|---|---|---|
| `sm` | 640px | Work-panel/rail tabs go icon-only below this; the ViewSwitcher tabs drop their text label |
| `md` | 768px | Overview grid relaxes; the full git identity is popover-resident at every width now (no `md:flex` header reveal) |
| `lg` | 1024px | Session rail becomes a sticky persistent column (mobile: a `Sheet` drawer); the transcript/work-panel resizable split becomes available (`useMinWidth(1024)` gates split availability only — placement is identical above/below since the rail rides the header at every width) |
| `xl` | 1280px | Overview grid reaches full column count |

### Touch targets

- CTAs: ≥40px height across all viewports.
- Rail rows: two-line provenance rows (`py-1.5`, title + meta) — the calm modern-chat-native thread-list row, still frictionless to scan.
- Form inputs: ≥44px tap target on touch.

### Collapsing strategy

- **Session rail**: hides entirely (`w-0`, desktop `sidebarCollapsed`) or opens as a left `Sheet` drawer (mobile hamburger) — no icon strip, modern-chat behavior.
- **Work panel**: collapsible via `⌘/Ctrl+J`, full-screen via a plain overlay; below `lg` it's tabs-only (no split).
- **Session page**: a fixed-height shell (`h-[calc(100dvh-3.25rem)]`) at every breakpoint — the panes own their scroll from the smallest phone up. Never `overflow-hidden` on the page column or `detail-panel` — a fixed-height, clipped ancestor holding actionable buttons hangs Playwright's pre-click `scrollIntoViewIfNeeded`.

---

## Implementation Guide

### Adding a new component

1. Identify which surface tier it belongs to (canvas / chrome (sidebar) / card / elevated).
2. Use the closest Tailwind utility (`bg-card`, `bg-elevated`, etc.) — never inline a hex.
3. Apply `border border-border` for the hairline where tone alone can't separate two surfaces.
4. Apply `rounded-xl` (12px) for the overview card + user bubble, `rounded-lg` (8px) for dialogs/popovers/panels, `rounded-md` (6px) for buttons/inputs, `rounded-full` for pills (+ the composer's circular send/Stop), `rounded-3xl` (24px) for the composer plane.
5. Source status/agent colors from `var(--status-*)` / `var(--agent-*)` — not a new constant.
6. If the component repeats a `glyph · label · age · branch` pattern, use `MetaRow` and `AgentStateMark`.
7. If it introduces a new neutral or terracotta token, run `npm run test -- contrast` (or the full unit suite) before committing.

### Modifying an existing token

1. Update the HSL value in `app/globals.css` (both `:root` and `.dark`).
2. If the token maps to a semantic status/agent color, update `lib/grove/status-tokens.ts` (or `agent-state-tokens.ts`) AND the Python source in `grove/core/contracts/`. The drift tests catch divergence.
3. If it's a neutral or terracotta token, `tests/unit/contrast.test.ts` will fail the build if the new value drops below its floor — that's the point.
4. Never update `lib/grove/types.gen.ts` by hand — it is codegen output.

### Linting the design

1. Does it use a raw hex literal? → Replace with a CSS variable.
2. Does it use `box-shadow` on a dark surface? → Remove; use the surface ladder instead.
3. Does it inline a status/agent color? → Replace with `var(--status-*)`.
4. Does it re-implement a shared atom (`MetaRow`, `AgentStateMark`, `RoleLabel`)? → Import it.
5. Does it use `rounded-full` on a button? → Replace with `rounded-md` (or `rounded-3xl` if it's genuinely the composer).
6. Does it use bare `--primary` as a background under text? → Replace with `--primary-strong`.
7. Does it add `::-webkit-scrollbar` or a new `ScrollArea` wrapper? → Remove; the global `scrollbar-color` rule already covers it.

---

## Design Philosophy Lineage

This system inherits from the analysis in `DESIGN.md`, which documented Linear's marketing canvas as the primary reference. The inherited principles (near-black canvas as whitespace, a single scarce chromatic accent, a surface ladder with no drop shadows, product-UI-as-protagonist) hold throughout — only specific numbers (hue, radii, the accent's three roles) have moved from that baseline.

**The mental model is "work in a session, switch sessions in a rail," not "monitor a fleet of cards."** This governs three structural decisions, recorded here so a future session doesn't re-derive them: **(1) the canvas is near-neutral** (hue ~45) rather than cool-blue, since a blue cast fights the warm terracotta accent; **(2) chrome consolidates to a rail + 52px header on every route** — one persistent session tree, with all system telemetry (daemon health, version, workspace count) folded into the rail's footer, no separate status bar; **(3) the control plane lives in named, glanceable places** — session switch in rail rows, diff/metrics in work-panel tabs, and git identity + lifecycle in a single session popover behind the title — the progressive-disclosure principle applied at IA scale.

**Adopt the template, diverge only where Grove's model requires, and name the divergence.** The transcript, rail, and cards all follow assistant-ui's styled-template anatomy verbatim (borderless tool expanders, a floating composer plane with circular CTAs, a flat section-less rail list with actionable-only default) — the identity/lifecycle popover consolidates git identity + Actions + Danger zone behind the title as the calm default; nested-project facets dedupe by `repo_root`.
