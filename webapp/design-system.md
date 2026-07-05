---
version: "1.0"
name: Grove-webapp-design-system
description: "A space-black product dashboard built around the deep-cool canvas (#0c0e14, hue 224 nearly pure black with a faint blue tint), off-white foreground (#f0f3f8), and terracotta (#d97757) as the single chromatic accent. Grove's webapp reads as software-craft infrastructure: dense, technical, and focused. Display type is Geist Sans at 500-600 with measured negative tracking. Cards are dark charcoal panels with hairline borders. Terracotta appears on the brand mark, focus rings, and the primary CTA — never decoratively. Page rhythm leans on product UI screenshots and workspace activity tiles rather than atmospheric color. This philosophy is directly inherited from Linear's marketing canvas, adapted to Grove's terracotta brand and space-black palette."

colors:
  # Accent — terracotta (Grove brand, inherits from TUI $primary)
  primary: "#d97757"
  primary-foreground: "#ffffff"
  primary-hover: "#e08a66"
  primary-focus: "#c96a44"
  primary-ring: "hsl(15 74% 61% / 0.4)"
  # Foreground (text)
  ink: "#f0f3f8"
  ink-muted: "#9dafc8"
  ink-subtle: "#8d9ab3"
  ink-tertiary: "#5c6a84"
  # Canvas & surfaces
  canvas: "#0c0e14"
  surface-1: "#151820"
  surface-2: "#191c28"
  surface-3: "#1b1f2c"
  chrome: "#0f1219"
  hairline: "#24293a"
  hairline-strong: "#2d334a"
  # Semantic
  semantic-success: "#84cc16"
  semantic-warning: "#b8860b"
  semantic-destructive: "#e64c4c"
  semantic-info: "#c2dcf7"
  # Ref / git
  ref-branch: "#26a69a"
  ref-add: "#99d199"
  ref-remove: "#e66666"

typography:
  display-xl:
    fontFamily: Geist Sans
    fontSize: 80px
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: -3.0px
  display-lg:
    fontFamily: Geist Sans
    fontSize: 56px
    fontWeight: 600
    lineHeight: 1.10
    letterSpacing: -1.8px
  display-md:
    fontFamily: Geist Sans
    fontSize: 40px
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: -1.0px
  headline:
    fontFamily: Geist Sans
    fontSize: 28px
    fontWeight: 600
    lineHeight: 1.20
    letterSpacing: -0.6px
  card-title:
    fontFamily: Geist Sans
    fontSize: 22px
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: -0.4px
  subhead:
    fontFamily: Geist Sans
    fontSize: 20px
    fontWeight: 400
    lineHeight: 1.40
    letterSpacing: -0.2px
  body-lg:
    fontFamily: Geist Sans
    fontSize: 18px
    fontWeight: 400
    lineHeight: 1.50
    letterSpacing: -0.1px
  body:
    fontFamily: Geist Sans
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.50
    letterSpacing: -0.05px
  body-sm:
    fontFamily: Geist Sans
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.50
    letterSpacing: 0
  caption:
    fontFamily: Geist Sans
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.40
    letterSpacing: 0
  button:
    fontFamily: Geist Sans
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.20
    letterSpacing: 0
  eyebrow:
    fontFamily: Geist Sans
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.30
    letterSpacing: 0.4px
  mono:
    fontFamily: Geist Mono
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.50
    letterSpacing: 0

rounded:
  xs: 4px
  sm: 6px
  md: 8px
  lg: 12px
  xl: 16px
  xxl: 24px
  pill: 9999px
  full: 9999px

spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  xxl: 48px
  section: 96px

