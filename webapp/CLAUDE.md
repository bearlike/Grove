# Grove Webapp Engineering Guidelines

> ↑ [root](../CLAUDE.md)

The webapp under `webapp/` is the **read-only mobile-first dashboard** for Grove workspaces — Next.js 15 (App Router) + React 19 + Tailwind v4 + shadcn/ui, talking to the Grove daemon (`grove daemon serve`) via a Backend-for-Frontend route. Phone-friendly grid + project facets + live agent peek + comprehensive per-workspace commit history. Read-only by design: no mutations, no auth surface, no backend logic that doesn't already exist in the daemon.

This file composes **on top of** the repo-root [`CLAUDE.md`](../CLAUDE.md). Inherited principles (KISS, DRY, YAGNI, code-as-poem, side-effects-at-edges, tests-pin-contracts, Pydantic at public boundaries) all still apply — this file captures only what is *additional* or *different* for the Next.js stack. Engine + cross-cutting lessons stay upstream.

## Documentation routing

| Concern | File |
|---|---|
| Webapp engineering + visual contract (this file's scope) | `webapp/CLAUDE.md` (you are here) |
| Engine, lifecycle, manager, config, build, repo-wide policy | [`../CLAUDE.md`](../CLAUDE.md) |
| TUI engineering lessons | [`../src/grove/tui/CLAUDE.md`](../src/grove/tui/CLAUDE.md) |
| TUI visual contract | [`../docs/design-system.md`](../docs/design-system.md) |

**Rules**

- Webapp engineering lessons and webapp visual contract both live in *this* file. Splitting them into two adds friction without payoff at our current scale; promote to a sibling `webapp/design-system.md` only when this file exceeds ~500 lines.
- Anything that crosses the wire (request/response shapes, error envelope, status palette) lives upstream — engine `grove/core/contracts/`, TUI palette `grove.core.contracts.status_palette` — and the webapp consumes via codegen. Don't redefine wire shapes here. Don't redefine status colors here either; mirror the upstream Python source through `lib/grove/status-tokens.ts` (drift-tested).
- A change that affects both the engine wire and this client lives in the engine commit; the webapp commit follows by re-running `npm run codegen`. Same commit if small, separate commits if large — but never invert the order.

## Stack contract (what we use, why, where)

| Layer | Choice | Notes |
|---|---|---|
| Framework | Next.js 15 App Router (React 19) | RSC where it pays, `"use client"` for interactivity. SSG for static pages, dynamic for `/api/*` and `/w/[id]`. |
| Styling | Tailwind v4 via `@tailwindcss/postcss` | Config lives in `app/globals.css` `@theme` block — NOT in a JS config. See `Tailwind v4 traps` below. |
| Component primitives | shadcn/ui via `npx shadcn@latest add <name>` | Compose, don't reinvent. All primitives go to `components/ui/`. |
| Animation utilities | `tw-animate-css` | Required for radix's `animate-in`, `slide-in-from-*`, `zoom-in-*`, `fade-in-*` classes (Tooltip, Sheet, Dropdown). Imported once in `globals.css`. |
| Icons | `lucide-react` | One icon set, never mix. |
| Theme | `next-themes` | `attribute="class"`, `defaultTheme="system"`. Mounting guard required (jsdom: see Testing). |
| Data fetching | TanStack Query 5 + SSE | Home grid and activity wall ride one SSE stream (`useActivityStream`, 4 s poll fallback). Remaining polls pinned by hook: peek 2 s + commits 15 s (detail page), workspaces 5 s (status bar). Never fetch in components — go through `lib/grove/hooks.ts`. |
| Type generation | `openapi-typescript` | `npm run codegen` writes `lib/grove/types.gen.ts` from daemon's `/openapi.json`. CI gate: `npm run codegen:check`. **Never hand-edit `types.gen.ts`.** |
| Tests | Vitest (unit + component) + Playwright (E2E) | Hermetic E2E uses fake daemon at `tests/e2e/_fake-daemon.ts`; live smoke (`tests/e2e/_live-smoke.ts`) is opt-in `npx tsx`. |

## Architecture

```
Browser ──http──▶ Next.js  ──http──▶ Grove daemon (loopback only)
                  │
                  ├ /api/grove/[...path] = thin proxy (BFF)
                  ├ /            = home grid
                  └ /w/[id]      = detail page
```

- **BFF (Backend-for-Frontend)**: `app/api/grove/[...path]/route.ts` is the **only** network boundary the browser hits. Daemon URL comes from `GROVE_DAEMON_URL` env (default `http://127.0.0.1:7421`). The browser never sees the daemon directly: no CORS, no client-side daemon URL config. LAN reachability comes from binding Next on `0.0.0.0` and forwarding through the BFF.
- **Wire types**: `lib/grove/types.ts` re-exports schemas from `lib/grove/types.gen.ts` (auto-generated). Components import from `lib/grove/types`, never from `types.gen.ts` directly. The codegen pipeline IS the DRY anchor for the engine ↔ webapp contract.
- **Domain models**: `lib/grove/{client,workspace-card,repo-facet}.ts` — class-encapsulated, methods on the type that owns the state (mirrors the engine's "code as poem" principle). `GroveClient` wraps the BFF; `WorkspaceCardModel` owns card-level derived state; `RepoFacet` groups workspaces by repo.
- **Hooks**: `lib/grove/hooks.ts` is the single subscription surface. One hook per resource. Components consume hooks; **components never fetch directly**. List surfaces (home grid, activity wall) subscribe to the one `useActivityStream`; only the detail page still polls per-workspace resources.

## Tailwind v4 traps (every one of these silently breaks the design)

1. **The `@theme` block in `app/globals.css` is mandatory.** Tailwind v4 + `@tailwindcss/postcss` does **not** consume `tailwind.config.{ts,js}`. Color utilities like `bg-background`, `bg-card`, `bg-muted`, `bg-primary`, `border-border`, `ring-ring`, `text-foreground`, `text-muted-foreground` are silently absent in compiled CSS unless declared via:
   ```css
   @theme inline {
     --color-background: hsl(var(--background));
     --color-card: hsl(var(--card));
     /* ...one entry per shadcn token... */
   }
   ```
   This is THE biggest gotcha and was the root cause of an entire session's worth of "the design looks broken" feedback. Verification probe: `curl http://127.0.0.1:3000/_next/static/css/app/layout.css | grep -c '\.bg-card'` should return `>0`. A green build does **not** mean the design system is wired — only the CSS-utility audit does.
2. **Animations and keyframes belong inside `@theme`.** `--animate-grove-pulse: grove-pulse 1s ...` and the `@keyframes grove-pulse { ... }` block both go inside the `@theme` block, not as siblings. The JS-config `theme.extend.animation` route is dead in v4.
3. **Radix animation classes need `tw-animate-css`.** Without `@import "tw-animate-css";` in `globals.css`, classes like `animate-in fade-in-0 zoom-in-95 slide-in-from-top-2` (used by Tooltip / Sheet / Dropdown / Dialog) compile to nothing and the corresponding components appear without animation.
4. **Tailwind v4 reverted v3's `currentColor` border default.** A bare `border` utility uses `currentColor` instead of the theme border. Reset explicitly:
   ```css
   @layer base {
     *, ::after, ::before, ::backdrop, ::file-selector-button {
       border-color: hsl(var(--border));
     }
   }
   ```
   Without this, every `border` looks like body text color, and `border-border` only works where it's the explicit utility.
5. **`tailwind.config.ts` should not exist in this repo.** PostCSS plugin ignores it; its presence misleads the next reader. If you add one back, you're working against the grain. Theme, fonts, keyframes, content paths — all of it goes in `globals.css` (`@theme`, `@source` in v4).
6. **`@source` is automatic in v4 for files imported transitively, but explicit for non-imported assets.** Currently we don't need it because every TSX file lands in the import graph; if you ever colocate a CSS file or use a class only emitted from a string template, add `@source "./components/foo.tsx"` to keep it scanned.

## shadcn/ui patterns

1. **Look in `components/ui/` first.** Available primitives: `Button`, `Badge`, `Card`, `Separator`, `ScrollArea`, `Tabs`, `Sheet`, `Skeleton`, `Tooltip`. If a primitive exists, compose it; never hand-roll the same chrome.
2. **Pull new primitives via the CLI**: `npx shadcn@latest add scroll-area tooltip` (etc.). The CLI uses `components.json` for path aliases and writes canonical-shadcn output. Don't paste primitives in by hand — drift from canonical breaks future `shadcn add --overwrite` refreshes.
3. **`asChild` is the canonical pill-as-link pattern.** Use it for nav links so a single `<a>` carries pill geometry, focus ring, and SPA routing simultaneously:
   ```tsx
   <Button asChild variant="ghost" size="sm">
     <Link href="/" aria-label="Back to all workspaces">
       <ArrowLeft />
       <span>All workspaces</span>
     </Link>
   </Button>
   ```
   Never wrap a `<Link>` inside a `<button>`; never wrap a `<button>` inside an `<a>`. The Slot primitive solves both.
4. **All Button variants and sizes flow through `components/ui/button.tsx` (CVA).** New visual treatments add a CVA variant — they don't fork into a sibling component. `size="icon-sm"` (h-9 w-9) is the icon-only-button shape used in the header chrome.
5. **`<Badge>` is the right base for any pill** (status, identity, count). Compose with `className` / inline `style` for color tokens; do NOT introduce a sibling pill primitive.
6. **`<ScrollArea>` everywhere we'd otherwise reach for `overflow-auto`.** Cross-browser-consistent thumb, theme-aware track. Used in `CommitList` and `PeekSnapshot`. The native scrollbar otherwise ranges from invisible (Chrome on macOS) to chunky-and-distracting (Firefox), and theme switches don't propagate.

## Design language (visual contract)

This section is the canonical visual contract for the webapp. Update it in the same commit as any change that affects look or feel.

### Surfaces (elevation tiers)

The four surfaces are deliberate — VS Code-like, four levels stacking from canvas to overlay.

| Token | Use | Class |
|---|---|---|
| Base canvas | Body, page background | `bg-background` |
| Subtle well | Card footer strips, status-bar | `bg-muted/40` (or `/60` for status-bar) |
| Panel | Cards, primary surfaces | `bg-card` |
| Overlay | Tooltips, dropdowns, dialogs | `bg-popover` |

Borders are always `border-border` (theme-aware) unless an indicator is meaningful (e.g. `border-l-2 border-[var(--ref-branch)]` for an active commit row).

### Color language (semantic tokens, not hex)

Defined once in `globals.css`, mirrored at component layer via `[var(--token)]` arbitrary values.

| Token | Hue | Use | Sparingly? |
|---|---|---|---|
| `--primary` | terracotta | Brand only — the logo block, primary CTAs (none currently). | Yes — never use for body chrome. |
| `--ref-branch` | teal | Current branch identifier (mono text + outline borders). | Wherever a branch name appears. |
| `--ref-info` | blue | Agent identifier. | Wherever an agent name appears. |
| `--ref-add` | green | Diff additions, "ahead" stat polarity. | Wherever a positive count is meaningful. |
| `--ref-remove` | red | Diff removals, "behind" stat polarity. | Wherever a negative count is meaningful. |
| `--status-active`, `--status-running` | lime / olive | Lifecycle: ACTIVE, RUNNING. | Status badge + accent stripe + status-bar dot only. |
| `--status-idle` | blue (light) / pale-blue (dark) | Lifecycle: IDLE. | Same. |
| `--status-offline`, `--status-paused` | neutral gray | Lifecycle: OFFLINE / PAUSED. | Same. |
| `--status-orphaned` | amber | Lifecycle: ORPHANED. | Same. |
| `--status-error` | red | Lifecycle: ERROR. | Same. Also the "daemon unreachable" indicator. |

The status palette is **mirrored** from `grove/core/contracts/status_palette.py` — the Python source is canonical. The drift test in `tests/unit/status-tokens.test.ts` reads the Python file at test time and asserts the JS constants match. **If you change a status hex, change it in Python.**

### Typography hierarchy

| Tier | Class | Use |
|---|---|---|
| Display | `text-xl font-semibold tracking-tight` | Page titles (one per page max — workspace title on detail). |
| Title | `text-base font-semibold` | Card titles, prominent stat values. |
| Body | `text-sm` | Body copy, descriptions. |
| Metadata | `text-xs text-muted-foreground` | Relative times, byline copy, secondary info. |
| Section label | `text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground` | Panel section headers ("SUMMARY", "AGENT"). The terminal-feel cue. |
| Micro label | `text-[10px] uppercase tracking-wider` | Stat units (AHEAD / BEHIND / DIRTY under numbers). |

`font-mono` for **every** git/code identifier: branches, base branches, agent names, SHAs, commit subjects (no — bodies stay sans), file paths. The `--font-mono` token is set in `@theme`.

### Spacing

4 px grid. Allowed gap values: `{1, 1.5, 2, 3, 4, 5, 6}`. Allowed padding values: `{2, 3, 4, 6}`. Card padding `p-4` standard, `p-3` compact, `p-6` spacious. Page max-width `max-w-screen-xl`, page padding `p-4`.

### Density (VS Code influence)

| Element | Height | Why |
|---|---|---|
| Status bar | `h-7` (28 px) | Persistent chrome, max info per row. |
| Tab triggers | `h-8` (32 px) | Compact tabs, file-tab feel. |
| Icon-sm button | `h-9 w-9` (36 px) | Header chrome (theme toggle, GitHub link). |
| Default button | `h-10 px-4` (40 px) | Standard interactive minimum. |
| Header | `h-14` (56 px) | Large enough for brand + actions, small enough not to dominate. |

WCAG-mobile recommends 44 px tap targets; we hold the line at 36–40 inside the header (acceptable for chrome) and use 40+ for primary actions (back nav). When in doubt, prefer the larger size.

### Transitions

200 ms ease-out across the board. **Never** `transition-all` — animate only `transform`, `box-shadow`, `colors`, `border-color` selectively to avoid color flicker on text. Card hover = `-translate-y-[1px] + shadow-md + border-t thickening`. Reserved animations: `animate-grove-pulse` for ACTIVE / RUNNING status badges only (4 Hz step pulse — terminal-cursor-like).

### Focus + a11y

- Every interactive element gets `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background`. The Button primitive bakes this in; if you build something that isn't a Button, copy the contract.
- All icon-only buttons need `aria-label`. All decorative icons get `aria-hidden`.
- Polling indicators use `aria-live="polite"`.
- Status colors are an enhancement — the badge **also** carries text labels and glyph characters so colorblind / monochrome users still get the signal.
- Animations honor `motion-reduce:animate-none`.

### App shell (the VS Code analogy)

Three persistent regions across every page:

1. **Header** (`components/layout/header.tsx`) — sticky top, h-14, brand block on the left, actions group on the right (GitHub link + theme toggle), translucent on scroll via `bg-background/80 backdrop-blur-md`.
2. **Main content** — `<main>` with `max-w-screen-xl mx-auto p-4`. Bottom padding accounts for the status bar via the parent wrapper's `pb-7`.
3. **Status bar** (`components/layout/status-bar.tsx`) — fixed bottom, h-7, daemon health (colored dot + "online"/"unreachable") + workspace count + "read-only dashboard" + GitHub link. Information density over chrome.

This shape is deliberate VS Code mimicry. The TUI uses a similar header-content-statusbar layout (see `docs/design-system.md`); web and TUI feel like siblings, not strangers.

## Testing pyramid

```
tests/unit/         — Vitest, pure logic (status palette, ANSI strip, RepoFacet, WorkspaceCardModel, GroveClient)
tests/component/    — Vitest + jsdom + RTL, single-component contracts
tests/e2e/          — Playwright, mobile-chrome + desktop-chrome projects
tests/e2e/_fake-daemon.ts — Hermetic Express server used by Playwright
tests/e2e/_live-smoke.ts  — Manual `npx tsx` against a real daemon for screenshot capture
```

- **Vitest config requires** `esbuild: { jsx: "automatic", jsxImportSource: "react" }` — without it the JSX runtime path errors on every component test. **`vitest.setup.ts` polyfills `matchMedia`** because next-themes calls it on mount and jsdom doesn't provide it.
- **jsdom serializes hex colors to `rgb()`.** Tests that assert on color use the `expectColor()` helper (in `tests/_helpers/`) which converts hex → rgb before comparing. Don't compare raw style strings.
- **Test seam = the component's testid + ARIA + visible text.** Tests pin `data-testid="status-badge"`, `data-testid="stat-trio"`, `data-testid="commit-list"`, `data-testid="workspace-card"`, `data-testid="identity-panel" / "summary-panel" / "agent-panel"`. New visual treatments must preserve those testids and the first-child structure that tests depend on (e.g. `StatusBadge`'s firstChild is the pulse-eligible glyph). Update both in the same commit if the contract truly changed.
- **Playwright uses port 3101**, NOT 3100. Some machines have a root-owned next-server bound to 3100 from another project; we side-step it. The fake daemon binds 8421.
- **`E2E_LIVE_DAEMON=1 npm run test:e2e`** runs Playwright against a real daemon instead of the fake (only useful when iterating on something the fake doesn't fully model — keep it the exception).

## Production hosting (systemd-user)

For "leave it running on a host" deployments — typically the same machine that hosts the daemon — install the user-scope systemd unit shipped under [`packaging/systemd/`](../packaging/systemd):

```bash
make webapp-build           # one-shot: npm ci + npm run build (required before enabling)
WITH_WEBAPP=1 make systemd  # writes both unit files
WITH_WEBAPP=1 make systemd-enable  # enable + start now
```

Webapp install is **opt-in** (default `WITH_WEBAPP=` is empty). The webapp service `Wants=` (not `Requires=`) the daemon — daemon hiccups don't tear the webapp down, the in-app status bar surfaces the unreachable state instead. The unit binds `0.0.0.0:3000` so the dashboard is reachable from a phone on the same Wi-Fi without extra port forwarding.

After webapp source changes: `make webapp-build && systemctl --user restart grove-webapp`. The service does NOT auto-rebuild — production mode reads `.next/` build output and runs cold-fast, dev mode's hot-reload would be wrong here.

## Local dev / tmux orchestration

The standard layout is a tmux session named `grove-webapp-dev` with two windows:

```
window 0: daemon  → uv run grove daemon serve --port 7421
window 1: webapp  → npm run dev   (binds 0.0.0.0:3000, LAN-reachable)
```

LAN URL is announced in the dev log (`http://192.168.x.x:3000`). Phone testing: open the LAN URL on your phone while connected to the same Wi-Fi.

**When `next dev` errors with `Cannot find module './<chunk>.js'` (e.g. `611.js`):** the `.next` cache is stale. Fix is `rm -rf .next && npm run dev` (NOT `--clear-cache`, NOT `npm install`). Same shape applies to a `_not-found` collection error during `next build` — clean cache, rebuild. **Do not** `rm -rf .next` while a `next dev` is running; it corrupts in-flight pack files and leaves the dev server in a degraded state. Stop dev → clear → start dev.

## Repo-level gotchas (specific to webapp's location in the monorepo)

- **`/webapp/lib/` was previously caught by the repo-root `.gitignore` rule for Python `lib/`**. Resolved by adding `!/webapp/lib` and `!/webapp/lib/**` negations to repo `.gitignore`. If a new top-level dir under `webapp/` shares a name with a Python directory we ignore (e.g. `dist/`, `build/`), apply the same negation pattern.
- **The webapp does NOT depend on the Python venv**. It has its own `node_modules` and `package.json`. CI matrices that build webapp can skip the Python toolchain.

## Session lessons (non-trivial)

Things that took real time to figure out. Capture *why* and the *invariant*, not line numbers — when code moves, update the bullet, don't delete it.

- **Tailwind v4 silently no-ops every shadcn color utility without an `@theme` block.** Compiled CSS for `bg-card`, `bg-primary`, `text-foreground`, `border-border`, `ring-ring`, `bg-muted`, `bg-accent`, etc. is empty. Body still has a background only because of the explicit `@layer base { body { background-color: hsl(var(--background)); } }` rule. The visible result is a layout that "looks fine but feels off" — surfaces blur into one another, no card boundaries, no muted strips, no focus rings, no hover affordances. The `tailwind.config.ts` JS theme is **dead** in v4 — moving theme tokens, fonts, and keyframes into `@theme` in `globals.css` is the only path. Verification probe: `curl /_next/static/css/app/layout.css | grep -c '\.bg-card'` returns `>0`. A clean build is not enough.
- **`tailwindcss-animate` (v3 plugin) is replaced by `tw-animate-css` in v4.** Without `@import "tw-animate-css";` in `globals.css`, radix component animations (Tooltip enter, Sheet slide, Dialog fade, Dropdown zoom) all silently no-op. The components still mount and dismiss correctly — they just teleport instead of animating, which makes the app feel cheap without any clear cause.
- **`animate-grove-pulse` (custom keyframe) lived in `tailwind.config.ts` for months and never compiled.** Same root cause as the `@theme` issue. Custom keyframes for v4 go in `@theme` via `--animate-<name>: <name> ...; @keyframes <name> { ... }`. No JS config layer participates in the build.
- **`shadcn add` writes to `components/ui/` based on `components.json`'s alias config.** Verify the file exists and has `aliases.{components,ui,utils}` before relying on the CLI. Without it, `shadcn add` errors with a misleading "couldn't read config" message; the fix is to keep `components.json` checked in even though it looks like CLI scaffolding.
- **Compose shadcn primitives — never hand-roll a sibling pill / divider / scrollbar.** Bespoke chrome was the cause of "no design language" feedback. The signal that it's time to delete custom code: the new file replicates the geometry of an existing `Button` / `Badge` / `Separator` / `ScrollArea` with no semantic difference. Use the canonical shadcn primitive with `className` overrides; reach for a new primitive only when truly novel (and even then, prefer adding a CVA variant to an existing one).
- **Use `asChild` to make a `<Link>` *be* a Button**, not a Button *containing* a Link. Slot replaces the wrapper element with the child while merging className + props + ref. The result: pill geometry + focus ring + SPA routing in a single anchor tag. Common mistake: nesting `<Link>` inside `<button>` (HTML invalid — interactive content nested in interactive content) or wrapping `<Button>` in `<Link>` (focus ring wraps the wrong element).
- **`next-themes` calls `window.matchMedia` on mount.** Vitest with jsdom doesn't ship the API. Polyfill in `vitest.setup.ts`:
  ```ts
  Object.defineProperty(window, "matchMedia", {
    value: (q: string) => ({
      matches: false, media: q, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  ```
  Without it, every component that imports something that imports `next-themes` (transitively, almost everything) errors during render.
- **jsdom serializes `style={{ color: "#abc"" }}` to `rgb(...)`** at the DOM layer. RTL assertions like `expect(el).toHaveStyle({ color: "#aabbcc" })` fail because the DOM serialized to `rgb(170, 187, 204)`. Helper `expectColor()` in `tests/_helpers/` converts hex → rgb before comparing. Don't compare raw style strings.
- **If you ever need fan-out fetches again, `useQueries` is the shape** — an array of query objects in stable order, paired with `useMemo` for the id list so the keys don't churn. A loop of `useQuery` violates the rules-of-hooks (hook count changes with the workspace list). The home grid USED this for per-card peeks and no longer does (see the home-grid-on-stream lesson); the detail page never fans out.
- **`peek` polls at 2 s, `commits` at 15 s, `workspaces` at 5 s; the activity poll fallback is 4 s.** Cadences are tuned to how fast each resource changes: agent panes update second-by-second, commits arrive minutes apart, workspace lifecycle changes are user-driven. The list surfaces don't poll at all on the happy path — they ride the SSE stream.
- **Don't hand-edit `lib/grove/types.gen.ts`.** It's regenerated by `npm run codegen` from the daemon's `/openapi.json`. CI runs `npm run codegen:check` and fails if the file drifts from the live OpenAPI. To add a field, change the daemon's Pydantic view in `grove/core/contracts/views.py`, run codegen, commit both files in the same PR. Engine first; webapp follows.
- **`peek.recent_commits` ≠ `commits()`.** The peek's recent_commits is a tight 3-row rail-shaped summary (no fork-point filter, capped). The detail page's commit list uses the `/workspaces/{id}/commits` endpoint, which is fork-point-filtered (`git log base..branch`) and uncapped. Don't conflate them — both exist on purpose and have different consumers.
- **Strip ANSI escapes in `peek-snapshot.tsx` via `stripAnsi` (in `lib/grove/ansi.ts`)**, don't render them as text. The daemon emits `tmux capture-pane -e` output with SGR codes intact; the TUI renders those via Rich, but the webapp's monospace `<pre>` would display them as literal `\x1b[31m` text. Color is not load-bearing for the dashboard — structure is.
- **Auto-scroll `<pre>` to bottom only when the user is already within 40 px of the bottom.** Otherwise we fight a deliberate scroll-up. Same pattern used in chat/log UIs everywhere; codified in `components/workspace/peek-snapshot.tsx`.
- **`<ScrollArea>` is a wrapper around `overflow-hidden` + a viewport** — its child's scroll happens on the viewport, not the root. Don't put `overflow-auto` on a child you've already wrapped in `<ScrollArea>`; you'll end up with two scrollbars.
- **The Next.js `_not-found` static collection error during `next build` is a stale `.next` symptom**, same family as `Cannot find module './611.js'` during dev. Fix: clean `.next`, rebuild. Do **not** clean `.next` while `next dev` is running (corrupts in-flight pack files; the dev server enters a permanent error loop until restart).
- **Port 3100 is contested on dev machines** — the workspace picked up a root-owned `next-server` from another project that we couldn't kill. Playwright uses 3101 instead. If 3101 ever clashes, increment further; do **not** add complex port-discovery logic — the fixed port is the contract that makes the fake-daemon webserver pair deterministic.
- **`.gitignore` negation**: the repo-root rule `lib/` (Python) catches `webapp/lib/`. Solved by `!/webapp/lib` + `!/webapp/lib/**` in root `.gitignore`. Same approach if a new ignored Python directory name (`build/`, `dist/`) shows up under `webapp/` later.
- **Dev mode bottom-left "N" / "Issues" badge** is the Next.js dev indicator. Production builds don't render it. Stop chasing it in screenshots — it's not part of the design.
- **VS Code-style status bar is a *single* persistent footer**, not per-page. Lives in `app/layout.tsx` so it appears regardless of route. Subscribes to the workspace list directly via `useWorkspaces()` — the polling cadence is shared with the rest of the app via TanStack Query's cache, so it costs nothing extra. Padding-bottom on the wrapper (`pb-7`) reserves the strip's footprint so content doesn't disappear underneath.
- **Color contrast is the failure mode of "color the label AND the surface".** Original status pill colored both the badge text and the badge background with the same status hue, which produced AA-borderline contrast in light mode (lime green text on near-white tinted surface). Decoupling glyph color from text color (text = `text-foreground`, glyph = status hue) fixes contrast while keeping the status cue. Apply the same rule any time a "tinted pill" lands in the design.
- **The fix for "the design feels inconsistent" is rarely more design.** It was almost always missing utilities, missing focus rings, hand-rolled chrome that drifted from a primitive that already existed, or a hex token that was never wired to a Tailwind class. Audit is `curl /CSS | grep -c utility-name` first, screenshot diff second, design talk last.
- **`placement: "worktree" | "root"` rides on `WorkspaceStateView` (and its embedded `state` inside `WorkspacePeekView`), surfaced by `PlacementBadge`.** Root workspaces run in the repo root with no isolated worktree; worktree is the default and common case. The badge is silence-by-default: `PlacementBadge` returns `null` for worktree (nothing to flag) and renders a tasteful neutral-outline "root" pill (FolderRoot glyph) only for root. It sits next to `StatusBadge` in both the card header and the detail identity panel, wrapped in a `flex gap-1.5` group. Neutral chrome on purpose: placement is orthogonal to lifecycle status, so it must not borrow a `--status-*` hue. Test seam mirrors `StatusBadge`: `data-testid="placement-badge"` + `data-placement` + the visible "root" label, and returning `null` makes absence assertable via `queryByTestId(...).toBeNull()`. Codegen wrinkle worth remembering: a live daemon was not reachable and `uv run` is broken here, but the schema needs no network. Import the app and dump OpenAPI to a file (`.venv/bin/python -c "...; build_app(cfg=GroveConfig.model_validate({'auth':{'enabled':False}}), store=JsonWorkspaceStore()).openapi()"` — store is `grove.core.store`, factory needs `cfg`/`store` kwargs), then point `openapi-typescript` at the JSON file instead of a URL. Same generated output as the live pipeline, so `codegen:check` stays green.
- **The agent terminal preview owns its scroll viewport via `h-full min-h-[28rem]`, NOT a fixed cap.** First cut of the detail page (`/w/[id]`) used `h-[min(60vh,28rem)]` directly on `PeekSnapshot`'s ScrollArea — the same pattern showed up on `CommitList`'s ScrollArea. Locked at ~448px on every viewport, leaving 400-500px of empty page below the panels on any 1080p+ display. Fix shape, codified now: (1) the page becomes a flex column with `lg:min-h-[calc(100dvh-5.25rem)]` (5.25rem = h-14 header + h-7 status-bar reserved by `pb-7`); (2) the side-by-side row is `lg:flex-1 lg:auto-rows-fr lg:grid-cols-12` so both columns stretch to the remaining viewport; (3) each card inside the row is `flex flex-col`, its CardContent is `flex min-h-0 flex-1 flex-col` (the `min-h-0` is load-bearing — without it, flex children with `overflow:auto` can't shrink below content and the scroll viewport breaks); (4) the leaf scroll components (`PeekSnapshot`, `CommitList`) default to `h-full` with a generous `min-h-[Xrem]` floor for mobile / short viewports. The leaf components do NOT bake in viewport caps — that's policy-at-the-page, not mechanism-at-the-leaf, and it's why one consumer can flex-fill while another (a future modal preview, etc.) can pass a `className` to constrain. Generalizes: any "fill the viewport" pattern in this codebase = flex column on the page + `flex-1 + min-h-0` on the row + `h-full + min-h-floor` on the leaf. The `min-h-0` trap is the part everyone forgets.
- **The Activity Dashboard (`/activity`) streams over SSE through the BFF, with a poll fallback — and adds NO new npm deps.** The epic floated `fancy-ansi` + `@xterm/xterm`; we shipped neither. Adding a dep needs `npm install` (network) and the codebase's own convention is `stripAnsi` + `<pre>` ("color is not load-bearing; structure is"), so the dashboard card is a *glance* tile that links to `/w/[id]` for the live pane (which already renders via `PeekSnapshot`); the dedicated streaming pane wall is #19. (1) **SSE pass-through:** `app/api/grove/[...path]/route.ts` special-cases `GET /events` — it pipes `upstream.body` (not `await res.text()`), sets `export const runtime = "nodejs"` + `dynamic = "force-dynamic"`, forwards `req.signal` (a browser disconnect aborts the upstream fetch — no `ResponseAborted` leak) and the `last-event-id` header (replay), and returns `cache-control: no-cache, no-transform` + `x-accel-buffering: no`. The browser uses a **cookie-auth `EventSource`**; the daemon bearer is injected server-side and never reaches the browser. (2) **`useActivityStream` = EventSource primary + `/activity` poll fallback** (`useQuery` `enabled: !connected`, so the happy path is one stream and zero polls). jsdom has no `EventSource`, so the hook guards `typeof EventSource === "undefined"` and falls back to the poll — which is how the component/unit tests exercise it. The fold logic is the pure `applyDashboardEvent` reducer in `lib/grove/activity-stream.ts` (`snapshot` → replace, `session_activity` → patch the one workspace + recompute counts, else no-op); that's the unit-test seam, not the hook. (3) **Agent-state palette is a separate axis from workspace status**, mirrored like `status-tokens`: `lib/grove/agent-state-tokens.ts` hex mirrors `grove.core.contracts.agent_palette.py` (drift-tested in `tests/unit/agent-state-tokens.test.ts`), glyphs/labels mirror `grove/tui/_status.py`, and `globals.css` carries `--agent-*` CSS vars (light + dark) the card reads via `var(--agent-<state>)`. Lens default is `"all"` (mirror the TUI — never open to an empty wall). (4) **e2e mirrors `home.spec`** (the fake daemon gained `/activity` + an SSE `/events` stub) and shares its auth-cookie setup — it can't run in a sandbox without a paired session, same as every existing spec.
- **The live preview is ANSI→HTML in a real `<pre>` (`components/terminal/terminal-view.tsx` via `fancy-ansi`), NOT a terminal emulator.** First cut used `@xterm/xterm`; it regressed the rendering three ways and was reverted. The daemon sends `tmux capture-pane -e` — a **fixed character grid** with SGR color escapes and *no cursor motion*. xterm is a terminal *emulator*: (1) it **reflows at its column count**, so a capture wider than the fitted terminal **word-wraps** (the user's "lines wrap" / "lines too long"); (2) its **DOM renderer draws box-drawing glyphs disconnected** ("stitched-up dashes" on `─│┼` — crisp connected lines need the WebGL/Canvas renderer's custom glyphs, which the DOM renderer doesn't do); (3) `fontFamily` can't take a CSS `var()`, so `var(--font-mono)` silently fell back to xterm's default font ("inconsistent font"). The faithful tool for a *snapshot* is `new FancyAnsi().toHtml(ansi)` into a `<pre className="font-mono whitespace-pre">`: the page's real monospace font joins box-drawing into clean lines, `whitespace-pre` never wraps (the wrapper is `overflow-auto`, so a wide grid **scrolls horizontally** like it did before xterm), and color is rendered via `fancy-ansi`'s `<span style="color:var(--ansi-*, #fallback)">` runs. `fancy-ansi` HTML-escapes the text and only injects color spans, so the `<pre>`'s `textContent` is still the plain stripped output — the screen-reader text + the unit/e2e seam (`peek-snapshot`'s exact `textContent`/`data-taken-at`/`font-mono`, `focused-pane`'s `toContainText`). Pure string→string, no DOM, so it works in SSR/jsdom with **no** dynamic-import/canvas dance. **xterm.js is the right tool only for a true live PTY** (the deferred ttyd-style websocket attach) — not for a polled grid; don't reach back for it on the snapshot path. Both `FocusedPane` (dashboard) and `PeekSnapshot` (detail) compose `TerminalView`, so web and TUI stay siblings. Dep: `fancy-ansi` (in `package.json` + lockfile; `make webapp-build`/`npm ci` needs it).
- **One consolidated filter (`components/dashboard/dashboard-filter.tsx`, a radix `Popover`) replaces the three lens tabs — projects × agent-states × attention, with live counts.** The "click Active, see nothing" confusion came from an opaque lens that filtered on *agent* state while the user read tmux-active workspaces as "active". The filter is stored as **hidden sets** (`DashboardFilterState = {hiddenProjects, hiddenStates, attentionOnly}` in `dashboard-filter.ts`), so the **empty default shows everything** AND a state/project that streams in *later* is visible by default — a deselected row is an explicit choice, never a stale snapshot of "what existed when the menu opened". Every row shows a live count from `computeFacets(snapshot)`, so `working: 0` is self-explanatory. `filterSnapshot(snapshot, filter)` is the pure apply (drops emptied groups); `displayState(w)` = `sessions[0]?.activity.state ?? "unknown"` is the single state a workspace filters/labels by. The lens helpers (`passesLens`/`groupsForLens`/`LENSES`) are **kept** in `dashboard-filter.ts` (still unit-pinned) — the page just stopped using them. Filter UI is e2e-covered (`filter-attention`/`filter-state-*`/`filter-project-*`/`filter-reset` testids); the logic is unit-covered. Don't add a vitest component test that opens the popover — radix's pointer-capture/portal needs jsdom mocks and is flaky; test the reducer, not the menu.
- **SessionCard is a four-band layout, roomy by default (grid maxes at 3 columns), and degrades when there's no agent session.** Cramming the card into a 4-col grid read as "scribble on an A4 sheet". Bands: header (state glyph · title-link · branch·agent·model, with status/placement/age right-aligned), agent-state + Live toggle, the agent's self-summary (`interpreted_status ?? title ?? current_task`), and a **labeled metric grid** (`Changes` `Base` `Turns` `Tokens` with micro-labels over mono values) — labels-over-values reads as structure where a cramped inline string reads as noise. A workspace with no session (`sessions: []`) still fills every workspace-level field and shows "no agent session yet" + `—` metrics rather than collapsing. Keep the seams: `data-testid="session-card"`+`data-agent-state`, `agent-state-label`, `live-toggle` (WORKING-gated), the `border-l-2` attention accent, and the title `<Link>`.
- **Agent brand logos go through one extensible registry (`lib/grove/agent-icon.tsx`), never hand-authored SVGs.** Verified 2026-06-08: `simple-icons` ships only `siClaude` / `siAnthropic` off-the-shelf; OpenAI, Codex, and OpenCode have NO brand icon in simple-icons OR lucide (lucide carries zero brands by policy). So they fall back to a lucide glyph (`Bot` / `Terminal`) behind the same `AGENT_ICON` map — adding a real glyph later, or a new agent, is a one-line map edit. `resolveAgentIcon(agentName, adapterKind)` matches `adapterKind` first, then an `agentName` substring. This map mirrors the backend's `AgentAdapter` registry: same "normalize the shape at the boundary, stay extensible" seam, on the client. `simple-icons` exports `SimpleIcon` as a *type*, so the wrapper re-exports it as `SimpleIconData` to free the component name. lucide note: it's `GitCommitHorizontal` (and `LoaderCircle`), NOT `GitCommit`, in 0.460. Brand marks are rendered **static** (a spinning logo reads wrong); liveness is a separate `animate-ping` ring around the still glyph.
- **The colour/transparency philosophy lives in ONE module (`lib/grove/activity-tier.ts`) so rendering and sorting can't drift.** Three tiers: *active* (working, or — with no agent session — tmux `ACTIVE`) = full opacity + animated `AgentBadge` ring; *attention* (waiting / blocked / error) = full opacity + highlighted accent, **never dimmed** (attention must pop); *dormant* (idle / offline / unknown / starting / sessionless-and-not-active) = dimmed, where **lower opacity encodes a lower active tier**. The **tmux fallback** is here: `tierForActivity(null, workspaceStatus)` derives the tier from `WorkspaceStatus` so a generic/non-Claude agent still reads working/idle from tmux. The same module exports `activityRank` — the sort key the dashboard uses to float running sessions to the front — so "what tier is this card" and "does it sort first" are the same source of truth.
- **Dashboard reactivity: filter persisted to `localStorage`, refresh-now reconnects the stream, active floats to front with `@formkit/auto-animate`.** Filter persistence (`lib/grove/filter-persistence.ts`, Sets↔arrays, `typeof window` guarded) survives reload AND refresh-now; the hydration-safe pattern is init `emptyFilter()` on server + first client render, `loadFilter()` in an effect behind a `hydrated` flag, and persist-on-change gated by that flag so the empty default never clobbers saved state. Refresh-now bumps a `reconnectKey` (a dep of the `EventSource` effect) → the stream tears down and the daemon resends a fresh `snapshot`, plus the poll-fallback query invalidates; `lastEventAt` (updated on *every* SSE event incl. `heartbeat`) is the dashboard-wide "last refresh" indicator, while per-card "updated Xs ago" is the wire's `observed_at`. `sortSnapshotByActivity` (pure, unit-tested) orders workspaces by `activityRank` within a project and projects by their min rank; `useAutoAnimate` (chosen over framer-motion purely for footprint — ~3 kb, honours `prefers-reduced-motion`) animates the reorder. One `useAutoAnimate` ref per grid via a small `ProjectGroupSection` subcomponent — never create refs in a loop (rules-of-hooks).
- **The dashboard's live focus is exactly ONE polled pane (#19), never N — page-level `liveId` is the enforcement.** `app/activity/page.tsx` holds a single `liveId`; only the focused card's `useWorkspacePane(id, enabled)` is enabled (`refetchInterval: 1_000`), so the wall pays for one `GET /workspaces/{id}/pane` poll, not one per card. The `<FocusedPane>` panel renders above the grid, decoding via `stripAnsi` + `<pre>` (same "structure not color" convention as `PeekSnapshot`); the **xterm.js colored-terminal upgrade is the further enhancement, deferred because it needs a network `npm install`**. The "Live" toggle on a card is **status-gated to WORKING** (`agentState === "working"`) and the page auto-drops `liveId` when that workspace stops working or vanishes from the snapshot (the `useMemo` over `live`), so a stale live view self-heals on the next tick. SessionCard is a `<div>` (not a `<Link>` wrapper) so the title-link and the Live `<button>` can coexist without nesting interactive elements — the e2e clicks `getByRole("link", {name})` for the title, `getByTestId("live-toggle")` for the pane.
- **Session drill-down is an inline expansion on `/w/[id]`, not a route — and mounting IS the fetch trigger.** `SessionsPanel` (list, panel-local "one expanded row" state) and `TurnsView` (the digest) are separate components because they sit on different fetch tiers: the session list rides the history tier (15 s, same as commits — sessions change when runs start/end, minutes apart), while turns are fetched **only when a row expands** — `useSessionTurns(id, sessionId | null)` is `enabled`-gated, and TurnsView only mounts for the expanded row, so a collapsed panel costs zero transcript reads. Turns never poll fast (30 s): the digest is a full transcript parse — the heaviest read here — and the live "what's happening now" signal already comes from peek/activity; a hot cadence would re-parse transcripts nobody is reading. Test seams: `sessions-panel`, `session-row`+`data-session-id`, `session-provenance`, `turns-view`, `turn-row`, `turn-entry`+`data-role`. Unlike PeekSnapshot/CommitList, TurnsView **does** bake in a `max-h-96` — it's an inline expansion inside a list, where unbounded height shoves later rows off-screen; the "policy-at-the-page" rule applies to viewport-fill leaves, not inline accordions.
- **Provenance gets quiet human labels at the render edge, raw codes elsewhere.** The wire's `provenance` is an open string (`grove_launched`, `fs_discovered`, …); the panel maps the two known codes to "grove" / "hand-started" and passes unknown codes through raw — same fallback philosophy as the enum-keyed color maps. Keep the mapping in the consuming component until a second consumer appears; don't pre-build a registry for two strings.
- **Token/treatment lookups fall back, and one bad card can't white-screen the wall — streamed data is untrusted at runtime.** An SSE delta is JSON; the TS unions do NOT constrain it, so a daemon that ships a new `WorkspaceStatus`/`AgentActivityState` makes `MAP[value]` `undefined` and the first `.field`/`.tier`/`.tone`-style read throws — and an uncaught render throw unmounts the **whole** React tree (the "it crashes everything" white-screen). Two defenses, both required: (1) every color/treatment lookup ends in `?? <neutral>` (`statusColor`→offline, `agentStateColor`→unknown, `tierForActivity`→unknown/dormant) so it never returns `undefined`; (2) each dashboard card is wrapped in `components/error-boundary.tsx` (`ErrorBoundary` — transparent on the happy path: it renders `children` with no wrapper element, so the parent's `useAutoAnimate` ref still sees the real card as its direct child), so any uncontained throw degrades to one placeholder tile. Pinned by `tests/unit/render-hardening.test.ts`. Rule for any new enum-keyed `Record` consumed from the wire: give the accessor a fallback, never index-and-deref raw.
- **The home grid rides the activity stream — the N+1 peek fan-out is deleted (2026-06-11).** The old shape (`useWorkspaces` at 5 s + `useWorkspacesPeeks` per-id at 5 s) cost N+1 requests per tick and made a new workspace take up to 10 s to appear; `useActivityStream`'s `DashboardSnapshotView` already carries everything the card shows (`base_ahead`/`base_behind`/`dirty_files` per `WorkspaceActivityView`) with <1 s deltas. `WorkspaceCardModel.fromActivity` is the card's normalizing factory; `RepoFacet.groupActivityByRepo` is the adapter entry (groups on the embedded state, callers re-pair by id) so grouping stays defined once. Trade-off accepted knowingly: the fan-out warmed the `["peek", id]` cache for grid→detail navigation; losing it means the detail page cold-fetches its peek — at a 2 s poll cadence that's a ≤2 s gap, not worth N+1 steady-state requests. One route renders at a time, so per-page `useActivityStream` = at most one EventSource; no shared provider needed.
- **An `EventSource` in a background tab can die silently — the stream needs its own focus self-heal.** TanStack queries refetch on window focus for free, but SSE has no such reflex: browser tab-throttling / network sleep can kill the connection *without firing `onerror`*, so `connected` stays true and the poll fallback never arms — a wall that looks live but is frozen. Fix in `useActivityStream`: a `visibilitychange` listener compares `lastEventAt` (stamped on every event incl. the 15 s heartbeat, mirrored into a ref so the listener never re-subscribes) against a 20 s staleness bound and bumps `reconnectKey`; the fresh connection makes the daemon resend a full `snapshot`. Any future SSE hook needs the same triad: heartbeat stamp + visibility check + reconnect nonce.
- **`activity-stream.ts` = stream contract + pure reducer; `dashboard-filter.ts` = wall-presentation policy.** The file had accumulated two answers to two questions: "how do SSE events fold into a snapshot" (`applyDashboardEvent`, transport-coupled, changes with the daemon's event contract) and "which workspaces show, in what order, under what label" (`DashboardFilterState`/`computeFacets`/`filterSnapshot`/`displayState`/`sortSnapshotByActivity` + legacy lenses, pure policy, changes with UX). They change for different reasons, so they live apart now; the test files split the same way (`activity-stream.test.ts` pins the reducer, `dashboard-filter.test.ts` pins the policy, shared wire-shaped fixtures in `tests/_helpers/activity-fixtures.ts`).
