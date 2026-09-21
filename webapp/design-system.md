# The Grove web design system

> ↑ owner: [webapp](CLAUDE.md) · [root](../CLAUDE.md) · the TUI's own visual contract is [docs/design-system.md](../docs/design-system.md) — a different surface, deliberately not merged with this one.

The source of truth for what this front end looks like. Every rule here names a **token or a class**. If you finish a section still asking "which grey?", the section is broken — say so and fix it, do not guess.

You should be able to build a new screen from this file without asking anyone. That is the acceptance criterion.

---

## assistant-ui is the framework AND the theme. This document complies with it.

**assistant-ui (and the shadcn primitives it builds on) is the exclusive UI framework and theme for this app.** This document does not sit on top of it as a competing system — it is subordinate to it, and every rule below is one of exactly three kinds. When you add a rule, say which kind it is.

| Kind | Meaning | How to spot it |
|---|---|---|
| **RECORD** | The vendored layer already decided. The doc writes the decision down and names the token so nobody re-derives it. | The rule names a token or variant that already exists |
| **ADD** | The vendored layer has no opinion here, so Grove defines a token in `globals.css` — and the doc says *why* it was not covered. | The token is Grove's own, and there is a stated reason |
| **OVERRIDE** | The vendored layer decided, and we deliberately decide differently. Requires a reason and applies globally, never per call site. | Rare. Currently exactly one. |

**The default is RECORD.** If you can express a rule with an existing token or variant, you must. Inventing a token that duplicates a vendored one is the failure this whole app exists to prevent, and no gate catches it.

### Everything Grove has ADDed, and why the vendored layer did not cover it

| Token | Why it is not vendored |
|---|---|
| `--success` / `--success-foreground` | shadcn ships `destructive` and no positive counterpart. Without it, every "good" signal reaches for a raw palette colour, which `lint:styling` forbids. |
| `--merged` | The semantic set has failed and healthy; a forge has a third outcome. A pull request **closed by landing** is neither, and every tracker draws it purple — `destructive` would say the merge failed and `success` would make merged and open indistinguishable, which is the one comparison the Tickets card exists for. No `--merged-foreground`: it is a MARK colour, never a fill, so nothing ever needs a legible colour on top of it. **Draft needed no token at all** — an absence of activity is what `--content-tertiary` already means, and adding a second grey would have been a token that duplicates one. |
| `--heat-0…4` | A colour *scale* is theme data; `heat-graph` takes it as CSS colour values and hard-codes blue otherwise. Dark mode needs a different ramp and a component cannot say that. |
| `--content-secondary` | shadcn ships exactly two neutrals. There is no third, and three tiers is the minimum that ranks title / supporting / metadata. Primary and tertiary are **aliases**, not new values — only the middle step is genuinely new. |
| `--text-*` values | The *names* are Tailwind's; only the values are ours. Redefining them is the only mechanism that resizes vendored components, which cannot be edited. |
| `--surface-sunken/base/raised/overlay` | shadcn ships `--background`, `--card` and `--popover` as three names that in light mode are **all the identical pure white** (measured 1.00:1). Three names for one surface, and **no way down at all** — there was nothing here to record. |
| `--surface-edge`, `--edge-control` | The boundary has to be a token of its own once a level is a tuple rather than a colour, and `--border` is a single value that cannot be both a decorative edge and a 3:1 control edge. |
| `shell-panel`, `surface-raised` | Measured off the base demo, which draws these with no component at all. A `Card` would invert the elevation cue in dark mode. **Both are now rungs** — see §4.8. |

### The one OVERRIDE

**`cursor: pointer` on enabled controls.** Tailwind v4 dropped its Preflight `cursor: pointer` on buttons and shadcn followed, so the assistant-ui reference demo has the same arrow cursor over its own controls. **This is not a gap against the reference — it is a place our rule is stricter than theirs**, because "if something is clickable, it looks clickable" is a Grove rule the reference never adopted.

That makes it exactly what an OVERRIDE is for: sanctioned once, globally, in `globals.css`'s base layer over `button:not(:disabled)`, `[role="button"]:not([aria-disabled="true"])`, `label[for]` and `summary`. It is scoped to *enabled* controls, because a disabled control is not clickable.

It is **not** a per-call-site `cursor-pointer`: `components/grove/**` carrying it would be Grove code inventing a look, and it would be forgotten the first time someone adds a control. Verified on the running app after it landed: every clickable element reports `pointer` (`button` ×6, `a` ×4, `tab` ×8, zero `default`), and the only remaining `default` is on a disabled dialog button, which is correct.

### Where a vendored decision stands even though we would have chosen otherwise

- **`elements/surfaces.tsx` exports `mono = "font-mono text-[11px] tracking-tight"`** — below this document's 12px floor, in an arbitrary size. **The vendored value stands where it already ships**; Grove code must not introduce new type below 12px, and must not copy the pattern.
- **The vendored markdown sets inline code to `text-[0.85em]`.** Em-relative, so it tracks its container. When the transcript adopts `text-base` (14px), inline code lands at **11.9px**. Accepted: it is monospace with a large x-height, and the alternative is editing a vendored file. Measured at 13.6px today because the transcript inherits the 16px root default.
- **`elements/` expresses hierarchy as alpha on `--foreground`** (`/0.3`, `/0.4`, `/0.5`, `/0.55`, `/0.9`). **This document wins for Grove code** — use the three tiers. Do not "match" the alpha ramp to blend in: an alpha neutral changes meaning against every background it lands on, and the tokens are theme-correct in both modes.
- **`ui/progress` never forwards `value` to the Radix root**, so it is `aria-valuenow`-less and reads as indeterminate. The caller supplies `aria-valuenow` itself. Assume nothing about a vendored component's a11y from the fact that it looks right.
- **The terminal's ANSI colour is CONTENT, but it was never un-themeable — the premise under the old exemption was wrong.** This document said `fancy-ansi` "injects it as inline styles", which would indeed put it beyond reach of any stylesheet. It does not: the shipped `dist` emits `var(--ansi-red)` and friends with defaults, and documents a `.dark` override. Verified in `dist/main.js`, not only in the README. So the theme supplies the 16-colour palette (see §4.6's terminal block) while the *meaning* stays the data's — red is still whatever the agent said was red. **An exemption justified by a mechanism dies when the mechanism turns out not to exist; check the `dist` before writing one down.**

---

## The only lever this app has

`components/{assistant-ui,elements,ui,icons}/` is vendored verbatim and `registry:check` fails any edit to it. `lint:styling` forbids colour, radius and shadow utilities under `components/grove/`. So there are exactly two ways to change how this app looks:

1. **Change a token in `app/globals.css`.** This reaches the vendored layer, because vendored components are written in the same utility classes the tokens sit behind. Redefining `--text-sm` resizes every vendored button, badge and table cell without touching one of their files.
2. **Change the composition** — which component, which variant, which class the *call site* passes.

There is no third way. **If you catch yourself about to open a file under `components/ui/` to make something look right, you have misdiagnosed the problem.** The fix is a token or a call site.