components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 8px 14px
  button-secondary:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 8px 14px
    border: "1px {colors.hairline}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 8px 14px
  workspace-card:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.lg}"
    border: "1px {colors.hairline}"
  workspace-card-active:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    border: "1px {colors.hairline-strong}"
  status-badge:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  sidebar-nav:
    backgroundColor: "{colors.chrome}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    height: 56px
  composer-panel:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.xl}"
    border: "1px {colors.hairline}"
    padding: 24px
  detail-panel:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    border: "1px {colors.hairline}"
    padding: 24px
  terminal-pane:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    fontFamily: "Geist Mono"
    rounded: "{rounded.lg}"
    border: "1px {colors.hairline}"
  text-input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    border: "1px {colors.hairline-strong}"
    padding: 8px 12px
  text-input-focused:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    focusRing: "2px {colors.primary-ring}"
    padding: 8px 12px
  project-header:
    backgroundColor: "{colors.chrome}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    height: 56px
    border: "1px solid {colors.hairline}"
---

## Overview

Grove's webapp canvas is "space black" — `{colors.canvas}` (`hsl(224 20% 6%)`) is a deep cool near-black with a faint blue tint. On top sits a four-step surface ladder (`{colors.surface-1}` through `{colors.chrome}`) for cards, panels, and chrome, with hairline borders at `{colors.hairline}`. Off-white text (`{colors.ink}` `hsl(210 24% 96%)`) carries the body and headlines.

The single chromatic accent is **terracotta** `{colors.primary}` (`#d97757`) — used on focus rings, primary CTA, the active status indicator, and the brand mark. Grove inherits this accent from the TUI's `$primary` clay, so the terminal UI and the web dashboard share one brand voice. A lighter hover state (`{colors.primary-hover}`) and a deeper focus-ring tint (`{colors.primary-focus}`) extend the same hue. Grove avoids saturated second accents on the marketing canvas — semantic colors (`{colors.semantic-success}` lime, `{colors.semantic-warning}` amber, `{colors.semantic-destructive}` red) exist only for workspace-status signals, never for decorative sections.

The underlying **design philosophy is inherited from Linear's marketing canvas** (see root `DESIGN.md`, the origin document). Linear's key insights adopted here: near-black canvas as the whitespace; a surface ladder replacing drop shadows; a single chromatic accent used at high discipline; aggressive negative letter-spacing on display type; and product UI screenshots as the decorative protagonist. The only divergence: Grove's brand accent is terracotta rather than lavender, matching the TUI and the Grove identity.

**Implementation:** All tokens map to Tailwind v4 CSS variables in `app/globals.css`. The Tailwind `@theme inline` block bridges these to utility classes (`bg-card`, `border-border`, `text-muted-foreground`, etc.). Never inline a raw hex — reach for the closest CSS variable first.

**Key characteristics:**
- **Space-black canvas** (`{colors.canvas}`) — the deepest surface, with a faint blue tint (not pure black).
- **Terracotta accent** (`{colors.primary}`) — used only on brand mark, primary CTA, focus ring, and active-status indicators.
- Four-step surface ladder (canvas → surface-1 → surface-2 → chrome) carries hierarchy without shadow.
- Display tracking is aggressively negative (−3.0px at 80px); body holds at −0.05px.
- Cards use `{rounded.lg}` 12px corners with 1px hairline borders — never pill-rounded cards.
- **Product UI screenshots and workspace-activity tiles** dominate the screen real estate.
- No second chromatic accent for decoration. No atmospheric gradients. No spotlight cards.

---

## CSS Variable → Design Token Mapping

> Every design token maps to one of Grove's Tailwind v4 CSS variables. This table is the implementation bridge.

| Design token | CSS variable | Dark value | Purpose |
|---|---|---|---|
| `{colors.canvas}` | `--background` | `hsl(224 20% 6%)` | Page canvas — every screen's root surface |
| `{colors.surface-1}` | `--card` | `hsl(222 17% 10%)` | Default card/panel background |
| `{colors.surface-2}` | `--elevated` | `hsl(222 16% 13%)` | Raised wells, composer hero, popovers |
| `{colors.chrome}` | `--sidebar` | `hsl(224 18% 8%)` | Persistent chrome: header, left rail, status bar |
| `{colors.hairline}` | `--border` | `hsl(220 12% 18%)` | Default 1px card borders, dividers |
| `{colors.hairline-strong}` | `--input` | `hsl(220 12% 21%)` | Input backgrounds and focus-ring surfaces |
| `{colors.ink}` | `--foreground` | `hsl(210 24% 96%)` | Body text, headlines |
| `{colors.ink-subtle}` | `--muted-foreground` | `hsl(216 14% 62%)` | Secondary text, captions, meta |
| `{colors.primary}` | `--primary` | `hsl(15 74% 61%)` | Terracotta accent |
| `{colors.primary-focus}` | `--ring` | `hsl(15 74% 61%)` | Focus rings |
| `{colors.surface-2}` | `--muted` | `hsl(222 14% 13%)` | Muted surface backgrounds |
| `{colors.surface-2}` | `--accent` | `hsl(220 13% 17%)` | Hover/accent surface |
| `{colors.semantic-destructive}` | `--destructive` | `hsl(0 68% 58%)` | Destructive actions, error status |

**Using tokens in Tailwind utilities:**

```tsx
// Background tiers
<div className="bg-background">          // canvas
<div className="bg-card">               // surface-1 (default card)
<div className="bg-elevated">           // surface-2 (raised well)
<div className="bg-sidebar">            // chrome

// Borders
<div className="border border-border">  // hairline
<div className="border border-input">   // stronger input border

// Text
<p className="text-foreground">         // ink
<p className="text-muted-foreground">   // ink-subtle
<p className="text-primary">           // terracotta accent

// Focus ring
<button className="ring-ring">         // terracotta focus ring
```

---

## Colors

### Brand & Accent

**Terracotta** (`{colors.primary}`, `--primary`): The single chromatic accent — primary CTA, focus rings, active-status glyphs, the brand mark. A direct inheritance from the TUI's `$primary` clay, ensuring visual continuity between terminal and web.

- Default: `#d97757` (hsl 15 74% 61%)
- Hover lift: `#e08a66` (slightly brighter)
- Focus/pressed: `#c96a44` (slightly deeper)
- Focus ring: 2px at 40% opacity

**One-accent rule.** Terracotta is the only chromatic accent on the marketing/chrome surfaces. The ref colors (teal for branches, cyan for agent info) live *inside* workspace tiles — those are content, not decoration.

### Canvas & Surfaces

The dark canvas IS the whitespace. Sections separate by lift onto surface panels, not by gaps in white. Four steps:

| Level | Token | CSS Var | Use |
|---|---|---|---|
| 0 — Canvas | `{colors.canvas}` | `--background` | Screen root, sidebar behind cards, page areas |
| 1 — Chrome | `{colors.chrome}` | `--sidebar` | Persistent header, left rail, status bar |
| 2 — Card | `{colors.surface-1}` | `--card` | Workspace cards, list panels, default content panels |
| 3 — Elevated | `{colors.surface-2}` | `--elevated` | Composer, detail popover, raised wells, `--muted`, `--accent` |

### Text Tiers

| Token | CSS Var | Use |
|---|---|---|
| `{colors.ink}` | `--foreground` | All headlines and emphasized body type |
| `{colors.ink-muted}` | (interpolated) | Secondary metadata, hover labels |
| `{colors.ink-subtle}` | `--muted-foreground` | Tertiary type, captions, disabled |
| `{colors.ink-tertiary}` | (interpolated) | Quaternary — footnotes, placeholder chrome |

### Semantic

- **Lime** (`{colors.semantic-success}` `#84cc16`): Active/working status indicators. The only vibrant green on the surface.
- **Amber** (`{colors.semantic-warning}` `#b8860b`): Orphaned, waiting, attention-needed states.
- **Red** (`{colors.semantic-destructive}` `#e64c4c`): Error, destructive actions.
- **Cyan** (`{colors.semantic-info}` `#c2dcf7`): Agent name, info slot, idle status.

Semantic colors live on status badges and state glyphs — **not** as section backgrounds or card fills.

### Ref colors (git / agent)

- **Teal** (`{colors.ref-branch}` `#26a69a`): Branch names, git refs.
- **Green** (`{colors.ref-add}` `#99d199`): `+N` additions, ahead, success flash.
- **Red** (`{colors.ref-remove}` `#e66666`): `-N` deletions, behind, error flash.