The corollary matters for reading this document: a rule below is only real if it can be stated as a token value or a class on a Grove-owned element. Where one cannot be, it is listed under [where a vendored decision stands](#where-a-vendored-decision-stands-even-though-we-would-have-chosen-otherwise) instead of pretending.

---

## The audit that produced these rules

Measured **2026-08-11** against the running app — computed styles in the browser, not the stylesheet, because what ships is the only thing that counts — and against the tree at `feat/webapp-consolidation`. Every rule below cites the number that forced it.

**The sample is every surface, not the shell.** Nine routes plus the five work-panel tabs and the create dialog, each measured in its own right:

| Surface | text-bearing elements | distinct size/weight pairs | Build |
|---|---|---|---|
| Fleet `/` | ~40 | 5 | pre-ramp |
| Create dialog | 53 | 5 | post-ramp |
| Sessions `/sessions` | 451 | 5 | pre-ramp |
| Session detail `/sessions/[id]` | 4107 | 12 | post-ramp |
| Workspace transcript `/w/[id]` | ~4000 | **16** | pre-ramp |
| Work · Terminal | 103 | 3 | post-ramp |
| Work · Changes | 21 | 4 | post-ramp |
| Work · Files | 14 | 4 | post-ramp |
| Work · Info | 41 | 4 | post-ramp |
| Work · Controls | 34 | 3 | post-ramp |
| Usage `/usage` | 814 | 6 | pre-ramp |
| Login `/login` | 8 | 4 | pre-ramp |

"Pre-ramp" rows were measured before the type ramp below landed (body reads 14px); "post-ramp" after (body reads 13px). The two are not comparable on size, which is exactly why they are labelled — the pre-ramp rows are the evidence *for* the change and the post-ramp rows are the app *after* it.

Source classes across `components/grove/**` + `app/**`: `text-xs` 81, `text-sm` 37, `text-2xl` 5, `text-xl` 2, `text-base` 2, `text-3xl` 1. Weights: `font-normal` 14, `font-medium` 13, `font-semibold` 5 — **three weights, already**. The ramp below codifies that rather than inventing a fourth.

**Colour is the actual finding, and it is worst away from the shell.** Every surface collapses into two usable neutrals:

- `/usage`: all 814 elements resolved to **three** colours, one of which (`--card-foreground`, 102 elements) is visually identical to `--foreground`.
- Session detail: **2916 of 4107** elements in one colour; 517 in the second.
- Workspace transcript, dark: **2607 elements** shared one near-white.
- **Create dialog: 51 of 53 elements in a single tier.** A whole form with no ranking at all.

That is "nothing is ranked and the eye has no entry point", measured on four different screens.

**The undesigned default is everywhere, and it is invisible precisely because it looks deliberate.** 16px/400 is the browser root, not a decision anyone made: **31 of the create dialog's 53 elements**, 1073 on session detail, 1030 on the transcript, and the login card's own title (`leading-none font-semibold`, no size class). No `text-*` utility is involved in any of them, which is why no amount of reading `components/grove/**` finds it.

**Expect the fix to change nothing, and do it anyway.** The login title measured 16px/600 inherited; the ramp step it should have been carrying, `text-lg`, is *also* 16px — so naming it moved zero pixels. That is not a wasted edit and it must not be reverted as one: an inherited value tracks the user agent, a named step tracks §1, and the two agree today only by coincidence. **A default that happens to land on the right value is still not a decision** — and it is the case most likely to be argued away, because the screenshot before and after are identical.

**Badges are worst on the Info tab, and it is the exact complaint.** One panel, 41 elements, **four variants side by side: `outline` ×3, `muted` ×8, `warning` ×1, `ghost` ×1.** Three of those four exist only in `assistant-ui/badge`. Controls renders **15** `outline` badges in one view. Nothing a user could learn separates them.

**The mono leak is a work-panel problem, not a rail problem.** Changes renders `+0 −0 0 1` in mono; Files renders `+1 −0` in mono; Info renders `11`, `1,068`, `317.9M`, `663.2K` in mono. Every one is a **metric**, and the usage page renders the same class of data in sans. Meanwhile the correct uses sit right beside them — `HEAD`, `.grove/`, `config.json`, `sonnet`, `opus`, `haiku` — so the two are genuinely indistinguishable to a reader.

**Two surfaces are legitimately exempt and the doc must say so, or someone will "fix" them.** Terminal is 98 of 103 elements mono with raw ANSI colours (`rgb(148,148,148)`, `rgb(78,154,6)`) injected by `fancy-ansi` — that is *content*, not chrome. Files carries `9px/700` inside `react-file-icon`'s SVG — that is artwork, not type.

**What this audit did NOT measure**, so nobody mistakes silence for a clean bill: the fleet command palette (`fleet-palette.tsx`), the fleet tree and overlays (`fleet-tree.tsx`, `fleet-overlays.tsx`), the split-view resizer, the live pending-question group, and every surface's hover/focus/error state — states cannot be sampled by walking the DOM once. Those need their own pass before their rows in the map below can be called complete.

**Monospace — leaking.** 24 `font-mono` sites under `components/grove/**`. Correct uses (session ids, branches, paths, MCP server names) sit beside `v0.0.7` in the rail footer, `CardStat`'s metric values, `CardField`'s own docstring promising mono "for identifiers **and timestamps**", and `+0 −0` diff counts. Meanwhile the usage page renders `12.6K`, `204.6K`, `35.9B` in **sans with `tabular-nums`**. The same class of data, two treatments, decided per screen.

**Badges — two components.** `components/ui/badge.tsx` (variants `default` `secondary` `destructive` `outline` `ghost` `link`, pill radius) and `components/assistant-ui/badge.tsx` (variants `outline` `secondary` `muted` `ghost` `info` `warning` `success` `destructive`, three sizes, `rounded-md`, and raw palette classes — `bg-blue-100`, `bg-amber-100`, `bg-emerald-100`, `bg-red-100`). 8 Grove files import the first, 6 import the second. Filed as **#489**. On the fleet card, `active` (workspace status) and `working` (agent state) both render `variant="default"` — two maximally loud badges, adjacent, encoding correlated facts.

**Buttons.** 21 explicit variants under `components/grove/**` + `app/**`: `ghost` 12, `outline` 7, `destructive` 1, `default` 1. `secondary` and `link` unused.

**Icons.** One set (`lucide-react`), 30 import sites, 55 distinct glyphs, plus `@/components/icons/github` and `react-file-icon`. Both spellings of the same glyph are in use — `Activity` 4 / `ActivityIcon` 2, `GitBranch` 2 / `GitBranchIcon` 2, `Server` 2 / `ServerIcon` 3, `TriangleAlert` 2 / `TriangleAlertIcon` 2, `Clock` 1 / `ClockIcon` 2, `Timer` 1 / `TimerIcon` 2, `Terminal` 1 / `TerminalIcon` 4.

**The expanded rail uses `w-[23.8rem]` through the shared `RAIL_WIDTH` constant.** Shell and public share rails keep one desktop measure. Children stay `w-full` so the docked measure cannot leak into the mobile sheet. Narrowing the rail must not shrink text or pointer targets. New workspace gives up spare width while Filters and Pause retain their targets on the same action row. The `text-xl` workspace title, `text-lg` body, and `size-7` brand mark preserve hierarchy through the existing ramp; do not add inline pixel floors.

**The rail's spacing uses the card vocabulary (RECORD + composition).** The expanded scroller uses `p-3` and `gap-3`: header-to-picker, picker-to-actions and actions-to-list match its side gutters. Cards and groups use the same gap; horizontal action gaps and footer rows retain `gap-1.5`. Collapsed uses `px-2` for a fine pointer and `px-0.5` for coarse-pointer 44px targets inside the 48px rail. Brand and page headers share `.workspace-header`, start at the viewport top, and center their contents above one closing rule: 32px on fine pointers, 56px on coarse pointers. The mobile sheet's Search and native Close share that header; no close-button gutter may reduce list or footer width.

**A group break is the rhythm doubled.** Siblings sit one step apart; different kinds of thing sit two. The footer's service baseline is a group break, not a third navigation row.

---

## 1. Type

### The ramp

Seven steps. They are the Tailwind `--text-*` scale, **redefined** in `globals.css` — that is what makes them reach the vendored layer. Nothing outside this table.

| Class | Now | Was | Line height | Role |
|---|---|---|---|---|
| `text-xs` | **12px** | 12px | 16px | Metadata, badges, table numerics, timestamps, captions |
| `text-sm` | **13px** | 14px | 18px | **The default.** Body, controls, card titles, table cells, nav |
| `text-base` | **14px** | 16px | 21px | Prose you *read* rather than scan — the transcript, long copy |
| `text-lg` | **16px** | 18px | 22px | A heading inside a full-page section |
| `text-xl` | **18px** | 20px | 24px | A page title on a surface that leads with one — never `ShellHeader`, see below |
| `text-2xl` | **20px** | 24px | 26px | The one display figure a surface leads with |
| `text-3xl` | **24px** | 30px | 30px | The login pairing code — its only consumer |

**The base came down from 16px to 14px, and the working body size from 14px to 13px.**

Three rules govern the ramp:

- **12px is the floor.** Nothing in Grove code goes below it. WCAG sets no minimum font size, so the constraints we can actually hold are the ones met here: every step is `rem`, so browser zoom and user font settings still scale it, and every step clears 4.5:1 against all three content tiers.
- **Keep the ramp's paired line heights unless a measured component geometry requires otherwise.** Sidebar titles use `text-base` and body metadata `text-sm` at the desktop density root; compactness comes from padding and content-sized layout, not a smaller text floor or a fixed-height crop.
- **The root is 80%, and the ramp above is written at the 16px it was measured against.** This REVERSES the earlier rule here ("the root stays 16px; type moves, geometry does not"), on an explicit request, and the reversal is worth keeping rather than overwriting: that rule's *reasoning* was correct — shrinking the root shrinks every gutter, control height and hit target along with the type — and its conclusion was wrong, because a proportional zoom-out was exactly what was wanted. Grove was being read at 80% browser zoom on a 2K desktop; measured off two captures of the same screen, every dimension differed by that one factor. **A density request is a request about the ROOT; a request about relative emphasis is a request about this table.** Read every figure in this document as its value at a 16px root, multiplied by 0.8 on screen.
  - It is a **percentage**, never a px value, so a reader's own browser font size still scales through it, and every step here stays `rem` so browser zoom still multiplies on top.
  - `@media (pointer: coarse)` restores 100%. A finger has a fixed size and a pixel budget does not.
  - **The ramp's nominal floor is not a rendered-pixel guarantee, and a px floor is NOT how you answer that.** At this root, `text-xs` is 9.6px. Workspace navigation used to restore a 12px rendered minimum at its composition seam through inline `max(12px, …)` on every band — and measured on the built app that override was the defect rather than the guard: it held the shell title, both tab strips, the terminal metadata and the diagram toolbar at 12px while the content those bands FRAME rendered 9.6–11.2px (terminal output 9.6, transcript prose 11.2, inline code 9.5). The chrome became the largest text on the page, which is exactly what "the top strip and tabs are visibly too big" turned out to mean. **A floor written in px also opts that surface out of the density lever, of a reader's own font size, and of zoom — the three things the ramp exists to preserve.**
  - **The fix for "this surface reads too small" is a RAMP STEP, not a pixel.** The bands rank instead: the shell title at `text-base`, both tab strips at the vendor's `default` size (`text-sm`), sub-bar metadata at `text-xs`. Where a vendored variant welds type to height — assistant-ui's `TabsList` `size` is `h-8` **plus** a `group-data-[size=sm]` selector forcing `text-xs` — ask for the size whose TYPE is right and keep overriding the band with an explicit height, which the callers already did. Re-measure nested vendor group selectors either way; they override an ordinary text class silently.
  - **Text size and hit area are separate decisions, and only the second one is legitimately px.** `min-h-[24px]`/`min-w-[24px]` on a compact control is a floor under a POINTER, whose physical size does not change when type gets denser, so it must not scale with this lever — and `size-6` does not hold it, because 1.5rem renders 19.2px here. The coarse-pointer branch above lifts both. Workspace tab rows drop labels before requiring scrolling: all labels → selected label only → icons only. Glyphs and pointer targets never shrink. Only a configured tab set whose icons alone exceed the row may scroll.
  - **An arbitrary `[Npx]` utility is the one thing this lever cannot reach.** Treat it as an explicit physical contract, not a typography repair; use the ramp for text and reserve pixel floors for targets.
- **`ShellHeader`'s title is `text-base` — ONE STEP above the navigation beside it, not the ramp's `text-xl`.** That row was written for a masthead; the header is a 32px chrome strip closed by its own rule, and 18px there would make the title the largest thing on a workspace page, above the transcript prose it frames. **A title's step is relative to what shares its band, not absolute** — the whole hierarchy this strip can carry is title > tabs > sub-bar metadata, three adjacent steps, and it reads because they are adjacent.
- **Line heights are absolute, not ratios.** A fixed `rem` line height is what lets a table row, a card field and a badge sit on the same rhythm when their sizes differ.

### Weights

Exactly three. A fourth is a bug.

| Class | Use |
|---|---|
| `font-normal` (400) | All body, all metadata, all values |
| `font-medium` (500) | Card titles, table headers, the active nav item, badges, button labels |
| `font-semibold` (600) | The page title and the display figure — nothing else |

**Weight is the coarse rank and the neutral tier is the fine one.** At 12–14px a 1px size difference is not perceptible; a tier change is. This is why the fix for "everything looks the same" is section 2 and not a wider size spread.

### Never

- **No arbitrary sizes.** `text-[11px]`, `text-[0.85em]` — if it is not in the table it does not exist. (The vendored layer has both; see [where a vendored decision stands](#where-a-vendored-decision-stands-even-though-we-would-have-chosen-otherwise).)
- **No `tracking-*` outside a display figure.** Letter-spacing at 12px is noise.
- **No `uppercase` for hierarchy.** It costs legibility and buys a rank the ramp already provides. The one surviving use is `terminal-tab.tsx`'s stream label, which is a literal channel name.

---

## 2. The neutral content ramp

Three tiers. Every piece of text on every surface is exactly one of them, and the choice is about **reading order**, not about importance in the abstract: which do you want the eye to land on first, second, and only-if-you-went-looking.

| Token | Class | Light | Dark | For |
|---|---|---|---|---|
| `--content-primary` | `text-content-primary` | 4.73:1 | 19.06:1 | What the surface is *about*. The name, the title, the value. **Read first.** |
| `--content-secondary` | `text-content-secondary` | 9.21:1 | 12.34:1 | Supporting text that qualifies the primary — a description, a subtitle, a body sentence. |
| `--content-tertiary` | `text-content-tertiary` | 4.83:1 | 7.59:1 | Metadata. Timestamps, counts, labels, units, field names. **Present, not read.** |

Contrast is measured against `--background` in each theme. All three clear AA (4.5:1) for normal text at every step of the ramp.

- `--content-primary` aliases `--foreground` (light L .56); `--content-tertiary` aliases `--muted-foreground`. **Every `text-muted-foreground` in the tree today is already correct as tertiary** — adopting the ramp is a rename, never a re-pick. `--content-secondary` is the genuinely new value, and it is what supporting text should have been using all along instead of borrowing one of its neighbours.
- **One primary per block.** A card whose title, value and description are all primary has ranked nothing. If two things want primary, one of them is secondary.
- **Do not use opacity to invent text tiers.** The sidebar's explicitly specified 95% unselected-card emphasis is an object-level exception, restored on hover/focus and contrast-checked after compositing.  `text-foreground/50` is a fourth ramp; the vendored `elements/` layer uses one (measured on the transcript: 331 elements at `/0.5`, 57 at `/0.9`, 29 at `/0.4`, 10 at `/0.55`) and Grove code must not imitate it. An alpha neutral changes meaning against every background it lands on; a token does not.
- **Do not use `text-card-foreground`.** It resolves visually identical to `--foreground` and buys nothing but a second name for tier one.

---

## 3. Monospace, and numbers

**Monospace is semantic. It means: this is a literal — something you could type, copy, or paste and have it mean the same thing.** It is not a texture, and it is not how data looks technical.

| Mono (`font-mono`) | Not mono (`font-sans`, the default) |
|---|---|
| File and directory paths | Timestamps, dates, durations, relative ages |
| Branch names, git refs, SHAs | Version strings (`v0.0.7`) |
| Session, workspace and run IDs | Counts, totals, percentages, currency |
| Commands, flags, env var names | Diff line counts (`+12 −4`) |
| Model IDs (`claude-opus-5`) | Token and cost figures |
| MCP server and tool names | Anything you would read aloud as a quantity |

The dividing question: **would retyping it character-for-character matter?** A SHA yes, a duration no.

### Chrome is exempt, and the exemption is stated as an ACT rather than a place

**No Grove code inside `AppSidebar` or the status band sets `font-mono` — not the branch, not the daemon's version, not a counter, not a footer control.** Both are scanning surfaces, and a monospace face costs roughly 15% more width for text a reader is not transcribing; the same branch name still renders mono on a fleet card, in the Info tab and in every table, because those are surfaces you stop on. **Tabular figures survive the exemption** — `tabular-nums` is a separate claim about digit alignment and the typeface was never what carried it.

**This was written as "the sidebar, wholly, the one place" and the status band then proved that framing wrong** — it is chrome by every argument the rail's exemption makes, and it kept two mono values for a year because the rule named a component instead of a reading act. The generalisation is deliberate and it is also the limit: **chrome you scan, not any surface that happens to be small.** A fleet card is dense and is still a place you stop.

Two things this exemption is NOT. It is not a judgement that a branch stopped being a literal — §3's table still holds everywhere else, and the exception is stated so nobody generalises it into "mono is optional". And it is not enforced by a linter: `tests/unit/app-shell.test.ts` and `tests/unit/status-footer-render.test.tsx` take source censuses over each surface's files, and `tests/e2e/sidebar-sessions.spec.ts` reads `fontFamily` off every element in the live `aside`, which is the check that survives a class arriving from a component nobody thought to grep.

### Numbers

Numeric formatting is a rule, not a per-screen choice.

- **Every number gets `tabular-nums`**, in mono or sans. A column of figures whose digits do not align is a column you cannot compare down.
- **Numbers right-align in tables**, with the header right-aligned too. The column class is `NUMERIC` from `components/grove/table-columns.ts` — never a hand-written `text-right`.
- **Numbers left-align inline** — in a stat, a badge or a sentence.
- **Abbreviate above 4 digits**, one decimal, SI-style: `12.6K`, `204.6M`, `35.9B`. Below 10,000, print the number. **Context occupancy is a precision exception (ADD):** use decimal K/M/B with up to two fractional digits for the occupied/capacity pair and percentage. Both the Activity card and footer use the same formatter over the primary session's raw counts. Values remain normal weight with tabular figures. Activity also shows the exact grouped token counts inline so touch readers can reconcile the compact reading without hover.
- **The exact value is always one hover away.** An abbreviation is a reading affordance and never destroys the precise figure — same contract `RelativeTime` keeps for its `title`.
- **Where there is no visible figure, the tooltip carries BOTH forms:** `516.8M tokens (516,826,976)`. The rule above assumes a visible abbreviation with the exact value behind it, and a heatmap cell breaks that assumption — it is a colour swatch, so its tooltip is simultaneously the readable rendering *and* the only place the precise number exists. Grouped digits alone were measured as unreadable by a person; abbreviating alone would put the exact figure nowhere. **This is the one exception, and it is an exception to the mechanism rather than to the intent** — both halves of the contract still hold, they just land in the same place.
- **A unit is tertiary.** `12.6K` primary, `tokens` tertiary; never the same tier, or the unit competes with the figure.
- **PRECISION follows the act, exactly the way SIZE does in §1.** A surface that is *scanned* takes the largest whole unit — a rail column that also truncates has no room for a remainder nobody is comparing. A surface *opened deliberately to read one thing* takes the remainder, because rounding it away discards what the reader came for: `5h ago` for something touched at five hours and fifty minutes. **That is two formatters, never a `precision` prop** — a formatter a caller can configure is one two callers configure differently, and the same value then reads two ways with nothing to say which was meant. Same rule as §7's entity components taking no `icon` prop.
- **Two measurements that can legitimately diverge are TWO figures, never one.** A session's wall clock (its active intervals merged, so concurrency counts once) and its compute (the same intervals summed across every sub-agent) answer different questions, and ten sub-agents running ten minutes side by side honestly read `10m` and `100m`. Collapsing them into one column is a claim they are the same number — the same species of dishonesty as a fabricated zero (§11).

### Provenance: a figure GROVE CALCULATED wears a dashed underline

**`underline decoration-dashed underline-offset-2`, plus a `title` saying what it was calculated from.** One treatment, every surface, for one claim: *this number is Grove's arithmetic, not the provider's word.* It is an **ADD** — the vendored layer has no opinion here — and it needs no token, because it is a text decoration rather than a colour.

**The axis is WHOSE CLAIM IT IS, not whether the number moves.** A percentage the provider published is reported even though it changes hourly; a countdown to a reported instant is derived even though the instant is not. "Everything dynamic" would mark nearly every figure on a data surface and carry no information at all.

**Why this is not a nicety: on the quota card exactly ONE figure per window is published.** Measured against the live daemon 2026-08-11 — the vendor supplies `used_percent`, and the measured tokens, the capacity, the countdown, the burn rate and the forecast are all Grove. A reader reconciling a number against their own bill has no other way to tell, and prose does not deliver it at a glance.

Three constraints, each already implied by a rule above:

- **Dashed, never dotted.** `decoration-dotted` is `Explain`'s glossary affordance — *there is a definition behind this word* — and the two appear on the same rows. Two claims sharing one decoration is two claims nobody can read.
- **The mark is the SECOND carrier.** §4.7 governs any visual carrier, not only colour, so the `title` is required rather than optional: it states the provenance and, where the figure is abbreviated, doubles as §3's exact value. Never nest it inside a component that mounts its own tooltip — one figure, one hover affordance.
- **It pairs with `~`, it does not replace it.** The tilde says an *estimate*'s accuracy is in doubt; the rule says *who did the arithmetic*. A derived figure that is exact (a countdown, a subtraction) takes the rule and no tilde.

**The same dash draws the chart's derived strokes** — the forecast line and every ceiling — so one vocabulary spans a figure and a line, and "dashed means Grove worked this out" is learnable once.

---

## 4. The colour system

Everything this app has decided about colour lives in this section. If a colour decision is not here, it has not been made.

### 4.1 Three kinds of colour, and the rule that sorts them

**Adopt this verbatim: if a colour is gated on run state it is a STATE colour and comes from the semantic set; otherwise it is an IDENTITY hue from a separate set.**

That single sentence resolves most of what looked like badge inconsistency, because it turns "which colour?" into a question with a checkable answer: *does this change when the thing changes?*

| Kind | Gate | Set | Examples |
|---|---|---|---|
| **State** | Changes with run state | `--destructive`, `--success`, `--primary` | error, working, healthy quota |
| **Identity** | Fixed property of the thing | `--chart-1…5`, brand marks | a model's series in a chart, an agent's logo |
| **Sequential** | Position in an ordered range | `--heat-0…4` | activity density |

**Sequential is a third category and is named here so nobody invents a fourth.** A sequential scale is not a set of states and not a set of identities: it encodes magnitude, so it must vary *lightness* monotonically rather than hue. `--heat-0…4` does, which is why it is the one colour system here that already survives greyscale (measured below).

Consequences:

- **Neutral is the default.** A surface with no state to report is entirely neutral. This is what makes the one coloured thing on a screen mean something.
- **This decides #489.** `assistant-ui/badge`'s `info`/`warning`/`success` variants are raw palette hues (`bg-blue-100`, `bg-amber-100`, `bg-emerald-100`) — **an identity set wearing the costume of a state set.** They are fixed hues chosen per call site, gated on nothing. `ui/badge` is canonical for **state**; the identity set is `--chart-*` and brand marks, and it never appears as a badge fill.
- **Amber means work in flight or outstanding work.** `--warning` is gated by the fleet state tables and nonzero dirty/behind/queued counts. It never colors identity or an unstarted zero. The accent rules in §6 govern badge fills; a labelled quantity uses the text token instead.
- **`lint:styling` is the enforcement** for raw palette classes under `components/grove/`. Trust it; do not re-derive it per file.

### 4.2 The neutral foundation

**Every neutral in this app sits on one hue — 286 — at chroma 0.004–0.016.** A single shared hue at low saturation is what makes a dark mode read as considered and warm rather than as dead black, and it is why greys from different sources never clash here.

### An alpha is a relationship; a rung is a position

**And we keep discovering we wanted a position.** Three separate defects turned out to be the same mistake:

- the shell gutter was `bg-muted/30`, so it could only ever be a *fraction of a step* from whatever sat behind it — when the page background moved, the gutter moved with it and the two collapsed to ΔY 0.0018;
- dark `--input` was `oklch(1 0 0 / 15%)`, an alpha edge that composited to 1.49–1.62:1 and was invisible to a contrast table because a table lists colours and this had none;
- the `elements/` layer expresses its whole hierarchy as alpha on `--foreground`, which is why §2 forbids Grove code from imitating it.

**An alpha value cannot state a level, because it does not have one — it has a difference from an unknown.** That is fine for a hover wash or a scrim, where "slightly more than whatever is there" is exactly the intent. It is wrong for anything the ladder is supposed to place, and it fails silently, because the value looks reasonable in isolation and only breaks when something underneath it moves.

The test: **if you would have to know what is behind it to say how light it is, it cannot be a rung.**

Pure `oklch(1 0 0)` and `oklch(0 0 0)` are the only permitted chroma-zero values, and only at the ends of the ladder.

### 4.2b Token naming: role and elevation, never appearance

**A token name says what a thing IS in the hierarchy, never what it looks like or which component uses it.** Components ask for a *level*; each theme supplies its own values; and when we discover the steps are too subtle we retune **one file** instead of hunting through components.

| Good — names a role or a level | Bad — names an appearance, a component, or a place |
|---|---|
| `--surface-raised`, `--surface-sunken` | `--card`, `--popover`, `--sidebar` |
| `--content-tertiary` | `--muted-foreground` |
| `--edge-control` | `--input`, `--ring` |

**This is the direct cause of the complaint that started this section — that the rail's colour decisions are hard to interpret against the content area's.** They are *not comparable*, because they are not the same kind of name: `--background` is a role, `--sidebar` is a **place**, and `--card` is a **component**. Three naming schemes describing one hierarchy means no one can tell whether the rail is meant to be above, below or level with the content — there is no shared axis to answer on. The ladder gives one: **every surface in the app is one of four rungs, and the rail and the page are both `base`.** That is the answer the old names could not express.

Two consequences worth stating:

- **`--sidebar-*` is a whole parallel palette keyed to a place.** It is vendored, so it stays; but Grove code reaches for a *rung*, and the rail's rung is `base` — the same as the page, because the rail and the gutter are one plane and the panel rises off both.
- **Appearance-named tokens are how a theme rots.** `--muted` cannot be retuned without asking "muted relative to what?", which is unanswerable; `--surface-sunken` can, because the name states its position.

### 4.3 The surface ladder

**The page background is a DATUM, not a floor.** Cards and panels rise above it; code wells, terminals and inset regions **recess below it**. Containment reads in both directions. The app had no vocabulary for "below" at all, which is why every inset region reached for `bg-muted`.

**Four rungs. Fewer levels further apart beats seven nobody can tell apart.**

| Rung | Token | Light L | Dark L | For |
|---|---|---|---|---|
| **sunken** | `--surface-sunken` | 0.900 | 0.100 | **The shell gutter and the rail.** Also terminal, diff wells, code blocks, inset lists — content a surface *contains* rather than presents |
| **base** | `--surface-base` | 0.950 | 0.155 | **The datum.** The page — i.e. the shell panel |
| **raised** | `--surface-raised` | 1.000 | 0.213152 | Card bodies and explicit raised surfaces |
| **overlay** | `--surface-overlay` | 1.000 | 0.285 | Popovers, dialogs, menus, the command palette — floating over everything |

**A rung is a TUPLE, never a colour.** Ask for a level and compose all four parts; lightness alone is never the carrier:

| Rung | Fill | Edge | Elevation | Radius |
|---|---|---|---|---|
| sunken | `bg-surface-sunken` | `border-surface-edge` | none (inset by tint) | `rounded-md` |
| base | `bg-surface-base` | none | none | none |
| raised | `bg-surface-raised` | `border-surface-edge` | shadow in **light only** | outer container role, 5.75px minimum |
| overlay | `bg-surface-overlay` | `border-surface-edge` | shadow in both | outer container role, 5.75px minimum |

**The model menu is bounded ONCE, by what Radix measured.** Its content is a column no taller than `min(24rem, --radix-popover-content-available-height)` and the list inside is the part that gives; the vendored list's own 300px cap is released there, because two bounds that disagree — a clipping popover around a self-capped list under a search row — left the tail unreachable on one side and the head on the other. Measured on the built app at a 560px viewport: flipped above the trigger the menu spans 0–279px, the search row stays visible, and both the first and last rows can be scrolled fully into view.

**Overlay content takes that both-themes shadow from the theme boundary** — `model-selector-content`, `popover-content`, `dropdown-menu-content` and `select-content` — because the vendored `shadow-md` is tuned for a white page and on the dark ladder reads as the composer's own plane rather than as something floating above it.

**FIVE RADII, EACH A ROLE.** This is the whole radius vocabulary; a corner outside it is a bug, and a Playwright census over every visible element on the workspace page enforces it in both themes.

| Radius | Role |
|---|---|
| `0` | list rows, table cells — a row's boundary is its neighbours |
| `2.3` | keycaps, badges, inner cells, code blocks |
| `3.45` | buttons, inputs, selects, tab triggers, chips |
| `5.75` | cards, popovers, dialogs, the page panel — and the composer bar at **6.9** (20% past the role, the one container that holds an editor plus a toolbar of rounded squares; scoped to its slot, admitted by the radius census for that slot only) |
| `full` | circles ONLY — status dots, avatars, brand marks, spinners |

`--radius: 0.2875rem` and a `5.75px` container floor make the original role scale 15% softer without changing its hierarchy. **The whole Tailwind scale collapses onto those roles:** `xs`/`sm` → 2.3, `md` → 3.45, `lg`/`xl`/`2xl`/`3xl`/`4xl` → 5.75. Both the rem base and its floor scale together, so reader font settings can still enlarge them. Zero and circular roles are unchanged; vendored files remain untouched.

**The theme boundary owns arbitrary radii too.** Prefer `data-slot` role selectors in `app/globals.css`; where an upstream element exposes none, keep a narrowly named literal-class exception in the same block. Do not edit vendored files or scatter corner overrides across Grove compositions. Preserve `CardRegion`/`CardCell`'s inner-cell role through `--radius-container` rather than pinning every Card to the outer corner.

**A card body is the `raised` rung.** It was briefly moved to `base` so that a `bg-muted/30` region would read *lighter* than the body it sits on; measured, that made the card body and the page it sits on the identical token — ΔL 0.000 — and the card was separated from the page by a hairline alone, which is the exact failure the ladder exists to prevent. A card rises off its page; the header band, the enclosures and their borders carry the hierarchy *inside* it. The alpha region remains a wash, **not a full rung**: it does not clear the 0.05-L adjacent-rung floor and must not be cited as doing so, and its border supplies the redundant enclosure. Headers alone use the `surface-header` gradient described in §8. In dark mode, `surface-raised` is `oklch(0.213152 0.006 286)`. Its WCAG relative luminance is 85% of the former raised fill, leaving the base to raised gap above 0.05.

The redundancy is **by specification**, exactly because the lightness delta is small by necessity in places. When the tint cannot do the work, the edge and the radius still say where the boundary is.

Labelled controls use `border-edge-control` for a quiet resting enclosure; contrast-critical input and focus boundaries use `--input` and `--ring` (§4.6). Do not substitute the resting outline for a focus indicator.

> **Gotcha worth knowing before you grep for missing CSS.** Tailwind v4 emits a utility only when it finds the class name in a scanned source, and **this document is one of them.** A class named here generates real CSS; a token that exists but is never written as a class generates nothing, which looks identical to a broken token. Verify a token by checking the emitted `--custom-property`, not by looking for its utility.

### 4.4 Perceptual separation, and the number to check

**Steps are defined in OKLCH, which is perceptually uniform, so a step means the same thing at both ends of the range.** The theme was already OKLCH, so this is a rule, not a migration.

**The floor: adjacent rungs differ by ≥ 0.05 OKLCH L.** Chosen as roughly 3–5× the just-noticeable difference under ideal viewing — the margin that is meant to survive a dim panel, glare and reduced brightness. A couple of points of HSL lightness is a real difference in a design tool and no difference at all on cheap hardware.

Measured deltas:

| Step | Light ΔL | Dark ΔL |
|---|---|---|
| sunken → base | 0.050 ✓ | 0.055 ✓ |
| base → raised | 0.050 ✓ | 0.058152 ✓ |
| raised → overlay | **0.000 ✗** | 0.071848 ✓ |

**Light sits EXACTLY on the floor on both steps, and that is not slack anyone left — it is the only configuration that exists.** The ladder is squeezed between a physical ceiling and a legal floor: `raised` cannot go above white, and `sunken` cannot go below 0.900 because tertiary text reads 4.56:1 there and **4.49:1 at 0.895**, under AA. That leaves 0.10 of range for two steps. Widen either one and the other breaks a rule.

**`raised → overlay` is the one step light cannot afford, and it is carried by a scrim rather than a tint.** A modal already darkens everything beneath it, which separates far more forcefully than 0.05 of lightness would; spending the last of the range there would have cost the card-on-page step, which is the one a user looks at constantly.

**A lightness delta is not a contrast ratio, and near black they diverge badly.** The same ΔL buys far less WCAG contrast at the dark end, because luminance is compressed there:

| Step | ΔL | Resulting contrast |
|---|---|---|
| light sunken → base | 0.065 | 1.21:1 |
| dark sunken → base | 0.055 | **1.05:1** |

Never quote one as if it were the other.

### 4.5 Light and dark are designed independently

**Neither theme is the other inverted.** Above white there is no headroom, so light mode has to buy its upward room differently:

- **The datum comes off pure white** (0.950), spending the last 0.050 on `raised`.
- **`overlay` has no lightness step left at all.** It is distinguished by the modal scrim, shadow and edge.
- **Light's range is a fixed budget, so every rung is a claim on the other rungs.** Adding a level in light is not a design decision, it is an arithmetic one: 0.10 of usable range, 0.05 minimum per step, therefore three fills and no more. A fourth layer must be carried by something that is not lightness.
- **Shadow does in light what lightness does in dark.** `surface-raised`'s box-shadow resolves to `none` in dark deliberately: there the tonal step already lifts, and a dark shadow under a lighter card only muddies the edge.

**Our own theme already proved this rule before it was written.** `--card` is the *same pure white* as `--background` in light mode — measured **1.00:1** — so a hairline border was the only thing separating an expanded plan card from the transcript behind it. That is the no-headroom failure in our own tokens, and it is the single strongest argument for designing the two themes independently.

The reverse case is just as real: **the shell panel's tint step REVERSES between themes.** `bg-background` is lighter than the `bg-muted/30` gutter in light and *darker* in dark, which is what makes it read as depth rather than as a colour — and precisely why `bg-card` was the wrong token, since `--card` is *lighter* than `--background` in dark and would invert the cue.

### 4.6 Contrast floors are acceptance criteria

Not aspirations. A surface that misses one is not done.

| What | Floor | Basis |
|---|---|---|
| Body text | 4.5:1 | WCAG AA |
| Large text (≥24px, or ≥18.66px bold) | 3:1 | WCAG AA |
| **Control edges and state indicators** | **3:1** | WCAG 1.4.11 non-text |
| Decorative container edges | 60% of their opaque source over the actual background | approved 40% reduction in border prominence, not a WCAG threshold |

**That last row is a real distinction, not a loophole.** 1.4.11 scopes non-text contrast to what identifies a *component* or its *state*. Chasing 3:1 between adjacent container fills would produce a checkerboard, and no adjacent rung here reaches it (the best is 1.21:1) — which is correct and expected. **The 3:1 belongs to the edge, not the fill.**

#### The edge tokens are TWO TIERS, and only one of them answers to WCAG

**"No numeric floor" was the right reading of the spec and the wrong thing to
ship.** It was true — and it let `--border` sit at **1.09:1** on the surface it
is drawn on, which is the token `Separator` uses (`bg-border`) and which the
base layer's `* { @apply border-border }` hands to every bare `border-t` in the
app. The weakest value in the theme was carrying nearly every seam, and the
result read as undrawn rather than as quiet.

**The spec's exemption is real, so quote it rather than re-deriving it.**
Understanding SC 1.4.11: *"This success criterion does not require that controls
have a visual boundary indicating the hit area"*, and where a control has
visible content identifying it, *"a border or other indication of the overall
boundary of the hit area is not required, as is therefore not subject to
non-text contrast requirements."* A labelled button's outline, a card's
perimeter and a section divider are all outside 1.4.11. **So the floor for them
has to come from somewhere else, and "nowhere" is not an option.**

The benchmark is what comparable systems actually ship, measured on their own
page: Primer `borderColor-default` **1.45:1**, Material 3 `outline-variant`
**1.70:1**, M3 `outline` (its prominent role) **4.55:1**. M3's two-role split is
the structure adopted here.

| Tier | Tokens | Floor | Job |
|---|---|---|---|
| **prominent** | `--input`, `--ring` | 3:1, per 1.4.11 | identifies a component or its state |
| **decorative** | `--border`, `--surface-edge`, `--sidebar-border` | 60% source opacity | card perimeters, interior separators and shell dividing lines |
| **control resting** | `--edge-control` | existing opaque value, unchanged | resting outlines on labelled interactive controls |

**The prominent tier remains unchanged.** Its light L0.594 and dark L0.56 values were solved against the worst surface. The decorative reduction does not alter that contrast guarantee or the resting control token.

#### Solve a SUBTLE edge on the rung it renders on, not on the worst rung

**The worst-rung rule belongs to the prominent tier and does not transfer.** A
3:1 identity claim must hold everywhere, so it is solved against the extreme. A
subtle edge has no floor to guarantee — it has a *look* to hit — and solving it
at the extreme guarantees every other rung overshoots.

Measured, that overshoot shipped once and was rejected on sight: `--border` at
L0.76 was reported as "1.59:1" (its worst rung, `sunken`) while the thing a user
actually looks at, **a card perimeter on `raised`, rendered 2.15:1** — and dark
`--border` at L0.42 drew **2.43:1** on the rail. The review was that the app had
become "a Windows 97 high-contrast application", with the outlines competing
with the shapes they enclose. Both numbers were honestly measured and neither
was the number on screen.

| token | renders mostly on | light | dark |
|---|---|---|---|
| `--border`, `--sidebar-border` | `raised` (card edges, dividers) | 1.69 | 1.62 |
| `--surface-edge` | `raised` (enclosures) | 1.75 | 1.62 |
| `--edge-control` | `sidebar` (labelled controls) | 1.79 | 1.92 |

**Treat the peer benchmark as a CEILING, not a target.** Primer 1.45 and M3
`outline-variant` 1.70 are where mature systems put a divider *on its own page*;
landing above them is not "more compliant", it is louder than the reference. And
**a subtle edge has a failure mode in both directions** — 1.09:1 reads as
undrawn, 2.15:1 reads as chrome — which is exactly why it takes a look and not a
floor.

**Dark needs a smaller step than light here, not a larger one.** The eye
tolerates far less separation from black, so the same nominal jump that reads as
a hairline on white reads as a drawn line on a dark rail. §4.4's "a lightness
delta is not a contrast ratio" is the arithmetic; this is its perceptual half.

**THE TABLE ABOVE IS PER RUNG, AND `sunken` IS NOT IN IT — a surface that lives
there needs its own value (ADD).** The measured rows name `raised` and
`sidebar`, which is where card edges and control outlines land. Composited on
`surface-sunken` — the darkest rung — `--border` reads **1.37:1**, below the
1.62-1.70 band the same token achieves one rung up, so a seam drawn with it
there is the "reads as undrawn" failure this section opens with. The global
status footer sits on that rung and takes `--footer-rule`, solved against it:
**1.75:1 dark, 1.59:1 light**. Do not reach for `--border` and conclude the
design is flat; ask which rung the element renders on first.

**A WASH IS SPECIFIED AS A LIGHTNESS STEP, NEVER AS A RATIO.** The footer
groups its middle sections with a ±0.045 L step, which computes to **1.04:1** —
a figure that says "invisible" about something plainly visible, because ratio is
the wrong instrument near black. It stays deliberately under §8's 0.05
adjacent-rung floor: this groups two areas of one band, it does not promote them
to a new elevation. And **it reverses between themes** (dark lifts, light
recedes) for the reason the shell panel already establishes — elevation is a
relationship between two layers rather than a colour. Every content tier and
state colour was re-measured against the wash and clears 4.5:1, which is the
check a wash owes before it ships.

**A CHROME BAND NARROWS BY DROPPING VALUES, NEVER BY DROPPING THEIR WORDS (#815).** The status band is the worked example: below `lg` it shows three icon-led values — project, the single most severe session count, the single most severe quota fact — and moves everything else into a bottom sheet. Three rules come out of it, and each is a rule about any band, not only this one.

- **Selection is by SEVERITY, never by position.** A summary showing the first of a list prints `0 idle` while an agent sits blocked, and a plain `4 accounts` while one is exhausted. `sessionSummary`/`quotaSummary` in `adapters/footer.ts` decide this as pure functions, so the ranking is testable without a viewport, and their thresholds are `accountTone`'s own rather than a second copy.
- **The word travels with the number, always.** §4.7 says colour is never the sole carrier, and the narrow band is precisely where a desktop's `lg:` label is absent — so `1 blocked` and `1 at limit` are the values, not `1` in red. This is why the narrow layout prints words (`at limit`, `near limit`) where the wide one prints a percentage: the wide strip has the account name beside it and the narrow one has nothing else to carry the meaning.
- **A rounded figure is a DISPLAY decision that must not reach a threshold.** `percentLabel` prints at most one decimal because a provider published `23.63`; every comparison still reads the raw number, or 99.6% would round to `100%` and claim a limit nobody reported.

**A REDUCTION IN CONTENT IS NOT A REDUCTION IN HEIGHT.** The narrow band keeps the same 24px/44px physical minima — fewer values at the same size, never the same values at a smaller one. Measured on the deployed app at 320/390/820/1600px: 44px on a coarse pointer, 25px on a fine one, no horizontal overflow and no wrap at any of them.

**A THIRD GROUND IS EARNED BY GROUPING THINGS THAT DO NOT TOUCH, and that is what distinguishes it from a second wash.** `--footer-wash` groups two ADJACENT sections, so a step against the band is enough to read them as a pair. `--footer-accent` groups the band's two ENDS — project and version — which never touch, so it has to be distinguishable from the wash as well as from the band: one step further out (±0.078 L against the band, against the wash's ±0.045). It is still under §8's 0.05 adjacent-RUNG floor in the sense that matters, because it groups areas of one band rather than promoting either end to a new elevation, and it reverses between themes for the same reason the wash does. **Before adding a ground, ask whether the things it groups are adjacent: if they are, the existing wash already does it.**

**A BAND THAT DIVIDES ITS SECTIONS AND NOT THEIR CONTENTS HAS DIVIDED NOTHING — so dividers come in TWO ORDERED TIERS (ADD).** The status band shipped with a full-height `--footer-rule` between sections and nothing inside them, and the result read as one stream: project, sub-path, branch, worktree, agent and context ran together as an undivided run of glyphs, and two subscription accounts abutted, so the boundary between one account and the next was no stronger than the boundary between a plan and its own percentage. **Two levels of structure sharing one divider is the same failure as no divider.**

- **The second tier must be genuinely QUIETER, and the arithmetic is the proof.** Measured on `surface-sunken`: `--footer-rule` is 1.59:1 light / 1.75:1 dark, `--footer-seam` is 1.39:1 light / 1.41:1 dark. The seam clears §4's 1.09:1 "reads as undrawn" floor and sits under the Primer `borderColor-default` 1.45:1 the decorative tier answers to. **Drawing both with one token is the inverse failure:** seven equal divisions carry no grouping at all.
- **Nesting is carried by HEIGHT as well as weight.** The section rule runs the band's full 24px (44px coarse), the seam is half of it — so the hierarchy survives a monochrome display and a reader who cannot resolve a 0.16 L step. A tier separated by colour alone is §4.7's rule applied to structure.
- **A DIVIDER INSIDE A PHYSICAL-PIXEL BAND IS ITSELF A PHYSICAL PIXEL.** `h-3` measured **9.59px** on the deployed page — 0.75rem at the 80% density root, §3's `h-6` trap exactly. The band is a rendered-pixel contract and its divider was not, so the density lever moved the seam while leaving the band alone and the half-band ratio the hierarchy rests on drifted with a setting that should never touch it. **The type inside the band stays on the rem ramp**; only the chrome that frames it is pinned.
- **Ask what the two values are, not how many there are.** `ahead`/`behind` take no seam (two directions of one comparison against the base branch) and neither does `Grove › webapp` (one location in two parts, where the chevron is already the separator). A seam between those would claim they were peers. A plan and its percentage DO take one; so do an agent's name and its context reading, which had been one span and rendered as a name running straight into a number.
- **Place dividers BETWEEN rendered children, never beside each conditional one.** Every group in this band is conditional — a root workspace has no worktree, a clean tree has no dirty count — so `{cond ? <Seam/> : null}` at each site puts the divider's presence in the hands of whichever neighbour happens to render, and the band opens or closes on a rule. Filtering the rendered children once (`Seamed`) makes a leading seam unrepresentable rather than merely unlikely.
- **A SEAM DIVIDES GROUPS, NEVER VALUES — and the first application of this rule got the grain wrong.** Seaming a plan from its reading and bordering each account gave a two-account section five verticals and no anchor, reported as "too many borders" and "cannot tell which 7d belongs to whom". Inside a group the divider is whitespace; the group's ANCHOR is what makes it findable, and for an account that anchor is its provider's brand mark at the left edge (the identity every fleet row already leads with). Everything from one mark to the next is one account, and the provider word is dropped from the text because the mark already says it.
- **ONE SIZE PER CHROME BAND, NAMED ONCE ON THE ROOT.** Counted on the status band: 18 `text-sm` and 8 `text-xs` in one 24px strip, every group setting its value in one size and its word in another. A glance cannot use two sizes; it can use tone, so value/word is `secondary`/`tertiary`. The band's primitives (`footer/primitives.tsx`) name no size at all — a section cannot disagree with a sibling about a choice it never makes.
- **ONE FACE, TOO — AND THE MONO EXEMPTION IS THE RAIL'S, EXTENDED TO EVERY CHROME BAND.** The branch and the worktree held `font-mono` on §3's argument that a ref is read character by character, which is right where you STOP to read one and is why they are still mono on a fleet card, in the Info tab and in every table. A band is the other act: it is scanned, never transcribed, so the face costs ~15% more width for no recovered meaning — and with the rest of the strip proportional, one word in a second typeface reads as a defect rather than as a category. `tabular-nums` survives the change untouched, because digit alignment is a separate claim that was never the typeface's job. **The discriminator is the READER'S ACT, not the value's type** — the same branch string is mono one surface over.
- **A `truncate` IS A FLOOR, NOT A CEILING, AND CHROME NEEDS BOTH.** `min-w-0 truncate` says a value *may* give way; it never says *how much* it may take, so in a flex row the longest string draws in full and the shortest is destroyed. Measured on the deployed band at 1024px: a 29-character plan held 133px while `main` was squeezed to 11px of the 24 it needed, and the context reading overlapped the git section's first value by 18px. **The fix is a ceiling per KIND of value, stated in `ch`** — `name` (22ch) for what a reader RECOGNISES, `label` (14ch) for what they CONFIRM — owned by the primitive so no section chooses whether one applies. `ch` rather than px because a px bound is written against a 16px root and this app renders at 80%: `max-w-40` silently meant something other than the 160px it says. **And the bound belongs on the elastic values only** — a version or an uptime is ours and bounded by construction, so requiring a ceiling there is cargo-culting the fix onto values that cannot exhibit the bug.
- **WHEN A BAND STILL OVERFLOWS, SHED A WHOLE VALUE — NEVER CLIP THE SECTION.** Ceilings cannot save a group of two figures, which has nothing to give; the workspace section still pushed 20px through its own closing rule. `overflow-hidden` on the section looks like the structural answer and is worse: a box clips its RIGHT-hand child, which was the percentage, so the one value carrying urgency and the tone ramp was deleted while the expendable raw counts survived whole. Gate the counts above the container's own threshold instead and never gate the reading. This is #815's rule reaching one step further: that rule forbids a value losing its WORD, and this allows the band to lose a whole VALUE — so a gate BELOW the container's breakpoint is still dead code and still the bug, while one ABOVE it is how a narrow band stays legible. **Ask which half of a group a reader acts on, and let the other half be the one that goes.**
- **UNEVEN SPACING AROUND A DIVIDER IS USUALLY A CLIPPED NEIGHBOUR, NOT A SPACING BUG.** The band's group wrapper was `min-w-0`, so flex could shrink it below content that is itself `shrink-0`; the child then overflowed the wrapper and the section's clip cut it. Measured at 1024px: **24.3px of air before the section rule against 6.4px after it**, with the `%` sliced mid-glyph. The reported symptom is the *gap*, and the cause is the *value*, so the padding is the last place to look. **A container may only shrink past its content if that content can elide** — the wrapper mirrors the group's own rigidity, and a group composed from a component must declare it at the composition point, since a parent inspecting `className` cannot see inside a component.
- **A PERMANENTLY VISIBLE READING TAKES THE COARSEST PRECISION THAT STILL SUPPORTS THE DECISION.** Quota and context percentages printed `23.6%` and `28.95%`; nobody acts on a tenth of a percent of a weekly quota, and the digits cost width in the band whose scarcest resource is width while putting a restless figure in a strip whose job is to stay still. Whole percents in the band; the Activity card and the usage page keep their decimals, because §3's precision rule follows the ACT and those are surfaces opened to read one thing. **The rounding is display-only and must never reach a threshold** — 99.6% prints `100%` and is still `near`, not `exhausted`, or the band claims a limit the provider never reported.
- **SAY HOW THE FLEET IS DOING ONCE.** Three sections answered it three overlapping ways (`blocked` and `need you` fold the same workspaces) and spent a sentence on a denominator. One group of short figures, each with its one word; a zero figure is absent rather than greyed; the coverage that keeps a percentage honest rides the tooltip — a qualification is one hover away, not a headline. VS Code's status-bar guidance is the reference: short labels, icons only for clear metaphors, workspace-global items on the left together (which is why the runtime moved beside the project).
- **A guard anchored on the class under test cannot fail when that class is what was removed.** Mutation-tested: repainting the seam with the loud section token — the one change that undoes this whole hierarchy — left all 13 guards green, because the assertion sliced from `class="footer-seam`, got `-1`, and ran against an empty string. Locate the element by its **testid**, which survives a repaint, and assert both halves: it carries the quiet token, and it does not carry the loud one.

**`--edge-control` belongs to the SUBTLE tier, stated rather than implied.** This
section already said it "is not a 3:1 focus indicator", which describes what it
is not; the table says what it is.

**Decorative borders deliberately depend on their background (ADD).** Their current output is `color-mix(in srgb, var(--border-source) 60%, transparent)`, with the equivalent `--surface-edge-source` formula for enclosures. This replaces the previous opaque decorative edge policy. The source keeps the existing hue and RGB value while the other 40% comes from the actual surface under the border. Do not apply opacity to a parent, which would fade text and marks as well.

The opaque source tokens also preserve non-border derivations such as attachment gradients. Controls retain opaque resting outlines and unchanged input and focus indicators. Verify composite pixels on card bodies, interior regions, base headers and the sunken rail in both themes. The earlier decorative contrast figures above describe the opaque source, not the softened result.

Text on every rung, measured:

| Rung | primary | secondary | tertiary |
|---|---|---|---|
| light sunken | 14.75 | 6.85 | 4.56 ✓ |
| light base | 17.18 | 7.98 | 5.31 ✓ |
| light raised | 19.89 | 9.23 | 6.15 ✓ |
| dark sunken | 19.72 | 12.79 | 7.83 ✓ |
| dark base | 18.73 | 12.15 | 7.43 ✓ |
| dark raised | 16.86 | 10.94 | 6.69 ✓ |
| dark overlay | 13.76 | 8.93 | 5.46 ✓ |

**Two rows of that table were STALE and are corrected above, which is worth
recording because of how they went stale rather than for the 0.1 they moved.**
`light base` read 17.96 / 8.34 / 5.55 and `dark raised` read 17.06 / 11.06 /
6.77 — both computed correctly against fills that were later retuned
(`--surface-base` came down 0.965 → 0.950 so that a card would not sit on its
page separated by a hairline; `--surface-raised` moved with the dark ladder).
**A measured table is a snapshot of two values, and editing either one silently
invalidates it.** Re-derive the whole table when a rung moves; five of these
seven rows reproduced exactly, which is what made the two that did not
identifiable at all.

**Adopting the ladder REQUIRED retuning the tertiary tier in light mode, and that shipped first, alone, for that reason.** At shadcn's `0.552` it passed on the pure-white page (4.83) and failed the moment the datum moved (4.36) or the text landed in a well (3.59). The retune is `oklch(0.495 0.016 286)`, giving 4.56 / 5.55 / 6.15 across sunken / base / raised, and the table above is post-retune.

**It was applied to `--muted-foreground`, not to `--content-tertiary`, because tertiary aliases it.** Retuning the tier token alone would have left the migrated call sites dark and the unmigrated ones at 4.83 for the length of the migration — and it would have falsified §2's "adoption is a rename, never a re-pick", which is the property that lets tier deltas land ahead of the ladder at all.

**Resting control outlines and focus/input boundaries have different jobs.** `--edge-control` is a subtle secondary enclosure for controls already identified by readable labels or glyphs: light `oklch(0.82 0.008 286)`, dark `oklch(0.34 0.008 286)`. It is not a 3:1 focus indicator. `--input` and `--ring` retain the former stronger values independently (light L0.594, dark L0.56), preserving their contrast floors. The theme applies the quiet edge to native outlined buttons and select triggers only while unfocused and valid; text fields, focus, and invalid states keep their dedicated tokens. Match outlined controls by semantic element plus `data-variant`, not `data-slot`: tooltip wrappers replace the slot and otherwise silently restore the bright input border. Outlined controls have no shadow/halo; their opaque focus border remains the indicator.

#### The terminal palette: constrain the RANGE, do not pick sixteen colours

The 16 ANSI colours are `--ansi-*` in `globals.css`. One lightness per tier per
theme, one chroma, and only the hue varies — six hues at the ANSI angles rather
than the app's neutral 286, because a terminal's colour is semantic output (red
is a failure) and not chrome.

| Theme | normal | bright | measured band, all rungs |
|---|---|---|---|
| light | L0.45 C0.13 | L0.38 C0.16 | 4.81 – 10.12 (**2.1x**) |
| dark | L0.70 C0.13 | L0.82 C0.15 | 4.83 – 13.89 (**2.9x**) |

`fancy-ansi`'s defaults span **8.7x** and **10.9x**. All 32 committed values
clear 4.5:1 on every rung of the ladder in both themes; the defaults miss it on
**13 of 16** colours in light and 5 of 16 in dark.

**LIGHT WAS THE WORSE THEME, which is the opposite of how it reads, and the
reason is worth keeping.** Dark's failures are *dim* — `black` at 1.63:1 — so
they present as unreadable and get reported. Light's are washed-out brights —
`bright-yellow` 1.09:1, `white` 1.08:1 — which stay just legible enough to
register as "this looks poor" rather than as a defect anyone files. **A
contrast failure that is still readable is the one that survives longest.**

**Bright moves AWAY from the ground, never toward white.** On a light terminal
"bright" is darker and more saturated; on a dark one it is lighter. Taking it
toward white in light mode is exactly what produced the 1.09:1 yellow.

**The two neutrals are the binding values, not the hues.** `--ansi-white` in
light and `--ansi-black` in dark are each the dimmest ink in their theme, so
they set the floor — both were the only values to miss AA when the hues were
solved first and the neutrals eyeballed. Solve them like the rest.

**THE NAMED SIXTEEN ARE NOT THE PALETTE — the 256-colour greyscale is, and it
was found by looking at a real pane rather than by auditing tokens.** SGR
232–255 was the most common ink in a measured agent TUI (54 spans) at
**2.62:1**, because `fancy-ansi` emits `var(--ansi-gray-N, rgb(…))` and nothing
defined the variable, so the raw fallback won — 13 of those 24 greys miss AA in
light, 12 in dark. `--ansi-gray-1..24` now cover them, remapped rather than
reordered so a TUI's own shade hierarchy survives and only the range moves.
**An audit scoped to the sixteen colours anyone can name reports clean while
most of the screen fails.**

**SGR 16–231, the colour cube, is genuinely unreachable**: the vendor emits a
literal `rgb()` with no variable, so an agent using those opts out of the theme
and no CSS can reach it. Recorded so the next reader does not go looking for a
seam that is not there.

**So LIGHT MODE IS NOT FULLY SOLVED, and the residue is measured rather than
estimated.** On one live agent pane after this change, the themed greys read
9.77:1 (light) and 11.45:1 (dark) — but two unreachable inks remain in the same
pane: cube green `rgb(95,215,95)` at **1.59:1** and literal white
`rgb(255,255,255)` at **1.16:1**, both fine in dark and both failing in light.
That is the whole reason a terminal on a light ground is hard: **an agent TUI
is written for a dark terminal and says so in its own bytes.** Closing it needs
a `PORTED_FILES` port of the converter that maps the cube through the theme,
which is a bigger change than this one and has not been made. Do not report the
light terminal as fixed on the strength of the token table alone.

#### The focus ring is TWO layers, and only one of them is the indicator

**Correcting a claim this document and `0379622`'s commit message both made: "focus rings now clear 3:1" is true of the border and false of the halo.** The measurement was taken on the opaque token; the app draws two things.

| Layer | Class | Light (sunken / base / raised) | Dark (sunken → overlay) |
|---|---|---|---|
| the **border** | `focus-visible:border-ring`, opaque | 3.00 / 3.50 / 4.05 ✓ | 4.42 / 4.19 / 3.67 / 3.08 ✓ |
| the **halo** | `focus-visible:ring-ring/50`, 3px | 1.65 / 1.75 / 1.85 ✗ | 1.86 / 1.89 / 1.87 / 1.76 ✗ |

**Where both are present the control is compliant** — the opaque border is the indicator and the halo is a glow widening it. `button`, `input`, `select`, `tabs` and `badge` pair them.

**Where the halo is ALONE there is no compliant indicator.** Seven components: five vendored assistant-ui, `ui/scroll-area` (which does add `outline-1`, so it is fine), and **`grove/workspace/ticket-refs.tsx`, which is ours and also sets `outline-none`**, leaving a 1.8:1 halo as the entire focus affordance.

**DO NOT fix this by darkening `--ring`.** Measured: for the halo to clear 3:1 the token would have to fall to **L 0.238** in light and rise to **0.792** in dark, which makes the *opaque* border **12.28–16.56:1** and **7.47–10.71:1** — a near-black, then near-white, hairline on every focused control in the app. That disfigures the compliant majority to rescue the minority. **And the two-token escape is closed**: `ring-ring/50` is a vendored utility that names `--ring`, so a second token cannot reach it. The fix belongs at the halo-only call sites.

**A note on why dark looked like the worse theme, because the inference is wrong and worth killing.** It is not a theme inversion. Light's worst rung (sunken, 1.65) is *worse* than dark's worst (overlay, 1.76); the two independent measurements disagreed because they were taken on elements sitting on **different rungs**, not in different themes. **When a per-theme number surprises you, check which rung it was measured on before concluding the themes differ.**

#### The trap this ladder set, named so the next token does not fall in it

**A floor solved against the middle of a range is not a floor.**

`--edge-control` was first derived as *"the lightest value still clearing 3:1 on `base`, and it clears every lighter rung by more"*. That sentence is true, it sounds complete, and it is wrong — because it only ever looked **upward**. `sunken` is *below* the datum, and there the same token measured **2.50:1**. It is also the rung where controls most often sit: terminals, diff wells, code blocks, inset lists.

The ladder was authored thinking upward — the whole vocabulary was built to add a direction the app did not have — and the derivation inherited that bias silently. The corrected contrast-critical value, retained by `--input` and `--ring`, is `oklch(0.594 0.008 286)`: 3.00 / 3.66 / 4.05 across sunken / base / raised. Solve that boundary from the **extreme**, not the datum; the quieter resting `--edge-control` no longer makes this contrast claim.

**Solve every future token against the worst rung, and work out which end that is per theme rather than assuming.** The two themes put it at opposite ends: in light the edge is darker than every rung, so the worst case is the darkest one (`sunken`); in dark the edge is lighter than every rung, so the worst case is the lightest one (`overlay`, where `oklch(0.56 0.008 286)` clears 3.08:1). **That asymmetry is why neither theme's value can be derived from the other's** — the same nominal step lands on a different rung in each.

### 4.7 Colour is never the sole carrier

**Every state also carries a word, a glyph or a shape**, so it survives colour-blindness and monochrome rendering. Verified by simulation (Viénot–Brettel–Mollon matrices in linear RGB, plus luminance greyscale), reporting distance in sRGB units out of 255:

| Pair | Normal | Greyscale | Protanopia | Deuteranopia | Tritanopia |
|---|---|---|---|---|---|
| `--success` vs `--destructive`, light | 272 | **12** | 104 | 90 | 315 |
| `--success` vs `--destructive`, dark | 271 | **14** | 67 | **32** | 291 |

**These FAIL.** Success and destructive are chosen at near-identical lightness, so in greyscale they are 12–14 units apart out of 255 — indistinguishable — and in dark-mode deuteranopia they are 32 apart, which is close to it. A user who cannot separate red from green, or anyone reading a greyscale screenshot in a bug report, cannot tell "healthy" from "failed" **from the colour alone**.

What that means in practice, and what passes today:

- **`--heat-0…4` PASSES.** Greyscale values 235 / 217 / 187 / 156 / 127, with steps of 18 / 30 / 31 / 29 — monotonic in lightness, so the ramp survives intact. This is the payoff of it being a *sequential* scale rather than a set of hues.
- **`PhaseBadge` PASSES** and is the model to copy: `○ ◔ ◑ ◕ ● ✓` carries progress as a *shape*, in the same vocabulary the TUI prints.
- **The rail separates attention from phase.** The former trailing slot painted a phase character with agent-attention hue and chose its tooltip independently; that exception is retired. Each axis now has its own Lucide shape, label and color (§7), so waiting, agent-blocked, and error remain distinguishable in greyscale and a phase never turns red because of another axis.
- **`StatusBadge` / `AgentStateBadge` PASS**, because their tone comes from a variant table and every one of them prints the state as a **word**. The colour is redundant by construction.
- **Bare `text-success` / `text-destructive` on a number FAILS** — a coloured figure with no word beside it is carrying its meaning in hue alone. This is the concrete defect the simulation finds, and `35.9B` in `text-success` on the usage page is an instance of it.

The rule that follows: **a state colour may only ever be the second carrier.** Put the word or glyph first, then colour it.

- **Labelled change figures use semantic text, not filled badges (ADD).** Added lines and commits ahead use `text-success`, removed lines use `text-destructive`, outstanding dirty/behind/queued counts use `text-warning`; zero stays `text-content-tertiary`. Signs, units and exact-value tooltips carry meaning independently of hue. These are measurements, not a second state-badge system. The light tokens were retuned on 2026-09-06 for the worst text ground, the sunken rail: success `oklch(0.47 0.14 155)`, warning `oklch(0.49 0.14 75)`, destructive `oklch(0.48 0.245 27.325)`, each above 4.5:1 there. Dark tokens already clear 4.5:1 through the overlay rung and stay unchanged. The earlier 3.99:1 restriction described the old light token, not a permanent ban on meaningful numeric color.

### 4.8 The decision log

Every colour decision this app has made, including the ones that previously existed only as a comment beside the code or in a commit message. If you are about to re-litigate one of these, the reasoning is here.

| Decision | Why | Status |
|---|---|---|
| `--success` exists at all | shadcn ships `destructive` with **no positive counterpart**. Our semantic set fills a gap upstream does not. Without it every "good" signal reaches for a raw palette colour, which `lint:styling` forbids. | shipped |
| `--heat-0…4` ends exactly on `--success` | So the calendar and the headline token figure read as **one signal** rather than two systems. It is a *sequential* scale — the third category in 4.1. | shipped |
| `shell-panel` has **no border and no shadow** | Measured off the assistant-ui base demo. The cue is a pure tint step against the `bg-muted/30` layer the rail and gutter share, and **that step reverses between themes** — which is the whole reason `bg-card`/`CardShell` was wrong here. | shipped; **becomes the `raised` rung** |
| `surface-raised` takes the vendored composer's own shadow values | The plan card sits directly *on* the composer, and two elevated things touching must be lit the same way or they read as two systems. | shipped; **becomes the `raised` rung's elevation** |
| `surface-raised` is `box-shadow: none` in dark | The tonal step already lifts there; a dark shadow under it only muddies the edge. Same reversal as the shell panel, opposite instrument. | shipped |
| `--card` is the same white as `--background` in light | Not a decision — a **defect**, measured at 1.00:1. It is the no-headroom-above-white problem in our own theme, and the reason 4.5 exists. | **superseded by the ladder** |
| `scroll-edge-top` / `-bottom` and `rail-scroll-depth` give fixed chrome depth (ADD) | Persistent shadow gradients share `--scroll-depth-ink` in both themes and never intercept input. Clip to the positioned scroll region so adjacent panes and shell corners remain clear. The transcript top fade spans 1.5rem; rail fades span 0.75rem to fit its `p-3` gutter. The transcript's lower fade rises from the composer's top through the status region and 1.5rem beyond it, spanning the pane rather than the text column. Keep the status region transparent and its loader above the gradient so neither an opaque footer nor a changing queue creates a hard band. The rail uses inner `p-3` and intrinsic list height to clear both fades at its endpoints. The collapsed rail has no fades. | shipped |
| The `@` in `user@host` steps back furthest | It is **structure, not information** — punctuation joining two values the reader actually wants. Made on instinct; now a stated rule: *structural punctuation between two values takes the tertiary tier, below both values it separates.* | shipped; **see the note below** |

**How that rule lands is worth keeping, because it is the general answer to "I need a level the tokens do not have".** It shipped as `text-foreground` → `text-muted-foreground/60` → `text-muted-foreground`: the ranking was right and the *expression* was an alpha step invented below the floor, because two neutrals cannot say three things. The three tiers say it exactly — user primary, host **secondary**, `@` tertiary — so adopting §2 deleted the opacity rather than merely renaming around it. **An opacity step in Grove code is almost always a missing tier, not a missing colour**, and it matters more than it looks here: this one string renders on two different rungs (the rail's `base` and the menu's `overlay`), and an alpha neutral means something different on each while a token does not.
| Scrollbars are styled once, globally, unscoped | Every surface creates a scroller; a per-surface opt-in leaves whichever one nobody remembered wearing the browser default. | shipped |
| `--content-*` and the type ramp | See §1 and §2. **They shipped GLOBALLY ahead of per-surface adoption** — see the migration state below. | shipped |
| Terminal ANSI colour is themed, not exempt | The palette is `--ansi-*` (§4.6); the meaning stays the data's. The old exemption rested on "inline styles", which the shipped `dist` disproves. | **superseded** |
| `PhaseBadge`/`TicketRollupMeter` never spend a TONE on phase position, only on `blocked` | Their position is the grayscale-safe shape-and-word ramp, so `destructive` can mean blocked without contradicting a green or amber phase. In either shape, `blocked` is a flag across the phase axis, never a seventh position. | shipped |
| `PhaseMeter`'s track DOES spend a hue on position | **The original reasoning, kept so it is not re-derived:** a badge variant table (`secondary`/`default`/`outline`) ranked reached/current/ahead, on the argument that a tone spent on position leaves none for `blocked`. That held while the track's only carrier of rank was weight. It no longer describes the surface: the six connected checkpoints are one composite sequence, the glyph ramp and the content tiers *already* say which step the agent is on, so `--primary` on what is reached and `--success` on `done` is **redundant with the shape rather than load-bearing on it** — 1.4.1 is satisfied by the silhouettes, and removing every hue would lose nothing a reader relies on. `blocked` is still the flag across the axis, and still outranks the position hue. | **partly superseded** — the badge row above stands; the variant-table half does not |

### 4.9 The check only a human can run

Everything above is computed: OKLCH deltas, WCAG ratios, dichromat and greyscale simulation. **None of it is a verification on real hardware, and this document does not claim one.** A computed ΔL of 0.055 says two surfaces differ; it does not say you can *see* the difference on a five-year-old laptop panel at 40% brightness with a window behind you.

So run this, and report back rather than adjusting anything first:

1. **Set your least-good display to ~30% brightness**, in a bright room if you can.
2. **Dark mode, workspace page.** Can you see where the card ends and the page begins **without** looking for the border? If only the border is doing the work, `sunken → base` (1.05:1, the weakest step in the ladder) is too subtle and the dark rungs need widening.
3. **Light mode, usage page.** Can you see the card edges at all? This step is exactly ON the 0.05 floor with no margin, and it cannot be widened — see 4.4. If it does not survive real hardware, the answer is a redundant carrier (edge, shadow), never a bigger step, because there is no room for one.
4. **Either mode, a dialog over a card.** Are there visibly *three* planes — page, card, dialog — or two? `overlay` has no lightness step in light mode at all, so this is the step most likely to collapse.
5. **Greyscale the screen** (macOS: Display accommodations; or a browser filter) **and look at a workspace that has both a healthy and a failed state.** If you cannot tell them apart, that is the measured 12/255 failure in 4.7 showing up in real life, and the fix is a glyph, not a different green.

**What would falsify the ladder:** any step you cannot see at low brightness without hunting for the border. That is a real result and it means widen the step, not lower the standard.

---

## 5. Buttons

Four kinds, and the vendored `variant` that expresses each. No others — `secondary` and `link` are unused today and stay that way.

| Kind | Variant | Rule |
|---|---|---|
| **Primary** | `variant="default"` | **At most one per surface.** The single thing this screen is for. A screen with no such action has none. |
| **Secondary** | `variant="outline"` | Real alternatives beside the primary. Bounded, visible, but not competing. |
| **Tertiary** | `variant="ghost"` | Chrome: toggles, icon buttons, row affordances, menu items. Most buttons in this app are this. |
| **Destructive** | `variant="destructive"` | Irreversible. Always behind a confirmation, never the primary of a surface. |

- Sizes (**RECORD + composition**): Controls uses native `size="xs"` (h-6, gap-1, px-2), with a **24px rendered minimum** and icon plus label. `sm` remains h-8 upstream; never edit the vendored size table to rename it. The 80% root makes h-6 alone 19.2px, so the call site must retain the actual target floor. Page primaries and touch spacing keep their existing sizes. Key palettes compose `Kbd` / `KbdGroup` inside one real button per chord, with an explicit `+` separator; caps are labels, never nested targets.
- **Header bands use rendered pixels, not density-scaled spacing (ADD).** The shell header and work-tab strip have a 32px minimum. Coarse pointers keep the shell's `h-14` and a 45px work strip so a 44px target and 1px closing rule both fit. Terminal/Diagram sub-bars remain 32px. **The global status footer is the fourth band and takes 24px, 44px on a coarse pointer.** Each band owns exactly one `border-border` closing rule, with zero vertical gap; the pane switcher stretches inside the shell header and draws no second rule. `.workspace-tab-list` composes the vendored `ui/tabs` line variant; its trigger-owned underline ends on the band's closing rule, with no positional animation. Type remains `text-sm` on the rem ramp. The rail has a right rule and the split handle a full-height 1px rule.
  - **A BAND'S HEIGHT IS NOT A `h-*` UTILITY, and the failure is silent in the direction that reads as correct.** The status footer shipped as `h-6` and measured **19.19px** on the built page: `h-6` is 1.5rem, and the density root is 80%. Nothing failed — the band rendered, the sections laid out, every test passed — it was simply a sixth of its specified height, which reads as "a bit tight" rather than as a defect. The §1 rule that a px floor opts a surface out of the density lever is about TEXT; a band's height is a physical contract about a strip you aim at, so it belongs in `globals.css` as `min-height`, exactly as `.workspace-header` has always been written. **Check a band's rendered height in the browser before believing its class**, and keep the type inside it on the rem ramp.

- **A PERMANENTLY VISIBLE SURFACE IS A PUBLISHING DECISION (ADD).** Chrome that never scrolls away is in every screenshare, every screenshot and every recording, so what it prints is a different question from what a page prints. The status footer's subscription strip first rendered the daemon's own account labels, which on a real host are **email addresses** — simultaneously the longest strings in the band and the one value nobody chose to publish. It prints the PLAN instead (`Claude max 20x`), with the identity one hover or focus away and listed in full inside the popover, which is where a reader goes to tell two accounts apart. **The general test: would this value be fine on a projector for an hour?** A name, a path, an id or an address usually would not, and the answer is almost always a shorter fact the wire already carries rather than a truncation of the long one.
- **Tab labels yield before destinations (ADD).** Both workspace rows fit against their allocated width and actual label sizes, not viewport breakpoints: all labels → selected label only → icons only. Reserve the longest label in the middle mode so selecting a tab cannot change the density. Built-in tabs fit without scrolling on mobile; only an arbitrarily extended configured set may scroll after reaching icon-only. Each tab retains its stable accessible name, native tooltip, keyboard behavior and focus indicator. No trigger or glyph shrinks below its pointer target to fit.
- **Focus is an opaque edge plus a halo**, not the halo alone (§4.6). Compact controls retain the vendored focus classes and supply an actual border where the variant has none; field/value wrappers must not clip the ring. A smaller radius never licenses a smaller or hidden focus indicator.
- **An icon-only button always has an `aria-label` and a tooltip.** Use `TooltipIconButton` from `components/assistant-ui/`; it does both.
- **Anything clickable looks clickable** — a hover state that changes background or underline, plus the pointer cursor. **The cursor is handled globally and you must not add `cursor-pointer` at a call site** (see [the one OVERRIDE](#the-one-override)); `components/grove/**` carrying it is Grove code inventing a look. A row that navigates is a link, not a `div` with an `onClick`.

**ADD — The composer control.** Every control on a composer toolbar is one pill: a 1px `--edge-control` edge, the top-lit gradient reusing `--attachment-card-start`/`--attachment-card-end`, `--content-secondary` ink rising to `--content-primary` on hover and while open, and ONE shape for the whole row — a rounded square at the control role (3.45), attach, model, expand and send alike — never below the 24px pointer floor; icon-only controls sit at 28px. The vendor draws send and attach as circles and the model trigger as a rectangle, and that mix read as three families on one bar; a toolbar's controls are one family and take one corner. **Send keeps its ink fill and is exempt from the pill's edge and gradient**; a second treatment on it would argue with the one action the bar exists for. The rule lives at the theme boundary in `app/globals.css`, keyed by `data-slot` inside `[data-slot="composer-toolbar"]`, because three composers — landing, workspace reply, expand dialog — mount the same controls and each would otherwise decide separately. And a glyph floating in a bar is not a control: with no edge, nothing says where the target begins, which is §0's undesigned default wearing a hit area.

---

## 6. Badges

A badge is a **state mark** — that is what a TONE is for. It says something about this object that could change.

**A chip is also a BOUNDARY, and the tone table below already said so: `outline` means "a neutral fact worth marking: runtime, phase, count", none of which change.** So "not a state, therefore not a badge" was never the whole rule, and enforcing it literally cost the Info tab its structure — four constants demoted to bare tertiary spans separated only by a gap read as jumbled prose, because nothing said where one fact ended and the next began. An edge, a glyph and a shared baseline turn that line into a scannable set.

**The question is therefore not "is this a state" but "is this row a SET of facts or a SENTENCE".** A set takes chips, constants on `outline`, and rank carried by which single one holds a tone. A sentence stays text. What the older rule got right and still binds: **a fixed property never carries a tone**, and one descriptive label adrift in prose is tertiary text, not a pill.

### One component — and what the other one is for

**`@/components/ui/badge` is canonical for all Grove code.** Two reasons, and the second is binding: `components/grove/fleet/tokens.ts` already types its state tables against its variants, and `assistant-ui/badge` carries raw palette colours (`bg-blue-100`, `bg-amber-100`, `bg-emerald-100`, `bg-red-100`) that `lint:styling` would reject if a Grove file wrote them itself — importing them **launders a violation** through a vendored file.

**`components/assistant-ui/badge.tsx` is not deleted and must not be.** It is upstream source that assistant-ui's own components consume, and `registry:check` re-fetches and diffs it. It stays vendored, unedited, and unimported *by Grove code*. That is the whole distinction: a vendored file existing is not permission to compose with it. **The rule is about Grove's import list, not about the file** (#489).

The measured cost of not having had this rule: the Info tab renders `outline` ×3, `muted` ×8, `warning` ×1 and `ghost` ×1 in one 41-element panel — four variants, three of which exist only in the non-canonical component, encoding nothing a user could learn.

### Four tones, and what each one means

| Variant | Weight | Means |
|---|---|---|
| `default` | Loudest — solid | **Live right now.** An agent that is working. At most one per object. |
| `destructive` | Loud — solid | Failed, errored, or needs a human |
| `secondary` | Quiet — filled | A settled, unremarkable state: idle, paused |
| `outline` | Quietest — hairline | A neutral fact worth marking: runtime, phase, count |

- **A variant is never chosen at a call site.** It comes from a `Record<State, BadgeVariant>` table, the way `fleet/tokens.ts` maps every workspace status, agent state and runtime. A call site that picks a variant is a call site that will disagree with the next one.
- **A COUNT ON A CONTROL is the documented exception, and it takes `secondary`.** The filter buttons badge how many criteria are active — there is no state union to key a `Record` on, and the tone table above sends a count to `outline`, which would nest a hairline badge inside a hairline button and read as a rendering artefact. Both filter menus already agree on `secondary`; this records that rather than "fixing" them apart. The distinguishing question is whose state the badge reports: **an object's state comes from a table; a control's own count is chrome on the control.**
- **One `default` per object, total, across all its axes.** Today a fleet card renders `active` and `working` both as `default` — two loud marks for one fact. The workspace status axis keeps `default`; the agent axis drops to `outline` when the status badge is already shouting.
- **When a new fact would push a row past its tone budget, fold it into an existing mark rather than adding a chip.** The tickets row carries four marks (an uncertain-link flag, the tracker's own status, Grove's phase, and blocked) on a budget of three toned badges — solved because `blocked` rides the phase badge's own tone and glyph instead of arriving as a fifth chip: it is a claim about the SAME axis (how far Grove got), not a new fact needing its own object. A row is over budget only when it needs a genuinely new axis, not a new state on one already present.
- **At most three TONED badges in a row; a uniform `outline` set is bounded by the row, not by a count.** The limit exists because competing loud marks cancel each other out, and chips that all read the same are not competing — the Info tab's identity row runs to five (status, runtime, root, agent, fallback) and stays legible precisely because only one of them is toned. Toned marks past the third belong in the tooltip or the detail view.
- **When a row's LENGTH is user-controlled, the tone budget has to be met by design, not by luck — put the state on the glyph and leave the chip neutral.** A fleet card carries however many tickets are attached, so a chip that took `ticketStatusTone` would put five toned marks in one row on a workspace nobody would call unusual, and no per-chip rule can prevent it. `TicketChip` therefore splits the two facts it holds: which tracker and which number is the ticket's IDENTITY, fixed, so it takes no tone by the rule above; whether the tracker calls it open, merged or closed is a state, and it rides `ticketGlyph`/`ticketStateColour` — shape first, hue second, per §4.7 and §7's table. The chip itself is `outline` on every ticket, so a five-ticket row spends nothing from the budget and the card's whole allowance stays with the status axes. **This is not a disagreement with the Info tab's `TicketRow`**, which does spend `ticketStatusTone`: there the badge holds the tracker's own WORD and IS the state mark; here the badge is the identity and the glyph is the state mark. Same vocabulary, two jobs — and the test for which you are looking at is *what is written inside the pill*.
- Badges are `text-xs` `font-medium` from the canonical component, at the **inner-cell corner (2.3)** — a badge is a cell, not a circle, and §4.3 spends `full` on circles alone. The component's own `rounded-full` is not a decision it exposes a variant for, so the corner is supplied by the `data-slot` block in `app/globals.css` rather than by editing upstream or by a call-site override; a badge anywhere in the app therefore has the same corner without any Grove file naming one. The nominal 12px type scales with the root (§1); it is not a claim of 12 rendered pixels.
- **Fleet badges share one compact density (RECORD):** the canonical vendored badge at `h-5 px-1.5 py-0`, retaining its 12px type, icon, radius and focus behavior. Identity remains outline; ordinary live lifecycle is secondary. Attention retains destructive. Working/checklist-in-progress are neutral, complete checklist uses `text-success` rather than a green fill. The workspace title is neutral primary text, not a primary-action orange link. There is no requirement to spend the available tone budget.

### Progress marks: an aggregate is a magnitude, a single claim is a position

**A plain accumulating bar (`Progress`) and a checkpoint track (chips joined by connectors) answer different questions, and picking between them is not a style choice.** A single claim — one ticket's phase, one workspace's phase — has a POSITION: which step is it on, and a track is the only shape that can say "this one, not that one" the way a fraction alone cannot. An aggregate over several claims — a workspace's whole batch of tickets — has no position to show, only a magnitude: how much of the batch is done. Reach for `Progress` there, and never for a single claim, where a bar that can only grow reads a legitimate backward report (`verifying` back to `planning`) as breakage.

**A single claim may carry BOTH at once, and that does not weaken the rule.** Position lives on the track; magnitude lives on the connector leaving the current node, which is the only span whose length is in doubt. The direction the rule protects is the other one: an aggregate still never gets a track, because it is standing on no step.

**A sub-step magnitude needs a denominator somebody REPORTED.** Elapsed time is not one — a phase has no expected duration, so a clock-driven fill invents a denominator and then implies lateness from it. The agent's own checklist is the only honest source, and where none was reported the segment is ABSENT rather than zero: §11's unmeasured-is-never-`0` rule, one level down from the figure it usually governs.

**The phase track stays horizontal and all six labels remain visible.** At a task-card container width of at least 420px, use full phase names at 12px. Below it, use Scope · Plan · Build · Verify · Deliver · Done at 10px—the explicit compact-label exception to the type floor. Only the current label is bold. The current phase and step count belong in the card header's action slot; the workspace note remains below the track.

Use the card's named container, never the viewport, for that threshold. Verify all six abbreviated labels at a 300px pane: the split pane is the normal reading width, not an exceptional fallback. Do not hide labels, free-wrap the sequence, or replace it with a vertical list.

**The full workspace note sits below the track as readable prose, and each latest per-ticket note sits on the ticket ROW that names it** — not in a second list on the task card, and never tooltip-only. Subscription windows likewise use container-driven columns, with a scan section for meters and a separately labeled metrics section; labels yield before controls.

**Position and magnitude remain distinct without filling either amber.** Phase uses its shape and word; checklist uses its fraction and checked-list glyph. Completed checklist text may use success; zero and in-progress counts stay neutral. Attention is the exceptional colored fill, not ordinary work.

**An aggregate must still say what fraction of it is silence, not just what fraction is done.** `TicketRollupMeter` scores an unclaimed ticket as zero rather than excluding it — excluding would let a workspace holding five untouched tickets and one finished one read 100% complete. Zero can only ever understate, so the honest fix is stating the coverage beside the number (`N not reported`), the same `Showing 100 of 2.5K` discipline §10 already asks of every partial read.

**Where TWO parties make claims about one object, the copy keeps them apart by ATTRIBUTION, never by wording.** A ticket row shows the tracker's state beside Grove's phase, and `closed` next to `scoping` reads as a contradiction the reader has to resolve — until every sentence names its claimant: *"The agent is reading the ticket…"* against *"Gitea says this issue is closed."* Then it is two true statements, which is what it always was. Merging them into one sentence is the conflation the explanation exists to undo, so the shared `phaseTooltip` composer returns the claims **separately** and both the ticket row and fleet phase badge stack them; concatenating in either surface makes the halves untestable and invites a single voice to smooth over the disagreement. A state Grove cannot normalize quotes the tracker's own word rather than inventing a controlled one.

**A progress tooltip leads with the agent's per-ticket note, verbatim, then states the phase meaning, the blocked reason where present, and the tracker-attributed claim.** The note is the only firsthand task context and must not be paraphrased or buried under Grove's explanation; quotation and italics keep it visibly somebody else's words. The fixed sentences that follow give an unfamiliar reader the phase vocabulary, state why it cannot advance, and then make the independent tracker claim legible without pretending the two systems agree. A missing note simply omits that row — it does not invent a substitute.

**A mark that encodes a position in a vocabulary the reader was never taught needs the vocabulary, not a louder mark.** `◐ 3/6` says how far along without ever saying what "along" measures, which is why a phase badge reads as an arbitrary process. The definitions belong in the hover, written once — the same six sentences the agent's own skill defines, turned from second person into third — because two independent explanations of one word teach it twice and trust neither. `Badge` is a bare `<span>`, so an explanation carried only by `title` is mouse-only: the trigger takes `tabIndex`, and the `aria-label` carries every claim the tooltip shows.

---

## 7. Icons

**Lucide is the default vocabulary.** `components/icons/github` and `AgentMark`'s vendored `@lobehub/icons` leaves carry brand artwork, not generic vocabulary. Model and tool values may resolve an Iconify slug through `AppIcon`; tool catalog defaults use colorful Material Icon Theme, VS Code and Flat Color Icons artwork, while supplementary MCP mappings may name a server's own mark. These fixed identity colors do not indicate success or failure; provider-state words retain that job. An unavailable slug keeps a stable fallback footprint. `react-file-icon` renders file-type artwork, not chrome.

**`AgentMark` imports each provider's `Color` leaf directly, never the compound icon and never the package root.** A brand mark's fixed hue is the concrete case of §4.1's **Identity** row (`--chart-1…5`, brand marks — "a fixed property of the thing"), so a vendored `fill` baked into imported path data is not a colour decision made in `components/grove/`; `lint:styling`'s ban is on a *class* fixing a colour in this tree, which a `<path fill="#…">` arriving from `node_modules` never is. The leaf import is load-bearing, not stylistic: the compound object's `.Avatar`/`.Combine` variants eagerly import `@lobehub/ui`'s `@emoji-mart` dependency, which fails to load under Vitest's Node ESM loader and would otherwise ride the bundle on tree-shaking alone — `@lobehub/icons/es/<Provider>/components/Color` touches only the one leaf (`react`, its own `../style`, and, where the artwork needs a gradient, `../../hooks/useFillId`), the same shallow profile `Mono` had. **Codex's brand colour is a blue gradient (`#3941FF`→`#7A9DFF`→`#B1A7FF`), not the near-white this section used to claim** — its `Color` leaf also draws its own opaque white rounded-square backing path behind the gradient mark, so the rendered glyph always carries its own contrast plate and reads on both the dark rail and the light theme regardless of what sits behind it. `openai` has no vendored `Color` leaf in the pinned version (`style.js` exposes per-product hex constants but no component), so it alone stays on `Mono`. `claude` renders lobehub's generic `Claude` mark, not the tool-specific `ClaudeCode` one — reversed deliberately: people recognise the Anthropic mark far more readily than a CLI-specific redraw of it, so recognisability now outweighs naming the exact binary. `codex` still keeps its own vendored mark rather than borrowing `OpenAI`'s, since Codex and OpenAI stay separate `AgentBrand`s (`fleet/tokens.ts`).

- **Always the `*Icon` spelling** — `GitBranchIcon`, not `GitBranch`. Both resolve, which is exactly why they drift and why no gate catches it. **The tree is fully swept; the census is the scan, never a number written here** — parse every `import { … } from "lucide-react"` under `components/grove/**` and `app/**` and check each name ends in `Icon`. It must read multi-line imports: three files declare theirs across several lines, and a single-line regex reports a clean sweep while never having looked at them.
- **One stroke weight and one optical size.** `size-4` beside `text-sm` body; `size-3` inside a badge or an `xs` button; **`size-[1em]` for any glyph inline with text**, which is what makes a mark read the same on a 13px card and a 12px rail row. Never a pixel size on an inline glyph.
- **Optically aligned:** an inline glyph gets `align-[-0.125em]` and `shrink-0`; the text beside it gets `truncate` on a `min-w-0` row. **The glyph must never be the thing that shrinks** — a clipped word is legible, a clipped icon is a smudge, and it is the part carrying the type.
- **Decorative icons are `aria-hidden`, and the meaning is restated in `sr-only` text.** A screen reader announcing "folder git 2 Grove" is worse than nothing.

**Transcript/footer boundary.** Transcript rows scroll only above the composer region. The entire footer—including working status, plan, queue, pending questions, gaps and composer—has opaque `surface-base` backing in each theme. It is a flex sibling, never a translucent sticky overlay on messages. Both columns consume the same measure and inset; unusually tall footer content scrolls within a bound so the transcript retains usable height.

**Tool timelines use the upstream action-row anatomy (RECORD + composition).** A `text-sm` verb and `size-5` mark lead a `text-xs` monospace preview capped at `max-w-64`; state, duration and the chevron sit beside the action. The summary carries a horizontal glass capsule with a straight centre and rounded ends. Light and dark themes have independently selected container, coin-base and edge values: never an opaque white plate in dark mode. Each icon sits on its own accent-tinted circular coin. Preserve the compact overlapping stack rather than turning it into a spaced icon row; glyphs paint above every coin rim and never cover each other. Cap the marks with an explicit `+n` remainder. Opening retains the entire stack and its geometry while reducing only the shell's opacity; glyphs and coins remain legible at full opacity.

Steps, nonzero command/read categories, and changed-file counts follow the stack. Omit zero command/read categories; category-free runs use the cataloged single action or `N steps executed`. Edits stay inside the run. Their expanded body has a small `mt-1.5` breathing gap before a non-collapsible framed card with a path header and `+n −n` totals; the step is its only disclosure. Empty-original edits use native unified rows, other edits native split rows. Per-file chips sum edits at the group's footer. Both triggers retain keyboard focus; long targets have their full value available, and bodies have no height cap. Expanded groups use `ps-2 py-1`; request/response wrappers use `ps-3 pt-0.5 pb-1`.

### The fixed glyph per entity

Each of these means one thing, everywhere, forever. Adding a row is a change to this table, not a decision in a component.

| Entity | Glyph | Owner |
|---|---|---|
| Project (a repo) | `FolderGit2Icon` | `ProjectLabel` in `components/grove/entity.tsx` |
| Branch | `GitBranchIcon` | `BranchLabel` |
| Location (a path on disk) | `MapPinIcon` | `LocationLabel` |
| Agent | its brand logo | `AgentMark` in `components/grove/agent-mark.tsx` |
| Model | `CpuIcon` | call site |
| Session | `HistoryIcon` | call site |
| Workspace / fleet | `TreesIcon` | `shell/nav.ts` |
| Usage | `ChartColumnIcon` | call site |
| Runtime: container / host | `BoxIcon` / `ServerIcon` | `fleet/badges.tsx` |
| Uptime / duration | `TimerIcon` | `Uptime` in `relative-time.tsx` |
| MCP server | `ServerIcon` | call site |
| Skill | `SparklesIcon` | call site |
| Issue (a ticket) | by state, below | `ticketGlyph` in `workspace/selectors.ts` |
| Pull request | by state, below | `ticketGlyph` in `workspace/selectors.ts` |

**`CpuIcon` names the model CONCEPT; a model VALUE wears its own brand mark.** A specific id resolves to per-value data — `adapters/model.ts` maps a family word to an Iconify slug and `AppIcon` renders it — with `lucide:key-round` as the fallback, so an id no table recognises still gets a mark of the same size and a picker row never shifts when the real one lands.

### Fleet state — one word and mark per axis

Every workspace lifecycle state and agent state has one sentence-case label and one Lucide mark in `fleet/tokens.ts`; the rail, filters, cards and palette consume that total table rather than spelling or decorating a state locally. The ordinary-English labels need no hover explanation. Only `provisioning`, `offline`, `orphaned`, and the agent's `blocked` state take the glossary, because they carry a Grove-specific meaning a newcomer cannot reliably infer. `active` and wire-level `running` deliberately render as the same reader-facing state, **Active**, with the same radio mark: the distinction is an implementation detail, not a second condition to teach.

The lifecycle and agent tables share `LoaderCircleIcon` for starting work and `CircleXIcon` for error, because those are the same claim on distinct axes. Runtime belongs to the same presentation owner (`ServerIcon` / `BoxIcon`) but retains its fixed-property `outline` treatment.

**One mark per axis, one native explanation owner (ADD).** Sidebar attention and task phase never share a priority-selected slot or a hue. Attention is question (`MessageCircleQuestionIcon`), agent blocked (`OctagonAlertIcon`), or failure (`CircleXIcon`), with distinct explanatory labels from `AGENT_PRESENTATION`, all destructive. Phase uses the six distinct Lucide silhouettes from `PHASE_PRESENTATION` through `phaseGlyph`: dashed circle, dot circle, play circle, big-check circle, send, check circle. The font-dependent `○◔◑◕●✓` ramp is retired on the web. Phase is tertiary, success when done, warning with its corner flag when phase-blocked—never the attention red. The corner flag is positioned within the icon's fixed-size wrapper, never against the whole label row. Each mark retains its accessible name; the row's single native hover/focus tooltip explains both claims.

The header reads logo → title, with a reserved options corner. Body context reads branch → attention → phase at intrinsic widths. Missing states occupy no slots, and the title does not move when they appear. Pointer hover and keyboard focus reveal options; touch keeps the action discoverable.

### Ticket state — the one entity whose glyph varies

A ticket is the only row in the table above whose mark depends on more than what it IS, because a forge's whole visual language is that an issue and a *closed* issue are different objects at a glance. The pairing below is the convention every developer already reads without being taught it, so Grove copies it rather than inventing one.

| State | Issue | Pull request | Colour |
|---|---|---|---|
| open | `CircleDotIcon` | `GitPullRequestIcon` | `--success` |
| merged | *n/a — see below* | `GitMergeIcon` | `--merged` |
| closed | `CircleCheckIcon` | `GitPullRequestClosedIcon` | `--destructive` |
| draft | `CircleDashedIcon` | `GitPullRequestDraftIcon` | `--content-tertiary` |
| unknown | `CircleDotIcon` | `GitPullRequestIcon` | `--content-tertiary` |

- **The shape is the first carrier, the WORD is unconditional, and the colour only agrees with them** — §4.7's rule, and the reason the glyph changes per state rather than only the hue. The state is spelled out beside the mark on every surface that draws one: `Open`, `Closed`, `Merged`, `Draft`. On the Info tab's ticket row that word is the coloured text on the tracker's own metadata line rather than a badge, which spends no tone at all and leaves the row's whole budget for §6's marks (measured on the card surface: light 6.22 / 6.32 / 5.71, dark 7.04 / 5.92 / 6.54 — all clear of the 4.5:1 floor).
- **MERGED WINS OVER CLOSED, and the precedence is the TRACKER'S rather than ours.** A merged pull request is also a closed one, and `closed` is the weaker of the two true statements — so the normalization reads the tracker's own word and lands on `merged` whenever that word is what arrived.
- **`TicketState` is GROVE'S union, not a wire enum.** `TicketRef.status` is `string | null` on the wire — free provider text (`open`/`opened`/`reopened`, `closed` vs `done`) — so there is nothing to key a `Record` on until it is normalized. `ticketState()` does that once, and every table above is total over the result. **Where a wire field has no enum, the compile-time guarantee has to be manufactured; say which side of the seam you are on.**
- **An issue is never `merged`, and that is now enforced at the normalization rather than only survived at the glyph.** `ticketState()` takes an optional `kind` and degrades a `merged` word on an issue to `closed`, so the word, the hue and the shape agree instead of a purple `--merged` figure sitting beside a closed check. The cell in the table above stays filled anyway, because a partial map is a map that stops catching new states. **The direction matters:** merged is inferred from the tracker's word, never from a state we reasoned our way to, so the narrowing only ever removes a claim.
- **`unknown` is a real state, not a null.** A tracker that answers a word we have never seen still has one, and the row still has to draw something: the generic mark, the neutral tier, and the tracker's own word spelled out.

**This table is written in the `*Icon` spelling because the rule above says so, and a table that disagrees with its own rule is read as the exception.** It named its glyphs bare — `FolderGit2`, `GitBranch`, `MapPin`, `Trees`, `Box`/`Server` — in the paragraphs directly under a rule demanding the suffix, which is how a rule dies: nobody disobeys it, they just copy the nearest example. The code and this table were swept together.

**Where a glyph belongs to an entity, the component owns it and takes no `icon` prop.** A caller that can choose the glyph is a caller that can disagree with every other caller — this is why `entity.tsx`, `agent-mark.tsx` and `relative-time.tsx` have none.

---

## 8. Cards

**`components/grove/card.tsx` is the only card system.** It is the only Grove file that imports the vendored `Card`, and the only place radius and elevation enter Grove code. Do not compose a card from `Card` directly, and do not add a prop to these — a different-looking card is a different *composition* of `CardShell`.

**The scope is `components/grove/**`, and `app/**` is deliberately outside it.** These primitives are built for a *dashboard* card — one of twelve in a grid — which is why `CardShell` resets the vendored `gap-6 py-6` rhythm to `gap-0 py-0` and hands padding to its children. A page whose card is the only thing on the screen wants that rhythm back: `app/login/page.tsx` composes the vendored `Card` directly and is **correct** to. Forcing it through `CardShell` would strip the padding it depends on to solve a density problem it does not have. So this is not an exemption anyone has to earn — it is the rule stating which problem it solves. A one-card page is a different problem from a card wall.

| Piece | Use |
|---|---|
| `CardShell` | The container. Nothing else. |
| `SectionCard` | A titled section: tinted header band + rule + body |
| `CardDisclosure` | A row whose detail is behind a click |
| `CardGrid` | The page canvas — bounded, scrolls internally, `@container` |
| `CardScroll` | A list that bounds itself (`max-h-*`, never `h-*`) |
| `CardRegion` | A bounded, named region inside a body |
| `CardCell` | One bounded fact: glyph + label over a figure |
| `CardFields` / `CardField` | Term-and-value rows inside a body |

**Header behaviour.** A `SectionCard` header names a **topic**; it is not for everything with a title. A header that names the **object** — identity plus state in a tinted bar — reads as a title bar for a thing, which is right for a fleet workspace card and wrong for a one-word stat tile. A stat tile takes `CardShell` and composes its own rows. Same principle, opposite answers; that contrast is the test.

**A tinted band and a plain header are TWO COMPONENTS FOR TWO JOBS, and unifying them is the trap.** They look like the same thing with a setting turned off, and they are not: the band says "this is a title bar for an object", and type-and-spacing alone says "this is the label of a figure". A stat tile that grew a band would claim an identity it does not have; a fleet card that lost one would stop reading as a thing. **So a plain header is not a `SectionCard` with the tint disabled — there is no such prop, deliberately.** If you find yourself wanting one, you are about to merge two decisions that were made separately and correctly.

**Anatomy, fixed:**
- Title `text-sm font-medium text-content-primary`, truncating on a `min-w-0` row.
- Header `surface-header px-3 py-1.5`, laid out as **one flex row** — title and icon gutter at one end, the action at the other, on a single baseline. It is not the vendored two-row grid: that grid exists to put a description under the title, this card's description moved into the body, and the surviving empty second row is what dropped the action half a line below the title it belongs beside. A header holding one row of content is a flex row.
- In light mode the band is a gray→gray top-to-bottom gradient from `--surface-header-start/end`, **amplitude 0.05 L** — the same step the surface ladder uses, because a narrower one is not a subtle band, it is an invisible one (0.015 L measured as flat chrome on both themes). A 1px `--surface-header-highlight` catch-light, lighter than the start, sits on top: a band lit from above has an edge, and without it the gradient reads as an unfinished fill. In dark mode the three values are `oklch(0.303142 0.006 286)`, `oklch(0.255779 0.006 286)`, and `oklch(0.350505 0.006 286)`. Each has 85% of its former WCAG relative luminance. Headers only. Forced colors replaces the whole thing with `Canvas`. At the darker stop, primary title must clear 4.5:1 and icon 3:1 in both themes.
- Description `text-xs text-content-tertiary`, **first in the body**, wrapping freely. A flush body pads this prose independently so the following list can remain full-bleed. Description length never determines header height.
- Body `text-sm text-content-secondary`. Message disclosures are reading surfaces: mailbox body `text-base` with native Markdown, sender/recipient and subject `text-sm`, delivery metadata `text-xs`. Mail is collapsed by default even when it is one line; the identity stays visible, the full body mounts on expansion.
- Field labels `text-xs text-content-tertiary`; field values `text-xs text-content-primary`.
- Spacing is three values and no others: `p-3` inside a body, `gap-2` between a body's rows, `gap-3` between cards.

**A card body is NAMED REGIONS in a fixed order, never one flow — and the reason is wrapping, not tidiness.** The fleet card put the agent name, four counters and every attached ticket in one `flex-wrap` row, and the complaint it produced was that the card's contents sat "in random places": a five-ticket workspace flowed its chips out of the middle of a run of figures and down three ragged lines, while a one-ticket workspace read as a different component. **Wrapping is a property of a SET**, so a row holding three sets cannot wrap any of them correctly — chips want to wrap as chips, prose as prose, figures as a baseline. The fleet card separates identity, Grove status (phase/activity and checklist on one line, reported note beneath), a compact horizontal change ledger, and an agent/runtime footer. The vendored `Separator` distinguishes ledger and footer from prose; each set wraps independently. No region reserves empty height. Optional detail disappears when absent, but the status region explicitly names an unreported status — it never substitutes the last prompt. Give every region a `data-testid`: the order is the contract, and it is the only part a static render can hold.

**A REGION IS AN ENCLOSURE AND A CELL IS A PAIR; NEITHER IS A HEIGHT (ADD).** The named-region rule above says where a set begins and ends; `CardRegion` and `CardCell` are what DRAW that boundary, so a surface stops needing a rule across the card or a heading over every group. Both compose `CardShell`, which is what keeps radius and elevation in the one file that owns them, and both take `bg-muted/30` — an alpha over the surface, so the separation reverses correctly between light and dark for the reason §4.5 gives.

Three things about them are contracts rather than styling:

- **The perimeter groups a label and a value; it does not make them a control.** A read-only cell has no pointer, no hover elevation and no tab stop. Where a term needs defining, the definition rides the LABEL through `Explain`'s dotted affordance, so a grid of six facts gains at most the two stops its vocabulary earns rather than six.
- **A reference height is a MINIMUM.** `CardCell`'s 64px is `min-h-16`, and its geometry is a sum a reader can check: 10px inset + a 16px glyph 4px from a 12px/16px label + a 20px/26px figure + 10px inset. Under 200% zoom or a longer translation the cell grows; a fixed height would crop the thing the cell exists to show. Same rule one level up: a region has no height at all, so prose inside it wraps to whatever it needs. **A bounded region is a visual enclosure, never a crop** — the only bound that may hide content is `CardScroll`, and it must say what it is bounding (§10).
- **No new numbers.** 12px inside a body, 12px between cards and 8px between siblings on BOTH axes is the existing three-value scale (`p-3` / `gap-3` / `gap-2`) read at one more level of nesting, and `CardCell`'s 10px inset is the one addition — a cell is smaller than a body and takes the step below it.

**`CardCell` REPLACED `CardStat`, and the replacement was a deletion rather than a second option.** Two labelled-figure primitives is two answers to one question, and the wrong one gets picked by whichever example the next author read first. Every surface that had a row of figures — the Info tab's Activity, the Changes tab's Divergence — is on the cell now, and `CardStat` was removed the moment its last caller left. **A component with no call sites is not a spare; it is a fork waiting to happen.**

**A GRID OF FIGURES MUST DIVIDE WITHOUT AN ORPHAN AT EVERY STEP IT TAKES.** `auto-fit` is what these surfaces used, and it cannot promise that — it flows to whatever fits, so a five-fact card lands 4+1 at one width and 2+2+1 at another, and the lone cell reads as an error. The counts are known (six or four on Activity, five or three on Divergence), so the column steps are authored to match them: 6→3→2, 4→2, 5→3→2, 3→2. **A withheld fact removes a cell; it never leaves a placeholder** — the grid is one fact shorter, not one hole longer.

**A header that names a topic and then explains it costs a row on the densest surface in the app.** The Info tab's three cards dropped their `description` bands, and the trailing slot carries the SCOPE or the RECENCY instead — `Primary session`, `Updated 4h ago`, `2 linked`. That is the fact a reader needs beside a title; a sentence restating the title is the fact they already had. A `description` still earns its place where the topic genuinely is not self-evident, which is why the prop stays.

**Grouping is a heading over matching objects, not a tile for every configured project (ADD).** The shared `ProjectHeading` uses `text-xs font-medium text-content-secondary`, a typed `ProjectLabel`, and a tertiary workspace count. Only nonempty groups render. The rail separates projects with heading spacing and one vendored `Separator` between groups; each workspace composes the shared card material. Inactive lifecycle rows still sort last; lifecycle does not control dimming. Group structure and selection have distinct visual cues. Search lives in the Grove brand band and opens the single native command dialog. Below the project-context picker, New workspace fills the flexible part of one action row beside Filter and Pause. New workspace, Search, Filter, Pause, and the header's sidebar toggles use native `outline` buttons with transparent light/dark resting fills and `border-edge-control`; none carries a primary-action color fill. Compact Search/Filter/Pause/collapse targets are 24px squares on fine pointers, as is New workspace when collapsed; touch retains 44px minima. A single subtle 1px contour replaces shadow/halo stacking. The persistent search field is gone. The context starts at All projects, preserves configured-CWD identity even for an empty group, and is a visit-scoped filter rather than navigation. Grouping remains a setting inside Filters, never the control's name.

### The rail's session row: compact, intrinsic, and stateful (ADD)

**A row has three authored lines: title, context, ledger.** `CardShell` is raised; its `surface-header` title band holds a `size-5` agent mark and `text-base` title, while the body is `text-sm`. These steps stay subordinate to workspace reading surfaces rather than using display-heading sizes for navigation. Every session uses body `py-2` for the same expanded breathing room, whether selected or not. Selection changes emphasis, never padding or geometry. Cards, groups and vertical control gaps share `gap-3`, matching the scroller's `p-3` edge gutter. Horizontal action and metadata gaps remain unchanged. Heights grow with content and enlarged text, never a fixed crop. Unselected cards use a restrained `opacity-95`, returning to full opacity on hover/focus; selection stays full. This narrowly scoped emphasis preserves semantic hues and must retain 4.5:1 composite text contrast on its actual card/rail surfaces.

**Context is intrinsic: branch → attention → phase.** The branch is `min-w-0 flex-[0_1_auto]` and may marquee; attention and phase are independent `shrink-0` groups. Use `gap-2` between groups and `gap-1` within a glyph-label pair. Phase is always worded, including Done; a blocked phase adds its warning flag and never borrows destructive attention tone. Missing facts take no placeholder or reserved slot.

**The ledger is compact, static, and honest.** Branch changes are `tabular-nums` figures at intrinsic width; the only flex remainder is the ledger before the intrinsic creation age. Figures never clip, scroll, or pretend an unknown is zero; the ledger wraps whole figures when user-enlarged text exceeds its width. One native row tooltip owns identity, branch, state claims, exact metric scopes, and timestamps on hover or keyboard focus. It requests `side="left"` with an 8px offset; native collision handling flips it to the right at the window edge rather than placing it over the row. Individual values retain accessible labels, not nested tooltip triggers that open competing popovers. The creation age identifies itself there, while list order remains last activity.

**Row options reserve only their own title corner.** The sibling menu button has a real pointer target, matching header minimum height, and title padding, so revealing it never covers text or moves the header. Compact targets stay at least 28px and coarse-pointer targets at least 44px; touch reveals the action without hover. This is an action reservation, not permission to reserve absent state groups. Body context never uses fixed slots or `space-between`.

**A destructive verb may sit in that menu, behind the SAME confirmation the rest of the app uses.** The rail's Delete opens `KillDialog`, not a second dialog with its own wording — two dialogs asking the delete-the-branch question differently is how a user learns to distrust both. The trailing ellipsis on the menu label is load-bearing: it promises the confirm step that licenses a destructive item one click from a navigation row.

**The project name is not on the row.** Grouped, the heading above already names it; flat, the row tooltip and accessible description preserve it without adding a fourth line.

**Six states, and no geometry moves between any two of them.** The border is 1px in all of them and only changes colour; focus is an `outline`, which does not participate in layout.

| State | Carrier |
|---|---|
| rest | raised card body, `surface-header` title band, `border-surface-edge` |
| hover | `bg-surface-base`, `border-edge-control` |
| pressed | `bg-accent` |
| selected | `border-primary` + a 24×2px `bg-primary` marker at the left edge, `aria-current="page"` |
| selected + hover | the hover tint, with the clay edge and the marker kept |
| focus-visible | a 2px `outline-ring` at a 2px offset, independent of selection |

**Hover and press step AWAY FROM THE RAIL'S RUNG, not toward a fixed colour.** `sunken → base → accent` reads as deeper in both themes precisely because lightness runs in opposite directions between them while *distance from the surface underneath* does not — the same reasoning §4.5 gives for the shell panel, applied to a control.

**The current destination is no longer a filled row, and that reverses an earlier rule here deliberately.** `secondary` made selection the loudest mark in the rail, which left nothing louder for an agent that needs a human — two different questions competing for one cue. Selection is now the quietest thing that is still unambiguous, and the row's one loud mark stays with the agent's state.

### Overflowing sidebar text LOOPS; overflowing text everywhere else clips (ADD)

**Inside the sidebar, and only inside it, a NAME too wide for its viewport scrolls leftward forever as `full text • full text • …`** — one constant speed (45 px/s), wrapping by exactly one text-plus-separator period so the seam is invisible. No ellipsis, no ping-pong, no pause at either end, and never gated on hover: a value that only reveals itself to a pointer is a value a keyboard reader never gets. Text that fits does not move at all.

**A NAME, not a FIGURE.** Titles, branches and project names loop; counters, ages and any other quantity are laid out at their own width and never scroll. The distinction is what the reader is doing: a name is *read* and its tail can arrive a second later, while a figure is *compared against the figure in the row above it*, and a moving target cannot be compared. If a quantity does not fit, the answer is to give it the room — take it from the name beside it — never to animate it.

**The scope is a provider, never a prop.** `ScannedTextScope` wraps `AppSidebar`; `LoopingText` loops inside it and is an ordinary `truncate` everywhere else. That is what makes it safe to put inside shared atoms — `entity.tsx`'s labels use it, so the rail's group heading loops while the identical label on a fleet card is untouched. A `marquee` boolean threaded through each call site is the disagreement `entity.tsx` refuses an `icon` prop to prevent, wearing a different name.

Five constraints, all of them requirements rather than polish:

- **Only the inner track moves.** The viewport, the row, its icons and its click target are stationary — a click target that drifts under a pointer is a worse defect than a clipped word.
- **One accessible name.** Every duplicate copy and every separator is `aria-hidden`; a screen reader hears the branch once. The clone is added only after measurement, so the server render and the first client render are identical and there is no hydration mismatch.
- **It stops for the reader.** `prefers-reduced-motion` renders the static full text with the value in `title`; a shared, discoverable pause control (in the rail's own control row, beside Filters) halts every loop at once and RESUMES where it stopped rather than restarting.
- **It stops when nobody is reading it.** Suspended while the row is out of view and while the document is hidden — **not** on window blur, which is an unfocused window someone is still reading.
- **It costs no timers.** One `ResizeObserver` and one `IntersectionObserver` are shared across every label in the app, and the motion itself is a WAAPI `transform` animation the compositor owns. A rail of twenty rows is not twenty intervals.

**A screenshot cannot verify any of this.** Three stills of a translated track look exactly like three stills of a static one, so the evidence is `tests/e2e/sidebar-sessions.spec.ts` reading `getAnimations()` and sampling the transform at 0, half a period, one tick before the period and at the period — and the seam assertion was mutation-tested by breaking the period.

**Where the regions END is a decision too.** `SectionCard`'s body is `flex-1` and `CardGrid` stretches every card in a row to the tallest, so `mt-auto` on the last region pins it to the card's floor and lines the counters up ACROSS the row. Without it a card with a one-line task and a card with five tickets put their figures at two unrelated heights and the eye has to find each one — the same content, arranged so it cannot be compared.

**Overflow: a NAME truncates, a SENTENCE wraps** — outside the sidebar, where a name loops instead (see the marquee rule above). Every cell that can overflow is `min-w-0`, and **truncated text always exposes its full real value through its existing tooltip, not a duplicate `title`** — truncation without a way to recover the value is data loss. But truncation only works where the head of the value identifies it. Clipping a sentence deletes its predicate, which is usually the half the row exists for: truncating a pace row kept "143.2% of the window at this rate" and cut the forecast it was qualifying. **Solve overflow in the ROW, not per string** — one grid, the naming cell `min-w-0 truncate`, the qualifying cell `shrink-0` and free to take a second line.

---

## 9. Tables

Column geometry comes from `components/grove/table-columns.ts` and is never re-derived per page.

| Constant | For |
|---|---|
| `LABEL_COL` / `LABEL_CELL` | The one column carrying a name. **Exactly one per table** — two columns both claiming the remainder do not split it. |
| `CAPPED_COL` / `CAPPED_CELL` | A second long-value column: sizes to content, caps itself |
| `NUMERIC` | A numeric column: content-width, right-aligned |
| `FROZEN_TABLE_HEAD` | On the scroll owner. **Every scrolling table freezes its header** — not a nice-to-have. |

- Header cells: `text-xs font-medium text-content-tertiary`. Body cells: `text-sm text-content-primary`, dropping to `text-content-tertiary` for metadata columns.
- Numbers: `NUMERIC` plus `tabular-nums`, always.
- **A row that navigates is a link**, with hover feedback across the whole row.
- **A bounded list states its bound**: "Showing 100 of 2.5K". A silent cap reads as "this is everything".
- **An event history is a connected sequence, not a card per record (ADD).** Use one ordered feed with `text-xs text-content-tertiary` local date headings and tabular times. Event titles take `text-sm font-medium`, while notes remain complete wrapped `text-sm text-content-secondary` prose. A thin `--border` rail links marks from the existing phase vocabulary. Done and blocked marks use their existing semantic colors with explicit words. Exact local times pair with `PreciseAge` in the same metadata group. Desktop groups align beside the rail and narrow layouts place both values below the content. Resolved ticket references are native anchors with `underline decoration-dotted underline-offset-2`, opening in a new tab with an accessible hint. A missing URL stays unlinked. Search and filters stay above the scroll region, with the visible count and Show more below it. The rail expresses order, never proportional elapsed time.
- **State the bound you can PROVE, and ask for one you know.** `GET /sessions` slices to a `limit` and returns no grand total, so the session browser could not say "50 of 312" — nobody had told it 312 — and it said "50 sessions", full stop, on a host holding several hundred. The fix is two halves: the page **requests an explicit limit** so the cap becomes a number it owns, and the label states which *end* of the list it holds ("the newest 200 on this host") rather than inventing a denominator. A route that caps without reporting a total makes an honest count impossible on the client; **name your own bound and describe the horizon.**
- **A header row is metadata about its columns, not a peer of them.** The vendored `TableHead` ships `font-medium text-foreground` with no size, so an untouched header measures at tier one, same weight and colour as the data — chrome competing with content. Every header cell takes `text-xs text-content-tertiary` at the call site.

---

## 10. The states every surface must handle

A screen is finished when its **states** are, not when its data renders. All six, every time:

| State | Requirement |
|---|---|
| **Hover** | Anything clickable changes — background, underline, or both — plus `cursor-pointer` |
| **Focus** | Visible keyboard ring from the vendored `focus-visible:ring-ring/50`. Never `outline-none` without a replacement. Tab order follows reading order. |
| **Loading** | A skeleton the shape of the content, never a spinner over a blank pane, never a layout that jumps when data lands |
| **Empty** | Says what would be here and how to get one. Quiet treatment (below). |
| **Empty after filtering** | A **different** state from empty, and it must offer the way back out. Conflating them turns a filter into a trap. |
| **Degraded** | Partial data renders and **states its own coverage** — "1 of 2 plans", "Showing 100 of 2.5K" — rather than refusing to draw until it is whole. The coverage is stated **on the mark a reader compares against**, not only in prose beside it. The missing part says so, scoped to the field that is actually missing. |
| **Error** | What failed, and the one action that might fix it. `text-destructive` on the signal, neutral on the explanation. |

**Never say "up to date" when the check did not run.** A false negative and an unknown are different claims, and a user relies on the difference when deciding not to act.

**Loading is US fetching; WORKING is the agent thinking — and only the first one gets a skeleton.** The rule above bans a spinner because a skeleton can be the shape of content we already know is coming; an agent mid-run owes us no shape at all, and a skeleton there would draw a message that may never arrive. That is the case the vendored `GenerationLoader` answers, and it is the one place a moving mark is right: `variant="rounded"` (the app's marks are soft rectangles — a grid of circles reads as another app's spinner), the label `Working`, left-aligned with its siblings rather than centred, and `role="status"` so it is announced once and never interrupts. It is not chrome: it renders only while the agent actually works, so it also functions as the answer to "is anything happening", which no skeleton can give.

**A moving mark is sized against the type it sits beside, never against the root.** Shipped at 25.5px next to its own 12.8px label, it was the largest thing in the transcript footer and read as a block rather than a mark; at `calc(var(--text-sm) * 0.375)` per cell the 3x3 matrix lands at ~1.5 label-heights. The general rule is §1's, applied to a mark: **a fixed size is a decision about one surface, and this one appears on two** — a footer line and a 28px rail title band. Governed once at the theme boundary (`globals.css`), because the cells are vendored children with no prop reaching them and `lint:styling` forbids Grove code from restyling them; `webapp/CLAUDE.md` carries why `em` is the wrong mechanism for it.

**In the rail and on a fleet card the word is dropped and only the mark remains** — the band is the workspace's name, and a second word competing with it is the "figure is never the thing that gives way" trade run backwards. The name survives as `aria-label`, so nothing is lost to a screen reader. It goes LAST in the title band — after the fleet card's status badge, inside the corner the rail's options button already reserves — so a header reads in one order: identity, name, standing state, liveness. **A badge states what a thing IS; the mark states that something is happening now, so the mark always follows.**

**Degraded is the state most often shipped as nothing, because refusing looks like rigour.** The usage trend's plan ceiling demanded that every account contribute and so drew no line at all on a real host — one idle account with nothing to extrapolate from erased a perfectly good cap. A stated partial bound is the honest answer, and it is the same rule as a bounded list's "Showing 100 of 2.5K" (§9): **draw what you can prove and name the horizon.** An unqualified `cap` on the line would have been read as the whole cap however the caption was worded, which is why the qualifier goes on the mark.

---

## 11. Empty, degraded and null states are QUIETER

**An absence of data is never the loudest thing on a screen.** The sharpest live failure: on `/usage`, `not measured` renders `text-2xl font-semibold text-muted-foreground` — 20px/600, the same size and weight as `35.9B`, the largest real figure on the page. The biggest, boldest text on the screen is the absence of data.

The rule, in tokens:

- **A null value renders one tier down and one step down from the value it replaces.** A display figure's absence is `text-lg text-content-tertiary`, never `text-2xl font-semibold`.
- **It is never `font-semibold`.** Weight is for things that are there.
- **It is always `--content-tertiary`.** Never primary, never coloured — an absence is not an error.
- **It names what is missing, scoped to the field.** "not measured" on the one figure that is unmeasured, never across a card holding real data beside it. Unmeasured is `unknown`, never `0` — a fabricated zero misleads a user about their own spend.
- **An empty *surface* is ONE ADMONITION, and the same one everywhere.** Composed from the vendored `Alert`: an icon gutter, a `text-sm` title, one tertiary line, and a link where there is somewhere to go. Files, Changes and Info all take that shape, because three surfaces inventing three treatments is how one of them ends up with `EmptyStateGreeting` — a chat-thread component — rendering at `text-xl` in the middle of a work pane, and another ends up drawing two full cards to say nothing happened. An empty state is not the screen's purpose, so it gets neither the largest type on the pane nor a card wall.
- **Only a MEASURED nothing gets the admonition.** A `null` is not an empty set: it still renders its ordinary card with `—`, per the unmeasured rule above. Collapsing the two is how a page tells a user their working tree is clean when the daemon never answered.

---

## 12. The governing discipline

**An icon or a colour earns its place by adding recognition or encoding state. If it is doing neither, leave it off.**

The failure on one side is a screen with no entry point, where everything is the same size in the same near-white. The failure on the other side is an app that looks like a candy store. Both come from the same root cause: decisions made per screen instead of once.

Three questions before adding anything visual:

1. **Does this encode state a user could act on?** If yes, it is colour or a badge. If no, it is neutral text.
2. **Does this make the thing recognisable faster than reading it?** If yes, it is a glyph from section 7. If no, it is noise.
3. **Is there already a token, a component, or a table for this?** There almost always is. Reach for it.

### A cue has a LIFETIME, and picking it is not a style choice

Three lifetimes, and the wrong one is a lie rather than a blemish:

- **A LOOP says "this is happening now."** It must therefore stop when the thing stops — a loop that outlives its subject is the animation asserting something the data does not.
- **A ONE-SHOT says "this just changed."** It is the notification and never the information, so it must leave a correct static end state: a reader who arrives after the fade has lost the announcement and nothing else.
- **A STATIC mark is a magnitude**, and a magnitude is not an event. It does not get animated on arrival.

**Motion is spent only where something is actually moving.** A pulse beside the word `blocked` claims activity that is not happening, so a live cue gates on the agent working AND not blocked AND not finished — three conditions, because each one alone leaves a case where the mark lies.

**Every cue collapses to its END STATE under `prefers-reduced-motion`, not to its opening frame.** `animation: none` alone is the trap: it leaves a flash overlay painted at the loudest moment it was ever meant to have, permanently. Remove the overlay; keep the state underneath.

---

## Migration state — which system a surface is on

**The app is mid-adoption and a reader must not assume otherwise.** Three systems, at three different stages:

| System | State | What that means |
|---|---|---|
| **Type ramp** (§1) | **SHIPPED GLOBALLY.** Every surface is on it. | The `--text-*` values are live, so every screen is already a step smaller. Nothing to adopt per surface. |
| **Content tiers** (§2) | Tokens live, **adoption started.** | The rail and the tickets card are on them; roughly 80 `text-muted-foreground` call sites are not. Adoption is per surface, and it is a rename because tertiary aliases `--muted-foreground` — which is also why it does **not** wait on the colour flip. |
| **Surface ladder** (§4.3) | **SHIPPED GLOBALLY.** Every surface is on it. | `--background`, `--card`, `--popover`, `--input` and `--ring` now READ the ladder, so every surface sits on a named rung without a single component changing. Nothing to adopt per surface. |

**It landed as two commits in a required order, and the order was correctness rather than tidiness.** The retune first (`4a34415`), the flip second (`0379622`). Landing the datum first would have shipped a live AA regression — tertiary drops to 4.36:1 the moment `--background` stops being pure white, and to 3.59:1 in a well.

**Three things that surfaced during the flip and are worth keeping:**

1. **The flip silently flattens anything whose "elevation" was really just `bg-background` being pure white.** `shell-panel` was exactly that: measured, light-mode separation from the gutter collapsed from ΔY 0.0294 to **0.0018** — one flat plane — because the panel and the gutter had become the same layer. Repointed at `--surface-raised` it is 0.1001. **Before moving a datum, grep for what was leaning on its old value.**
2. **`shell-panel` keeps no border and no shadow, deviating from the `raised` tuple deliberately.** Those were measured off the assistant-ui base demo and reviewed; the tuple's redundancy exists for rungs whose tint cannot carry the boundary alone, and at ΔY 0.1001 this one can.
3. **The dark panel now reads LIGHTER than the gutter, where it used to read darker.** The old reversal was a property of the ad-hoc implementation, not a decision — the ladder's premise is that `raised` sits above `base` in both themes. Recorded because §4.5 still (correctly) describes the *general* tint-step reversal, and this one specific instance is now gone.

**Naming note before you grep:** the `surface-raised` **utility** (shadow only) and the `--surface-raised` **token** (fill) are still two different things sharing a name.

---

## What the base-size change costs

The `--text-*` redefinition in `globals.css` is **global by construction** — that is the point of it, since the vendored layer only speaks `text-sm` and `text-xs` and no per-surface change can reach it. So it is **PR 0**, landed alone, changing size and nothing else. Everything after it — tier assignment, mono, badges, buttons, icons, states — is per-surface and lands one PR at a time.

Surfaces PR 0 touches, all of them, and what to re-check on each:

| Surface | What moves | Re-check |
|---|---|---|
| Rail (`shell/app-sidebar.tsx`) | Row title/body hierarchy follows the ramp | Verify compact intrinsic rows at the shared `RAIL_WIDTH` measure. |
| Shell header | Compact page title `text-sm`; pane labels `text-xs` | Header is `h-8` with native 24px controls; title truncates before actions wrap. Shell plus compact work-tab row stays within 64px. |
| Fleet cards | Titles 14→13px, subtitles 12px, badges 12px | Two-line card height; badge row wrapping |
| Work panel tabs | Tab labels 14→13px | Tab strip height and hit targets |
| Workspace transcript | **Nothing** — its prose is the 16px root default, not `text-base` | Adopting `text-base` is its own PR; it drops prose to 14px and inline code to 11.9px |
| Sessions table | Cells 14→13px, headers 12px | Column widths were tuned at 14px; `LABEL_COL`/`CAPPED_CELL` re-measure |
| Session detail | Same as transcript | — |
| Usage stat tiles | Figures `text-2xl` 24→20px | Tile height; the `not measured` case gets *worse-looking* until its own PR |
| Usage tables | Cells 14→13px | Same column re-measure |
| Login | Pairing code `text-3xl` 30→24px | It is the only thing on the screen; confirm it still reads as the subject |
| Account menu | Items 14→13px | Menu item hit targets stay ≥32px |

**The rail follows its own compact hierarchy.** Its title is `text-xl`, its body is `text-lg`, and the brand mark is `size-7`; the existing ramp, rather than inline pixel floors, carries their relationship.

---

# The audit map

Each surface against this system, with the specific deltas it needs. **One PR per row**, cut from the Delta column.

**A row is struck from this map the moment it lands.** A stale audit map is worse than no map: it sends the next reader to fix something already fixed, and it has done exactly that twice. So this section only ever lists what is still OPEN — if a surface is not here, it is done, and the way to check is to read the code, never to trust this table.

**Two shared PRs land before any row**, because both are global by construction: the type ramp (already shipped) and the colour flip in §"Migration state" — the tertiary retune, then the four-token flip onto the ladder.

**Read that ordering precisely, because it is narrower than it looks.** The tertiary retune gates **rung** adoption, not **tier** adoption. Tiers are safe to adopt today for exactly the reason §2 gives — tertiary *aliases* `--muted-foreground`, so a rename changes nothing until the datum moves. Measured 2026-08-11: light `--content-tertiary` is still `var(--muted-foreground)` and `--background`/`--card`/`--popover` are all still `oklch(1 0 0)`, so neither shared PR has landed; and every one of the new-token call sites in the tree is `text-content-*` with zero `bg-surface-*`, so nothing has jumped the order. **A row below may take its type, tier, mono, icon and state deltas now. Only the rungs wait.**

**Which rung each surface takes**, decided once here so no surface picks its own:

| Surface | Rung |
|---|---|
| Shell gutter, rail | `sunken` — one plane, which is why neither carries a border |
| Shell panel (the page) | `base` (the datum) |
| Cards, fleet workspace cards, stat tiles | `base` body; §8 header/enclosures supply internal hierarchy |
| Terminal, diff wells, code blocks, transcript file diffs, bounded inset lists | **`sunken`** — the direction the app previously had no vocabulary for |
| Dialogs, popovers, menus, command palette, the account menu | `overlay` |
| Plan card (floats over the transcript) | `overlay`, not `raised` — it is over content, not in flow |

### Workspace transcript — `components/grove/workspace/thread.tsx` (ported)

| Delta | Detail |
|---|---|
| Adopt the ramp | Prose is the **16px root default** — an undesigned size. Set the reading column to `text-base` (14px). Accept inline code at 11.9px. |
| Size sprawl | 16 distinct size/weight pairs, the worst in the app. Audit each: 13.6/15.3px are `text-[0.85em]` (vendored, accepted); 9px/700 is `react-file-icon` SVG artwork (not type, exclude); 11px is `elements`' `mono` (vendored, accepted) |
| Content tiers | 2607 dark-mode elements share one near-white. Message body → secondary; metadata rows → tertiary; only the turn's subject stays primary |
| Mono | Correct here — code spans, paths and commands are literals. No change. |
| States | Loading skeleton must match turn shape; error state names the turn that failed |

### Work panel — five tabs, five PRs

They share `card.tsx` and `assistant-ui/badge`, so the two shared deltas land first as one PR, then each tab takes its own.

**The badge migration is done except `files-tab.tsx`,** the last importer. **The census is `grep -rn "^import.*assistant-ui/badge" components/grove/`, never a list written here** — this row was written as six named files and was wrong in both directions within a day: `ticket-refs` had already migrated off it, `selectors.ts` had been importing it the whole time and was never named, and `phase-meter` matched only a *comment*. `grep -rl` counts comments, so it cannot establish the import census.

**The shared `card.tsx` deltas are done, and the lesson is where the defect was, not what it was.** The work panel's "mono metric" leak was one line in `CardStat`, not five per-tab bugs — no tab styles its own figures. **When the same defect appears on every surface of a family, look for the primitive before you write a row per surface**; the map cost five rows and a sixth on Usage describing a single line.

The `mono` flag now means IDENTIFIER and nothing else. What made it two jobs was the weld: `font-mono tabular-nums` as one unit, so a caller could not ask for tabular figures without also claiming the value was a literal. **They are separate claims** — mono says what a value IS, tabular figures say how digits should line up — and the tell is that `tabular-nums` is redundant under a monospace face, whose digits are fixed-width already. Tabular figures moved onto every row; mono stayed a flag. Every call site that had been passing it was a timestamp.

#### Work · Terminal — `terminal-tab.tsx`

| Delta | Detail |
|---|---|
| **Themed palette, not a raw one** | 98 of 103 elements are mono carrying ANSI colour. The *meaning* is the agent's and is never second-guessed; the 16 **values** are ours, via `--ansi-*`. See the palette rule in §4.6. |
| **Rung** | `sunken` when inset, `base` in the Work tab. A terminal is the clearest case for the downward direction — content the surface contains rather than presents. The palette is solved against the worst of those, not against one. |
| Type | The one Grove-owned string is the stream label at `font-mono text-xs uppercase` — the only sanctioned `uppercase` in the app, because it is a literal channel name |
| States | Disconnected and empty-buffer states get the quiet treatment; a blank terminal must not look like a failed render |

#### Work · Changes — done, and one row was UNREACHABLE

Its own strings are on the tiers. **"Commit subject primary, SHA and author tertiary" was struck as unreachable, not done:** the commit list is the vendored `elements/timeline`, and subject, SHA and date are styled inside it. `commitEvents` hands it `title` / `time` / `detail` as plain strings, so the call site has no seam to tier them through.

**Divergence is on `CardCell` (§8), and the reason is meaning rather than density.** Its five counters answer three different questions — uncommitted paths right now, lines on the branch since its diff base, commits against the base branch — and as five bare figures in one flowing row they read as one measurement taken five ways. The units are in the labels (`Dirty files`, `Lines added`, `Commits ahead`) because nothing else on the card says them, and the `title` sentences are the fleet card's own, verbatim, so the two surfaces teach one vocabulary. Signs ride the figure, so `+11.2K` in green has both a sign and a word before the hue is asked to carry anything, and a real zero stays untoned: `+0` in green claims a change that did not happen.

**That is the correct outcome rather than a gap to close.** Restyling a vendored component is the one move this app forbids, and there is no prop for it — so the honest options are to accept upstream's ranking or raise it upstream, never to reach in. **Before writing a tier row for a surface, check whether the surface actually owns the text**; three of the deltas on this page turned out to belong to `card.tsx` or to `elements/`, not to the tab.

#### Work · Files — `file-row.tsx`, `files-tab.tsx`

| Delta | Detail |
|---|---|
| Mono | `file-row.tsx`'s `+1 −0` and `files-tab.tsx`'s edit totals are mono at the CALL SITE, not through `CardStat` → sans + `tabular-nums`. Paths and filenames stay mono. |
| Exempt | `9px/700` inside `react-file-icon` is SVG artwork, not type. Leave it. |
| Content tiers | Filename primary, directory prefix tertiary, stats tertiary |

#### Work · Info — done

Badges and mono are done, and what is left mono here (`main`, the base ref) is correctly mono. The Timeline and Identity cards' figures come through `CardField`; **Activity's come through `CardCell`**, because a figure that is one of six peers wants a perimeter and a figure in a term-and-value list does not.

**The tab was rebuilt on card → region → cell (§8), and the three defects it removed are worth naming because each looked like a rendering choice and was a MEANING problem.** A tall phase ladder that was the ordinary reading rather than the narrow fallback (§6). A `Ticket reports` list on the Task card restating rows that were already a card below — one ticket split across two cards, and the note only in the copy nobody scrolled to. And a rollup labelled *completion* while computing *average phase progress*, with six always-present per-phase population chips beside it that were mostly zeroes.

**The rollup is the one to remember: 80% and `0 / 2 done` are BOTH correct.** Two tickets at `delivering` are at index 4 of six phases, so the mean of `index / (total − 1)` is 0.8, while neither is finished. Replacing that 80% with 0% to make the two numbers agree does not fix a contradiction — it discards every claim the agents actually reported. The bar is named `Average ticket phase progress`, wears §3's dashed provenance with the formula in its `title`, and states its own coverage underneath (`0 / 2 done · 2 claims reported`, or `· 1 claim not reported` where a ref carries no claim at all).

**The tier row was already satisfied by the primitive, which is worth knowing before you go looking for it.** §8 asks for tertiary field labels and primary values; `CardField`'s `dt` carries the tertiary and its `dd` **inherits** primary from the card body, which sets `text-sm` and no colour. So the values are primary because nothing overrides them, not because a class says so — correct, and invisible to a grep for `text-content-primary`. **Absence of a tier class is not absence of a tier.**

**The Identity and Branch cards went to four badges and two, and the round trip is the lesson.** One pass deleted three of Identity's four chips on the reasoning that runtime, placement and the agent's name are FIXED for the life of a workspace and §6 reserves badge chrome for what changes; the next pass put them back, because the tertiary metadata line that replaced them had no visible structure and read as jumbled prose. Both passes were right about badges as *signals* and only the second was right about the card as a *layout* — see §6, which now states the boundary job the tone table had always implied. Constants take `outline`, only the lifecycle status carries a tone, and Branch matches it with the glyph carrying the rank (`GitBranchIcon` for the branch that moves, `GitForkIcon` for the settled origin). **When a row of chips ranks nothing, the answer is a rank, not a demotion out of the row.**

The no-base case stayed a SENTENCE rather than becoming a chip: there is nothing to mark, so an edge drawn around an absence would claim one.

Two specifics from these passes worth keeping:

- **The runtime fallback is the one chip in that row that would still be a badge under the strictest reading.** A fallback is not a property — it records that the isolation contract the workspace *asked for* was not honoured, which is a state, and one only a respawn clears. It keeps `outline` anyway, because the row already has its toned mark.
- **The agent name is rendered exactly as configured, with no capitalisation applied.** Measured, it reads `Claude Code (via …)` — already prose, and the user's own words. Title-casing somebody's proper nouns is the same mistake as stripping characters out of a ticket title. `capitalize` is for the lower-case wire enums (`status`, `runtime`) and nothing else.

#### Work · Controls — done

The full Controls surface has six cards: Session, Send keys, Sharing, Commands, Skills, MCP servers. Session composes Attach, Lifecycle and Runtime as fields on a 72px term column. Sharing keeps workspace publication and project policy together, separated by a hairline at the two-column layout; narrow containers stack rather than clip. The MCP-server names remain a plain wrapped list at tertiary, mono because a server name is an identifier. Model choice belongs in the workspace composer toolbar, not a seventh Controls card; the reported model is a fact, never a guessed catalog selection.

**Send keys: THE BUTTON IS THE CONTROL AND THE KEYCAP IS A LABEL, and the old shape had it the other way round.** Each key shipped as an `xs` `ghost` button — a 24px transparent hit area with a muted cap floating on it — so the only visible edge on the control belonged to a label, and nothing said where the target began. Nothing was functionally wrong, which is why it survived review; it is §1's "if it looks like the default, it wasn't a decision" applied to a hit area. They are `outline` buttons at 36px now, which is the vendored variant's own border, hover, pressed and focus ring plus a target you can hit with a thumb.

- **A chord is ONE button and ONE tab stop.** `Ctrl + C` is a simultaneous key; two caps inside it are decoration behind the button's worded name (`Send Ctrl+C`), and the `+` is a plain separator in the quiet tier rather than a third cap — a separator drawn like a key reads as a key to press. Rendering the chord as two controls would be two *sequential* sends, which is a different key.
- **The keycap INVERTS with the theme, through a token pair rather than a `dark:` fork.** `bg-foreground text-background` is a dark cap with light lettering in the light theme and a light cap with dark lettering in the dark one, because those two swap by construction — so the inversion cannot disagree with itself and the lettering can never fall below the contrast the theme's own body text has. It is scoped to this card's call site: the vendored `Kbd` is untouched, so a shortcut mentioned in a tooltip or a command menu stays the quiet muted chip it should be there. **Inverting globally would make every passing mention of a shortcut shout.**
- **Words where a glyph would only hint.** `↵`, `⇥` and `⎋` are conventional and also the marks that render as tofu on a font that lacks them, at the size where tofu and a box glyph are indistinguishable. Enter, Tab and Esc are spelled; the four arrows keep their symbols because every UI font has them and because the cluster's SHAPE — an inverted T, three buttons wide, so it fits the narrowest panel this card can be — identifies them before the glyph does. Type is the app's ramp: 14px (`text-base`) cap labels, 16px (`text-lg`) arrows.
- **`Cancel turn` is not a ninth cap.** It reaches the provider's own cancellation channel and is neither Ctrl+C nor Escape under another name. A different variant on a different row is the cheapest way to say so; drawing it in the key row would teach the opposite.

**One delta here was struck as WRONG rather than done: "card descriptions secondary".** A `SectionCard`'s description is set inside `card.tsx`, and §8 fixes it at `text-xs text-content-tertiary` — so the row was asking a surface to override a shared anatomy from the outside, which it cannot do and should not want to. §8 wins; a description is metadata about the card, not a claim competing with its title. If that is ever wrong it is wrong for every card at once, and it changes in §8.

### Fleet create dialog — `components/grove/fleet/create-workspace-dialog.tsx`

**The clearest case of the undesigned default in the app.**

| Delta | Detail |
|---|---|
| Type | **31 of 53 elements sit at the 16px/400 browser root**, with no `text-*` utility anywhere. Give the dialog body `text-sm` and its title `text-lg`. |
| Content tiers | **51 of 53 elements are one tier.** Field labels tertiary, help text secondary, values primary. |
| Buttons | One primary (Create), one secondary (Cancel). Verify no third competes. |
| Icons | **Done** — its model rows carry per-value brand marks through `ModelMark`/`AppIcon`, the same way the landing and workspace pickers do. |
| States | Submitting, validation error, and the project-list loading state |

### Session detail — `app/(shell)/sessions/[id]/page.tsx`

Shares the transcript renderer, so it inherits that row — and it is the app's second-largest surface: **4107 text elements, 12 distinct size/weight pairs, 2916 of them in one colour.**

| Delta | Detail |
|---|---|
| Content tiers | 71% of the surface is one tier. Message body secondary, metadata tertiary, turn subject primary. |
| Type | 1073 elements at the 16px root default — same undesigned prose size as the transcript, fixed by the same `text-base` adoption |
| Read-only | **Verified: no composer mounts.** Read-only is a capability, not a hidden control — keep it that way, and make sure the surface says *why* rather than looking broken |

### Usage — `app/(shell)/usage/page.tsx`, `components/grove/usage/**`

Type, tiers, the null state, the colour and the badge tables are done. **Two findings from that pass are worth keeping, because both generalise past this page:**

- **An absence has TWO routes to shouting, and only one is a literal.** The `not measured` in the cost card was written out at `text-2xl font-semibold`. The other route was `AbbreviatedNumber`, which renders a null *inside whatever the caller styled* — and its callers run from a display figure down to an inline table cell, so the same component was quiet in one place and 20px/600 in another. **Fix weight and tier in the shared component** (an absence is never bold and never primary at any size) **and leave SIZE to the call site**, which is the only thing that knows what size the missing value would have been.
- **Check whether the null state is reachable before building one.** Every field in the headline tile row is a plain `number` on the wire, so an unmeasured tile cannot happen; the step-down branch written for it was dead code. It became a narrowed prop type instead — the type is the guard, and it is a better one than a runtime branch. The genuinely nullable figures are token totals, a quota's `used`/`limit`, and cost.

| Delta | Detail |
|---|---|
| Mono | Session ids in `usage/sessions.tsx` stay mono. The `CardStat` half is the work panel's shared row above — one fix, two surfaces, so do not do it twice. |

---

## Adopting this on a new screen

1. Lay it out with `CardGrid` + `SectionCard`/`CardShell`. Do not invent a container.
2. Give every string a tier from section 2 and a step from section 1. If two things are primary, one is wrong.
3. Decide mono per section 3 by asking whether retyping it matters.
4. Add colour only where section 4 gives you a token. Most screens need none.
5. One primary button. Badges from a state table.
6. Glyphs from the table in section 7. If your entity is not in it, add a row here first.
7. Build all six states from section 10 before calling it done. The empty and degraded states are quiet.
8. Run `npm run gate`.

---

**ADD — Launch composer input: `min-h-24 max-h-64`.** The vendored composer is sized for a reply in a running conversation, where the message above carries context; Launch's composer is the whole screen and holds a task brief, so it needs the taller range.

**RECORD — Both composers share Assistant UI anatomy.** `ComposerBar` contains the vendored `File` element's cards (`size="sm"`) above the editor and a `ComposerToolbar` below it; a sent message draws the identical cards above its bubble, outside the clamp, so a file is one object — and one width — on every surface it crosses.

**ADD — Attachment cards use the container role.** `.attachment-card` supplies `--radius-lg`, a thin outline and a subtle vertical gradient derived from semantic tokens. Both dark stops must be lighter than the composer's actual `dark:bg-popover` fill, which resolves to overlay rather than raised. Light cards fade from raised toward the edge token. Filename uses `text-base` above a `text-sm` metadata region. That region always names kind and size, or `Size not recorded`. Upload status preserves known size. Metadata may wrap in narrow cards rather than clip. Attach stays left and model, expand and send stay right. Launch uses the vendored shadcn `Textarea` without a runtime. Sessions retain `ComposerPrimitive.Input` and its runtime attachment behavior. The theme boundary integrates both editors with the same surface and focus treatment. Editor focus uses a single 1px inset `--ring` outline at offset -1px, not a second outer frame around the whole composer; toolbar controls retain their own focus boundaries.

**ADD — Launch configuration occupies an inset shelf below the writing surface.** Compose `ComposerToolbar` with `composer-shelf` for project, agent, branch and runtime. Keep the controls discoverable through labelled triggers and expose full values in native pickers. Working directory and advanced settings may use progressive disclosure. Strategic placement separates writing from configuration without hiding useful choices. The session composer does not acquire redundant metadata or controls that cannot change its workspace.

**ADD — The welcome mark has a static decorative grid.** `launch-brand-field` uses the existing neutral tokens and fades at its edges. Keep it `aria-hidden` and outside pointer interaction. The existing Grove mark remains unchanged. The welcome heading uses `text-2xl` and its supporting sentence uses `text-base`, with the normal content tiers and no new font or typography scale.

**§7 — Launch takes `SproutIcon`.** Not a rocket. A rocket is the stock glyph for anything named "launch" and would be exactly the undesigned default §0 warns about; Grove's vocabulary is a forest, Fleet already holds `TreesIcon`, and starting a workspace is planting one. The mark currently renders nowhere — `RAIL_ITEMS` excludes `/` because the brand mark links there — but `NavItem.icon` is required, and a required field still takes a decided value rather than the first plausible one.