---

## Typography

### Font Families

- **Geist Sans** (`--font-sans`): All UI chrome, headings, body, buttons, captions. Wired via `next/font` in `app/fonts.ts`. The Geist family is a direct substitute for Linear's custom display/text cuts at 400–600 weight — clean, technical, and legible at dashboard density.
- **Geist Mono** (`--font-mono`): Code blocks, SHA hashes, terminal-style values, commit IDs, structured data tokens.
- **JetBrains Mono Nerd Font** (`--font-terminal`): Live terminal panes (`font-terminal` utility). Falls back to Geist Mono when not installed. Never used in UI chrome.

### Type Scale

| Token | Size | Weight | Line Height | Letter Spacing | Use |
|---|---|---|---|---|---|
| `{typography.display-xl}` | 80px | 600 | 1.05 | −3.0px | Landing hero (marketing only) |
| `{typography.display-lg}` | 56px | 600 | 1.10 | −1.8px | Section opener (marketing) |
| `{typography.display-md}` | 40px | 600 | 1.15 | −1.0px | Page title, empty-state hero |
| `{typography.headline}` | 28px | 600 | 1.20 | −0.6px | Section headings, card group titles |
| `{typography.card-title}` | 22px | 500 | 1.25 | −0.4px | Card primary label |
| `{typography.subhead}` | 20px | 400 | 1.40 | −0.2px | Lead text, intro paragraphs |
| `{typography.body-lg}` | 18px | 400 | 1.50 | −0.1px | Hero subhead, lead copy |
| `{typography.body}` | 16px | 400 | 1.50 | −0.05px | Default body |
| `{typography.body-sm}` | 14px | 400 | 1.50 | 0 | Card meta, sidebar items, footer |
| `{typography.caption}` | 12px | 400 | 1.40 | 0 | Status badges, timestamps, sub-labels |
| `{typography.button}` | 14px | 500 | 1.20 | 0 | All button labels |
| `{typography.eyebrow}` | 13px | 500 | 1.30 | +0.4px | Section eyebrows, group labels |
| `{typography.mono}` | 13px | 400 | 1.50 | 0 | Geist Mono for code, SHA, IDs |

### Typography principles

- **Aggressive negative tracking on display** (−3.0px at 80px). On dashboard chrome, body holds at −0.05px.
- **Single weight voice: 600 for headings, 500 for labels/buttons, 400 for body.** Grove resists 700+ weights on the dashboard.
- **Eyebrow uses positive tracking** (+0.4px) — contrast against the negative-tracked display marks it as taxonomy/metadata.
- **Mono only in code/data contexts.** Geist Mono for terminal panes, SHA values, branch slugs shown in a monospace context, structured IDs. UI chrome is always Geist Sans.
- **Letter-spacing override in `body` tag.** `globals.css` sets `letter-spacing: 0.01em` on `body` to restore air at dashboard density — this is a measured departure from the strict token value; don't fight it.

---

## Layout

### Spacing system

Base unit: 4px. Token names match the front-matter `spacing` block.

| Token | Value | Use |
|---|---|---|
| `xxs` | 4px | Tight internal gaps, icon margins |
| `xs` | 8px | Chip padding, icon-to-label gaps |
| `sm` | 12px | Input inner padding (horizontal) |
| `md` | 16px | Default card inner padding |
| `lg` | 24px | Card interior padding (feature/workspace cards) |
| `xl` | 32px | Panel internal breathing, wide card padding |
| `xxl` | 48px | Section inner padding (composer, CTA panels) |
| `section` | 96px | Between-section vertical rhythm |

### Grid & container

- Max content width: 1280px.
- Workspace grid: fluid column count driven by `minmax(280px, 1fr)` — fills the terminal gracefully.
- Activity wall: same fluid columns, capped at 6 max on ultra-wide.
- Detail page: 2-column (content left, sidebar right) above 768px; single-column below.
- Sidebar rail: fixed width (~240px desktop), collapsed on mobile.

### Whitespace philosophy

The dark canvas IS the whitespace. Sections separate by lift onto surface panels, not by gaps in a white page. Within a panel, generous `lg` (24px) gaps between content blocks; `section` (96px) between major page sections on marketing.

---

## Elevation & Depth

| Level | Treatment | CSS | Use |
|---|---|---|---|
| 0 (flat) | No border, canvas bg | `bg-background` | Page body, behind cards |
| 1 (chrome) | Sidebar bg | `bg-sidebar border-b border-sidebar-border` | Header, rail, persistent chrome |
| 2 (card lift) | Card bg + hairline border | `bg-card border border-border` | Workspace cards, list panels |
| 3 (elevated lift) | Elevated bg + stronger border | `bg-elevated border border-input` | Composer, raised wells, popovers, detail drawers |
| 4 (focus ring) | 2px terracotta ring at 40% opacity | `ring-2 ring-ring` | Focused inputs, focused buttons |

Grove's depth is carried by surface ladder + hairline borders. **No drop shadows on dark surfaces.** The brand resists box-shadow on dark almost entirely — where shadow is unavoidable (a floating popover), use `shadow-lg` with opacity clipped low.

### Decorative depth

- **Workspace activity tiles** and product UI screenshots dominate as decorative depth — same as the product UI screenshots on Linear.
- **No atmospheric gradients.** No spotlight cards with radial glows.
- **Subtle inset wells** (the composer panel, the terminal pane, the detail sidebar) use a slightly lifted surface (`bg-elevated`) with a hairline border to read as depth without shadow.

---

## Shapes

### Border Radius Scale

| Token | Value | CSS | Use |
|---|---|---|---|
| `{rounded.xs}` | 4px | `rounded` | Status badges, small chips (TW default `0.25rem`) |
| `{rounded.sm}` | 6px | `rounded-sm` | Inline tags (TW `0.375rem` ≈ 6px) |
| `{rounded.md}` | 8px | `rounded-md` | All buttons, form inputs (TW `0.5rem`) |
| `{rounded.lg}` | 12px | `rounded-lg` | Workspace cards, list panels, feature cards (TW `0.75rem`) |
| `{rounded.xl}` | 16px | `rounded-xl` | Composer panel, product screenshot frames (TW `1rem`) |
| `{rounded.xxl}` | 24px | `rounded-2xl` | Large hero cards, oversized CTA banners |
| `{rounded.pill}` | 9999px | `rounded-full` | Status pills, toggle-type badges |

---

## Components

### Buttons

**`button-primary`** — Terracotta CTA. The default primary action.
- `bg-primary text-primary-foreground`, padding `8px 14px`, `rounded-md`, weight 500.
- Hover: slightly brighter (`hover:bg-primary/90`).
- Pressed: slightly darker (`active:bg-primary/80`).
- Focus ring: `ring-2 ring-ring ring-offset-2 ring-offset-background`.

**`button-secondary`** — Dark card button. Used for secondary CTAs.
- `bg-card text-foreground border border-border`, same geometry. Hover: `hover:bg-accent`.

**`button-ghost`** — Transparent text button.
- `bg-transparent text-foreground`, same geometry. Hover: `hover:bg-accent`.

**Rule:** Never pill-round CTAs (`rounded-full` on a button). That shape is reserved for status badges and toggle pills.

### Status Badges

**`status-badge`** — Workspace lifecycle pill.
- `bg-elevated text-muted-foreground`, `rounded-full`, `text-xs`, `px-2 py-0.5`.
- Active state uses the status CSS variable directly: `style={{ color: 'var(--status-active)' }}`.
- Never fill a badge with the status color as the background — use it only on the glyph and text.

Status colors (`--status-active`, `--status-idle`, etc.) are inherited from the Python TUI contract (`grove/core/contracts/status_palette.py`). They are drift-tested. Never redefine them here.

### Workspace Cards

**`workspace-card`** — The primary grid tile.
- `bg-card border border-border rounded-lg`.
- Active border lift: `border-border/60` → `border-border` (stronger hairline, not color change).
- Focus (keyboard): `ring-2 ring-ring` around the card container.
- Never paint the card background with the status color. Status reads only on the leading glyph and label.
- Three-line anatomy: (1) glyph + title + age; (2) branch + agent + state; (3) stat numstat. See `components/shared/` atoms.

### Composer Panel

**`composer-panel`** — The create-workspace hero.
- `bg-elevated border border-input rounded-xl`, `p-6`.
- Elevated above the card tier — this is the one surface that intentionally lifts above cards.
- Inputs inside use `bg-elevated border border-input rounded-md` (same tone as panel bg to read as inset wells).

### Terminal Pane

**`terminal-pane`** — Live tmux capture.
- `bg-background font-terminal rounded-lg border border-border`.
- Canvas-tone background so the pane's own SGR foreground colors read against a near-black field.
- `overflow-hidden`, `no-wrap` equivalent via `whitespace-pre` + `overflow-x-hidden`.

### Navigation / Sidebar

**`sidebar-nav`** — Persistent left rail.
- `bg-sidebar border-r border-sidebar-border`.
- Nav item active: `bg-accent text-accent-foreground rounded-md`.
- Nav item hover: `hover:bg-accent/60`.
- Section label (eyebrow): `text-xs font-medium tracking-widest text-muted-foreground uppercase`.

### Inputs & Forms

**`text-input`** — All form fields.
- `bg-elevated border border-input rounded-md text-foreground`, `px-3 py-2`.
- Focus ring: `focus:ring-2 focus:ring-ring focus:ring-offset-0` (no offset — the elevated bg is the inset cue).
- Placeholder: `placeholder:text-muted-foreground/60`.

### Shared Presentational Atoms

Grove's `components/shared/` atoms encode the design rules as reusable leaves:

- **`AgentStateMark`**: THE agent-state glyph+color system. Source of truth for the glyph shown beside agent names. Uses `--agent-working`, `--agent-waiting`, etc.
- **`StatusDot`** / **`StateMark`**: Workspace-status glyph. Uses `--status-active`, etc.
- **`MetaRow`**: THE middot-separated meta row (`branch · agent · age`). Muted text tier, consistent spacing.
- **`CountChip`**: Small numeric badge — `bg-muted text-muted-foreground rounded-full text-xs px-2`.

**Never hand-roll these patterns in a component.** Import the shared atom.

---

## Do's and Don'ts

### Do

- Reserve `{colors.canvas}` as the system's anchor surface — the faint blue tint is intentional.
- Use `{colors.primary}` terracotta ONLY for: brand mark, primary CTA, focus ring, active-status glyphs.
- Use the four-step surface ladder for hierarchy. Avoid skipping levels.
- Pair heading weight 600 with body weight 400 — Grove resists 700+ on the dashboard.
- Apply negative letter-spacing on display sizes (−1px and below at 28px+).
- Use workspace activity tiles and screenshots as the visual protagonist of every section.
- Compose CTAs as `{rounded.md}` 8px corners; status pills as `{rounded.pill}`.
- Source status and agent colors from the CSS variables (`--status-*`, `--agent-*`) — never inline the hex.
- Use the `components/shared/` atoms (`AgentStateMark`, `MetaRow`, `CountChip`) rather than re-implementing the same patterns.

### Don't

- Don't ship a light-mode-first page. The webapp is dark-primary; light mode is supported but designed after dark.
- Don't use terracotta as a section background, card fill, or decorative strip.
- Don't introduce a second chromatic accent (blue, green, purple for chrome decoration).
- Don't add atmospheric gradients or spotlight/glow cards.
- Don't pill-round buttons (`rounded-full` on a `<button>`).
- Don't use `#000000` pure black as the canvas — Grove's canvas has the blue tint.
- Don't define a new status color inline — extend `globals.css` and the Python contract together.
- Don't re-implement `AgentStateMark`, `MetaRow`, or `CountChip` inline in a component. Import the shared atom.
- Don't use Tailwind `dim` equivalent (`opacity-50` as a blanket mute) — use `text-muted-foreground` instead (emulator-consistent, theme-aware).
- Don't use `box-shadow` on dark surface cards. Use the surface ladder.

---

## Responsive Behavior

### Breakpoints (Tailwind defaults)

| Name | Width | Key Changes |
|---|---|---|
| `sm` | 640px | Composer goes full-width |
| `md` | 768px | Detail 2-col; nav hamburger; single card-col below |
| `lg` | 1024px | Grid 2-col → 3-col; sidebar rail visible |
| `xl` | 1280px | Grid 3-col; full content width reached |
| `2xl` | 1536px | Max-width capped at 1280px; extra padding on sides |

### Touch targets

- CTAs: ≥40px height across all viewports.
- Status pills: ≥32px tap height; touch viewports use ≥44px.
- Form inputs: ≥44px tap target on touch.

### Collapsing strategy

- **Sidebar**: collapses to hamburger/sheet below `md`.
- **Workspace grid**: fluid columns using `minmax(280px, 1fr)` — naturally collapses to 1-col on narrow.
- **Display type**: scales toward `{typography.display-md}` (40px) on mobile from 80px on desktop.
- **Detail sidebar**: stacks below the main content panel below `md`.

---

## Implementation Guide

### Adding a new component

1. Identify which surface tier it belongs to (canvas / chrome / card / elevated).
2. Use the closest Tailwind utility (`bg-card`, `bg-elevated`, etc.) — never inline a hex.
3. Apply `border border-border` (card-tier) or `border border-input` (elevated-tier) for the hairline.
4. Apply `rounded-lg` for cards, `rounded-md` for buttons/inputs, `rounded-full` for pills.
5. Source status/agent colors from `var(--status-*)` / `var(--agent-*)` CSS variables — not from a new constant.
6. If the component repeats a `glyph · label · age · branch` pattern: use `MetaRow` and `AgentStateMark`.
7. Run `npm run typecheck && npm run test` before committing.

### Modifying an existing token

1. Update the HSL value in `app/globals.css` (both `:root` for light and `.dark` for dark).
2. If the token maps to a semantic status color (`--status-*`, `--agent-*`), update `lib/grove/status-tokens.ts` AND the Python source in `grove/core/contracts/`. The drift test (`status-tokens.test.ts`) will catch divergence.
3. Never update `lib/grove/types.gen.ts` by hand — it is codegen output.

### Linting the design

Check a component against this system with these questions:
1. Does it use a raw hex literal? → Replace with a CSS variable.
2. Does it use `box-shadow` on a dark surface? → Remove; use surface lift instead.
3. Does it inline a status/agent color (`#84cc16`, `#b8860b`, …)? → Replace with `var(--status-*)`.
4. Does it re-implement `MetaRow` or `AgentStateMark`? → Import the shared atom.
5. Does it use `rounded-full` on a button? → Replace with `rounded-md`.
6. Does it use terracotta (`--primary`) as a background or decorative fill? → Remove; reserve for CTAs and focus only.

---

## Design Philosophy Lineage

This system inherits directly from the analysis in `DESIGN.md` (committed 2026-06-17), which documented Linear's marketing canvas as the primary reference. The key inherited principles:

1. **Near-black canvas as whitespace.** Linear uses `#010102`; Grove uses `hsl(224 20% 6%)`. Both treat the deep dark surface as the whitespace — sections separate by lift, not by gaps.
2. **Single chromatic accent, used sparingly.** Linear uses lavender `#5e6ad2`; Grove uses terracotta `#d97757`. Both treat the accent as scarce: brand mark, primary CTA, focus ring only.
3. **Four-step surface ladder, no drop shadows.** Both systems carry hierarchy through background-color steps + hairline borders. Both resist box-shadow on dark.
4. **Aggressive negative tracking on display.** −3.0px at 80px is the shared specification.
5. **Product UI as the decorative protagonist.** Linear leads every section with product screenshots; Grove leads with workspace-activity tiles and terminal pane captures.
6. **No atmospheric gradients. No spotlight cards.** Both systems keep the dark canvas clean.

The original DESIGN.md file is the research artifact; this file is the implementation contract.
