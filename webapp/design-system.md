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
- **The terminal is exempt from the colour palette entirely.** ANSI colour arrives in the data and `fancy-ansi` injects it as inline styles. That is content, and content is not chrome.

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

**The functional damage.** ~~In the 260px rail, the workspace title `Frontend UI migration` needs **138px** and has **123px**. It truncates because the text outgrew its container, not because the name is long.~~

**RESOLVED — and the resolution was the container, not the type.** The rail is now `w-98` (392px), widened in two steps — 260 → 328 (+25%) → 392 (+20%) — precisely because the measurement above says the slot was the problem. The first step cleared the clip; the second bought the gutters and the rhythm below, which the first had left no room for. The title consequently stays `text-sm`: shrinking it was the workaround for a width that no longer binds, and pinning it to `text-xs` alongside the metadata beneath it left no size gap for hierarchy to live in. Any argument below that reasons from "the 260px rail" is reasoning about a rail that no longer ships.

**The rail's own spacing, which is §8's card vocabulary and not a second scale.** Its gutter is `p-3` — the same 12px §8 gives a card body, adopted rather than invented because the rail's footer already used it and was the one band nobody reported as cramped. **`px-2` while collapsed is not that gutter and must never be widened to match it**: 8px + a 32px icon button + 8px *is* the 48px icon rail, so a roomier value there does not add air, it pushes the icons off centre. Between rows the rail runs one step, `gap-1.5`, from the brand to the footer.

**A GROUP BREAK IS THE RHYTHM DOUBLED, and it is the only structure a surface with no rules on it can have.** The rail draws no separators, so spacing carries the entire hierarchy: rows that are siblings sit one step apart, a row that is a *different kind of thing* sits two, and a pair that is one object (a workspace row's title and entity lines) sits tighter than either. This is why an even rhythm is a prerequisite rather than a polish — a break is only legible as a multiple of something regular, and at a uniform 2px the daemon's version and uptime read as a fifth footer destination instead of as a description of what the four above are served by. The same rule is what separates the work panel's tab strip from `ShellHeader`, which likewise carries no border: the clearance has to beat the spacing *inside* either row or it reads as more of the same row.

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
| `text-xl` | **18px** | 20px | 24px | The page title in `ShellHeader` |
| `text-2xl` | **20px** | 24px | 26px | The one display figure a surface leads with |
| `text-3xl` | **24px** | 30px | 30px | The login pairing code — its only consumer |

**The base came down from 16px to 14px, and the working body size from 14px to 13px.**

Three rules govern the ramp:

- **12px is the floor.** Nothing in Grove code goes below it. WCAG sets no minimum font size, so the constraints we can actually hold are the ones met here: every step is `rem`, so browser zoom and user font settings still scale it, and every step clears 4.5:1 against all three content tiers.
- **The root stays 16px.** Do not shrink `html { font-size }`. Tailwind v4 derives spacing from `rem` as well, so that would shrink every gutter, control height and hit target along with the type — a zoom-out, not a densification. **Type moves; geometry does not.**
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
| `--content-primary` | `text-content-primary` | 19.90:1 | 19.06:1 | What the surface is *about*. The name, the title, the value. **Read first.** |
| `--content-secondary` | `text-content-secondary` | 9.21:1 | 12.34:1 | Supporting text that qualifies the primary — a description, a subtitle, a body sentence. |
| `--content-tertiary` | `text-content-tertiary` | 4.83:1 | 7.59:1 | Metadata. Timestamps, counts, labels, units, field names. **Present, not read.** |

Contrast is measured against `--background` in each theme. All three clear AA (4.5:1) for normal text at every step of the ramp.

- `--content-primary` aliases `--foreground`; `--content-tertiary` aliases `--muted-foreground`. **Every `text-muted-foreground` in the tree today is already correct as tertiary** — adopting the ramp is a rename, never a re-pick. `--content-secondary` is the genuinely new value, and it is what supporting text should have been using all along instead of borrowing one of its neighbours.
- **One primary per block.** A card whose title, value and description are all primary has ranked nothing. If two things want primary, one of them is secondary.
- **Never use opacity for hierarchy.** `text-foreground/50` is a fourth ramp; the vendored `elements/` layer uses one (measured on the transcript: 331 elements at `/0.5`, 57 at `/0.9`, 29 at `/0.4`, 10 at `/0.55`) and Grove code must not imitate it. An alpha neutral changes meaning against every background it lands on; a token does not.
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

### Numbers

Numeric formatting is a rule, not a per-screen choice.

- **Every number gets `tabular-nums`**, in mono or sans. A column of figures whose digits do not align is a column you cannot compare down.
- **Numbers right-align in tables**, with the header right-aligned too. The column class is `NUMERIC` from `components/grove/table-columns.ts` — never a hand-written `text-right`.
- **Numbers left-align inline** — in a stat, a badge or a sentence.
- **Abbreviate above 4 digits**, one decimal, SI-style: `12.6K`, `204.6M`, `35.9B`. Below 10,000, print the number.
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
- **No amber.** There is no `--warning` token. No surface has a rule for a third state that is neither failure nor success, and a hue with no state table behind it is an identity colour pretending. If one is ever genuinely needed it arrives as a token *with its gate written down*.
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
| **raised** | `--surface-raised` | 1.000 | 0.225 | Cards, the shell panel, anything sitting *on* the page |
| **overlay** | `--surface-overlay` | 1.000 | 0.285 | Popovers, dialogs, menus, the command palette — floating over everything |

**A rung is a TUPLE, never a colour.** Ask for a level and compose all four parts; lightness alone is never the carrier:

| Rung | Fill | Edge | Elevation | Radius |
|---|---|---|---|---|
| sunken | `bg-surface-sunken` | `border-surface-edge` | none (inset by tint) | `rounded-md` |
| base | `bg-surface-base` | none | none | none |
| raised | `bg-surface-raised` | `border-surface-edge` | shadow in **light only** | `rounded-lg` |
| overlay | `bg-surface-overlay` | `border-surface-edge` | shadow in both | `rounded-lg` |

The redundancy is **by specification**, exactly because the lightness delta is small by necessity in places. When the tint cannot do the work, the edge and the radius still say where the boundary is.

Interactive edges take `border-edge-control` instead of `border-surface-edge` — that is the 3:1 token from §4.6, and it applies to a control's own edge regardless of which rung the control is sitting on.

> **Gotcha worth knowing before you grep for missing CSS.** Tailwind v4 emits a utility only when it finds the class name in a scanned source, and **this document is one of them.** A class named here generates real CSS; a token that exists but is never written as a class generates nothing, which looks identical to a broken token. Verify a token by checking the emitted `--custom-property`, not by looking for its utility.

### 4.4 Perceptual separation, and the number to check

**Steps are defined in OKLCH, which is perceptually uniform, so a step means the same thing at both ends of the range.** The theme was already OKLCH, so this is a rule, not a migration.

**The floor: adjacent rungs differ by ≥ 0.05 OKLCH L.** Chosen as roughly 3–5× the just-noticeable difference under ideal viewing — the margin that is meant to survive a dim panel, glare and reduced brightness. A couple of points of HSL lightness is a real difference in a design tool and no difference at all on cheap hardware.

Measured deltas:

| Step | Light ΔL | Dark ΔL |
|---|---|---|
| sunken → base | 0.050 ✓ | 0.055 ✓ |
| base → raised | 0.050 ✓ | 0.070 ✓ |
| raised → overlay | **0.000 ✗** | 0.060 ✓ |

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
| Decorative container edges | no numeric floor | 1.4.11 does not reach them |

**That last row is a real distinction, not a loophole.** 1.4.11 scopes non-text contrast to what identifies a *component* or its *state*. Chasing 3:1 between adjacent container fills would produce a checkerboard, and no adjacent rung here reaches it (the best is 1.21:1) — which is correct and expected. **The 3:1 belongs to the edge, not the fill.**

Text on every rung, measured:

| Rung | primary | secondary | tertiary |
|---|---|---|---|
| light sunken | 14.75 | 6.85 | 4.56 ✓ |
| light base | 17.96 | 8.34 | 5.55 ✓ |
| light raised | 19.89 | 9.23 | 6.15 ✓ |
| dark sunken | 19.72 | 12.79 | 7.83 ✓ |
| dark base | 18.73 | 12.15 | 7.43 ✓ |
| dark raised | 16.39 | 10.63 | 6.51 ✓ |
| dark overlay | 13.76 | 8.93 | 5.46 ✓ |

**Adopting the ladder REQUIRED retuning the tertiary tier in light mode, and that shipped first, alone, for that reason.** At shadcn's `0.552` it passed on the pure-white page (4.83) and failed the moment the datum moved (4.36) or the text landed in a well (3.59). The retune is `oklch(0.495 0.016 286)`, giving 4.56 / 5.55 / 6.15 across sunken / base / raised, and the table above is post-retune.

**It was applied to `--muted-foreground`, not to `--content-tertiary`, because tertiary aliases it.** Retuning the tier token alone would have left the migrated call sites dark and the unmigrated ones at 4.83 for the length of the migration — and it would have falsified §2's "adoption is a rename, never a re-pick", which is the property that lets tier deltas land ahead of the ladder at all.

**Control edges: fixed.** `--input` and `--ring` now read `--edge-control`, which took light from **1.15:1** / **2.37:1** to 3.04+ against the datum. Dark's `--input` was the worse offender and the audit never saw it: it was `oklch(1 0 0 / 15%)`, an **alpha** edge compositing to **1.49–1.62:1**, and a contrast table that lists colours cannot measure something that has none. Assume an alpha value is unmeasured until you composite it.

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

The ladder was authored thinking upward — the whole vocabulary was built to add a direction the app did not have — and the derivation inherited that bias silently. The corrected value is `oklch(0.594 0.008 286)`: 3.00 / 3.66 / 4.05 across sunken / base / raised. One token still covers the ladder; it just has to be solved from the **extreme**, not the datum.

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
- **The rail row's trailing mark PASSES the same way.** It used to render `text-muted-foreground` regardless of what the agent was doing, so a fleet of twenty answered "who needs me" only by shape, at 12px. `activityHue` (`components/grove/fleet/fleet-tree.tsx`) now colours it too — `text-warning` while `agentAccent` says the row is mid-flight, `text-destructive` while `agentTone` says it needs a human, neutral otherwise — but it reads those two tables rather than inventing a third. **The two hues are not equally redundant, and saying they were is how this bullet was first written.** `!` renders exactly when `needs_attention`, which the daemon derives as `state ∈ {waiting, blocked, error}` (`ATTENTION_STATES`) — the same set `agentTone` sends to `destructive` — so the red genuinely restates its glyph and survives greyscale. The amber does not: a row that is not asking for attention shows its *phase* mark, which reports how far the task got and says nothing about whether the agent is running, so `working` and `idle` at the same phase are one shape. That is accepted rather than excused, and the reason is that the surface previously said nothing at all here — both rows already rendered the same muted glyph, so the hue **adds** a distinction to a colourblind reader's tie rather than replacing one they could see. Do not cite this bullet as precedent for a hue that REPLACES a shape. **This is §4.6's *state indicator* row (3:1, WCAG 1.4.11), not the body-text row `text-success` failed at 3.99:1** — the glyph is `aria-hidden` and never read as prose, so it is measured against the 3:1 floor rather than 4.5:1: `text-warning`/`text-destructive` on the rail's `sunken` rung measure 3.53–4.28:1 in light and 6.81–8.11:1 in dark, both ✓. Colouring the same hue onto a *number* would still fail — the distinction is what kind of thing the coloured pixels are, not the token.
- **`StatusBadge` / `AgentStateBadge` PASS**, because their tone comes from a variant table and every one of them prints the state as a **word**. The colour is redundant by construction.
- **Bare `text-success` / `text-destructive` on a number FAILS** — a coloured figure with no word beside it is carrying its meaning in hue alone. This is the concrete defect the simulation finds, and `35.9B` in `text-success` on the usage page is an instance of it.

The rule that follows: **a state colour may only ever be the second carrier.** Put the word or glyph first, then colour it.

- **`text-success` also needs size to be legal alone.** Measured 3.99:1 in light: clears AA-large (3:1), fails AA-normal (4.5:1). At body size, carry meaning in the word and leave the text neutral.

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
| `scroll-edge-top` / `-bottom` are gradient scrims fading to `--background`, not shadows | The requirement is that content passing beneath the chrome **fades**; a shadow only draws a lip. | shipped |
| The `@` in `user@host` steps back furthest | It is **structure, not information** — punctuation joining two values the reader actually wants. Made on instinct; now a stated rule: *structural punctuation between two values takes the tertiary tier, below both values it separates.* | shipped; **see the note below** |

**How that rule lands is worth keeping, because it is the general answer to "I need a level the tokens do not have".** It shipped as `text-foreground` → `text-muted-foreground/60` → `text-muted-foreground`: the ranking was right and the *expression* was an alpha step invented below the floor, because two neutrals cannot say three things. The three tiers say it exactly — user primary, host **secondary**, `@` tertiary — so adopting §2 deleted the opacity rather than merely renaming around it. **An opacity step in Grove code is almost always a missing tier, not a missing colour**, and it matters more than it looks here: this one string renders on two different rungs (the rail's `base` and the menu's `overlay`), and an alpha neutral means something different on each while a token does not.
| Scrollbars are styled once, globally, unscoped | Every surface creates a scroller; a per-surface opt-in leaves whichever one nobody remembered wearing the browser default. | shipped |
| `--content-*` and the type ramp | See §1 and §2. **They shipped GLOBALLY ahead of per-surface adoption** — see the migration state below. | shipped |
| Terminal ANSI colour is exempt | It arrives in the data and `fancy-ansi` injects it. Content is not chrome. | shipped |
| `PhaseBadge`/`TicketRollupMeter` never spend a TONE on phase position, only on `blocked` | Their position is the grayscale-safe shape-and-word ramp (`○ ◔ ◑ ◕ ● ✓`), so `destructive` can mean blocked without contradicting a green or amber phase. `PhaseMeter` is deliberately different: its six connected checkpoints are one composite sequence, and its `secondary`/`default`/`outline` table ranks reached/current/ahead positions while the glyphs still carry them without hue. In either shape, `blocked` is a flag across the phase axis, never a seventh position. | shipped |

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

- Sizes: `size="sm"` (h-8) for surface chrome, `size="default"` (h-9) for a page's primary, `size="icon-sm"`/`size="icon"` for icon-only. Nothing smaller than `sm` for anything a pointer must hit.
- **An icon-only button always has an `aria-label` and a tooltip.** Use `TooltipIconButton` from `components/assistant-ui/`; it does both.
- **Anything clickable looks clickable** — a hover state that changes background or underline, plus the pointer cursor. **The cursor is handled globally and you must not add `cursor-pointer` at a call site** (see [the one OVERRIDE](#the-one-override)); `components/grove/**` carrying it is Grove code inventing a look. A row that navigates is a link, not a `div` with an `onClick`.

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
- Badges are `text-xs` (12px) `font-medium`, pill radius, from the component. Never restyled at a call site.
- **The four tones say how LOUD; an ACCENT says which of two hues the four cannot name.** `ui/badge` is vendored, so its variant table is not ours to extend, and the two states a fleet most needs to separate are exactly the two it omits: work that is UNDER WAY and work that is FINISHED. `destructive` covers failure and `default`/`secondary` cover loud and quiet, which leaves "in flight" and "done" sharing a grey — the reason a whole wall of cards read as one colour. So `fleet/tokens.ts` carries a second read of the same state tables (`statusAccent`, `agentAccent`, `progressAccent`) returning a **semantic-token className** (`bg-warning text-warning-foreground`, `bg-success text-success-foreground`) that rides `className` beside the `variant`. Three things make that composition rather than restyling, and all three are load-bearing: it is a token, not a palette step, so the theme still owns what it resolves to; it comes from a `Record` over the same union the variant does, so **"a variant is never chosen at a call site" holds for the accent too** — the rule is about the decision, not the prop; and the variant is kept, so the hairline survives and a toned chip still sits in the same set as the untoned ones beside it.
- **An accent is spent only where the object is MID-FLIGHT or FINISHED — never on identity, never on a resting state.** That gate is what stops a second colour language growing beside the tone table: `provisioning`, `starting` and `working` take amber; nothing on those two axes is ever "finished", because a workspace that finished is simply idle again. The one place `success` lands is a bounded count (below).

### Progress marks: an aggregate is a magnitude, a single claim is a position

**A plain accumulating bar (`Progress`) and a checkpoint track (chips joined by connectors) answer different questions, and picking between them is not a style choice.** A single claim — one ticket's phase, one workspace's phase — has a POSITION: which step is it on, and a track is the only shape that can say "this one, not that one" the way a fraction alone cannot. An aggregate over several claims — a workspace's whole batch of tickets — has no position to show, only a magnitude: how much of the batch is done. Reach for `Progress` there, and never for a single claim, where a bar that can only grow reads a legitimate backward report (`verifying` back to `planning`) as breakage.

**That same split decides which of the two figures on a fleet card gets a HUE, and it is the reason one of them stays grey while the owner was asking for colour everywhere.** The todo count is an aggregate — `2/5` of a bounded batch — so it is a magnitude, and `progressAccent` gives it amber in flight and green complete. The phase badge beside it is a single claim, so it reports a position and spends no tone on how far along it is, exactly as §4.8 already recorded for `PhaseMeter`: position is shape (`○ ◔ ◑ ◕ ● ✓`) and the only state on that axis worth a tone is `blocked`. Colouring the ramp too would leave `destructive` contradicting a green `delivering`, and it would say the two figures answer the same question when they do not. **An unstarted count gets nothing at all** — `0/6` in amber claims work has begun when none has, and `progressAccent` returns `undefined` there rather than making the caller remember.

**An aggregate must still say what fraction of it is silence, not just what fraction is done.** `TicketRollupMeter` scores an unclaimed ticket as zero rather than excluding it — excluding would let a workspace holding five untouched tickets and one finished one read 100% complete. Zero can only ever understate, so the honest fix is stating the coverage beside the number (`N not reported`), the same `Showing 100 of 2.5K` discipline §10 already asks of every partial read.

**Where TWO parties make claims about one object, the copy keeps them apart by ATTRIBUTION, never by wording.** A ticket row shows the tracker's state beside Grove's phase, and `closed` next to `scoping` reads as a contradiction the reader has to resolve — until every sentence names its claimant: *"The agent is reading the ticket…"* against *"Gitea says this issue is closed."* Then it is two true statements, which is what it always was. Merging them into one sentence is the conflation the explanation exists to undo, so the shared `phaseTooltip` composer returns the claims **separately** and both the ticket row and fleet phase badge stack them; concatenating in either surface makes the halves untestable and invites a single voice to smooth over the disagreement. A state Grove cannot normalize quotes the tracker's own word rather than inventing a controlled one.

**A progress tooltip leads with the agent's per-ticket note, verbatim, then states the phase meaning, the blocked reason where present, and the tracker-attributed claim.** The note is the only firsthand task context and must not be paraphrased or buried under Grove's explanation; quotation and italics keep it visibly somebody else's words. The fixed sentences that follow give an unfamiliar reader the phase vocabulary, state why it cannot advance, and then make the independent tracker claim legible without pretending the two systems agree. A missing note simply omits that row — it does not invent a substitute.

**A mark that encodes a position in a vocabulary the reader was never taught needs the vocabulary, not a louder mark.** `◐ 3/6` says how far along without ever saying what "along" measures, which is why a phase badge reads as an arbitrary process. The definitions belong in the hover, written once — the same six sentences the agent's own skill defines, turned from second person into third — because two independent explanations of one word teach it twice and trust neither. `Badge` is a bare `<span>`, so an explanation carried only by `title` is mouse-only: the trigger takes `tabIndex`, and the `aria-label` carries every claim the tooltip shows.

---

## 7. Icons

**One set: `lucide-react`.** Exceptions are exactly two and both are artwork, not vocabulary: `components/icons/github` (a trademark), and the vendored brand marks behind `AgentMark`, sourced from `@lobehub/icons` (pinned exact) rather than traced by hand — brand artwork is trademarked, versioned and occasionally redrawn by its owner. `react-file-icon` renders file-type artwork, which is a data visualisation, not an icon.

**`AgentMark` imports each provider's `Color` leaf directly, never the compound icon and never the package root.** A brand mark's fixed hue is the concrete case of §4.1's **Identity** row (`--chart-1…5`, brand marks — "a fixed property of the thing"), so a vendored `fill` baked into imported path data is not a colour decision made in `components/grove/`; `lint:styling`'s ban is on a *class* fixing a colour in this tree, which a `<path fill="#…">` arriving from `node_modules` never is. The leaf import is load-bearing, not stylistic: the compound object's `.Avatar`/`.Combine` variants eagerly import `@lobehub/ui`'s `@emoji-mart` dependency, which fails to load under Vitest's Node ESM loader and would otherwise ride the bundle on tree-shaking alone — `@lobehub/icons/es/<Provider>/components/Color` touches only the one leaf (`react`, its own `../style`, and, where the artwork needs a gradient, `../../hooks/useFillId`), the same shallow profile `Mono` had. **Codex's brand colour is a blue gradient (`#3941FF`→`#7A9DFF`→`#B1A7FF`), not the near-white this section used to claim** — its `Color` leaf also draws its own opaque white rounded-square backing path behind the gradient mark, so the rendered glyph always carries its own contrast plate and reads on both the dark rail and the light theme regardless of what sits behind it. `openai` has no vendored `Color` leaf in the pinned version (`style.js` exposes per-product hex constants but no component), so it alone stays on `Mono`. `claude` renders lobehub's generic `Claude` mark, not the tool-specific `ClaudeCode` one — reversed deliberately: people recognise the Anthropic mark far more readily than a CLI-specific redraw of it, so recognisability now outweighs naming the exact binary. `codex` still keeps its own vendored mark rather than borrowing `OpenAI`'s, since Codex and OpenAI stay separate `AgentBrand`s (`fleet/tokens.ts`).

- **Always the `*Icon` spelling** — `GitBranchIcon`, not `GitBranch`. Both resolve, which is exactly why they drift and why no gate catches it. **The tree is fully swept; the census is the scan, never a number written here** — parse every `import { … } from "lucide-react"` under `components/grove/**` and `app/**` and check each name ends in `Icon`. It must read multi-line imports: three files declare theirs across several lines, and a single-line regex reports a clean sweep while never having looked at them.
- **One stroke weight and one optical size.** `size-4` beside `text-sm` body; `size-3` inside a badge or an `xs` button; **`size-[1em]` for any glyph inline with text**, which is what makes a mark read the same on a 13px card and a 12px rail row. Never a pixel size on an inline glyph.
- **Optically aligned:** an inline glyph gets `align-[-0.125em]` and `shrink-0`; the text beside it gets `truncate` on a `min-w-0` row. **The glyph must never be the thing that shrinks** — a clipped word is legible, a clipped icon is a smudge, and it is the part carrying the type.
- **Decorative icons are `aria-hidden`, and the meaning is restated in `sr-only` text.** A screen reader announcing "folder git 2 Grove" is worse than nothing.

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

### Fleet state — one word and mark per axis

Every workspace lifecycle state and agent state has one sentence-case label and one Lucide mark in `fleet/tokens.ts`; the rail, filters, cards and palette consume that total table rather than spelling or decorating a state locally. The ordinary-English labels need no hover explanation. Only `provisioning`, `offline`, `orphaned`, and the agent's `blocked` state take the glossary, because they carry a Grove-specific meaning a newcomer cannot reliably infer. `active` and wire-level `running` deliberately render as the same reader-facing state, **Active**, with the same radio mark: the distinction is an implementation detail, not a second condition to teach.

The lifecycle and agent tables share `LoaderCircleIcon` for starting work and `CircleXIcon` for error, because those are the same claim on distinct axes; all other marks are distinct **within their own axis**. Runtime belongs to the same presentation owner (`ServerIcon` / `BoxIcon`) but retains its fixed-property `outline` treatment, not a lifecycle or agent-state tone.

### Ticket state — the one entity whose glyph varies

A ticket is the only row in the table above whose mark depends on more than what it IS, because a forge's whole visual language is that an issue and a *closed* issue are different objects at a glance. The pairing below is the convention every developer already reads without being taught it, so Grove copies it rather than inventing one.

| State | Issue | Pull request | Colour |
|---|---|---|---|
| open | `CircleDotIcon` | `GitPullRequestIcon` | `--success` |
| merged | *n/a — see below* | `GitMergeIcon` | `--merged` |
| closed | `CircleCheckIcon` | `GitPullRequestClosedIcon` | `--destructive` |
| draft | `CircleDashedIcon` | `GitPullRequestDraftIcon` | `--content-tertiary` |
| unknown | `CircleDotIcon` | `GitPullRequestIcon` | `--content-tertiary` |

- **The shape is the first carrier and the colour is the second**, which is what makes the card survive greyscale — §4.7's rule, and the reason the glyph changes per state rather than only the hue.
- **`TicketState` is GROVE'S union, not a wire enum.** `TicketRef.status` is `string | null` on the wire — free provider text (`open`/`opened`/`reopened`, `closed` vs `done`) — so there is nothing to key a `Record` on until it is normalized. `ticketState()` does that once, and every table above is total over the result. **Where a wire field has no enum, the compile-time guarantee has to be manufactured; say which side of the seam you are on.**
- **An issue is never `merged`.** The cell is filled anyway, because a partial map is a map that stops catching new states — it takes the closed check rather than a git-merge glyph that would claim a branch was involved.
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
| `CardStat` / `CardFields` / `CardField` | The vocabulary inside a body |

**Header behaviour.** A `SectionCard` header names a **topic**; it is not for everything with a title. A header that names the **object** — identity plus state in a tinted bar — reads as a title bar for a thing, which is right for a fleet workspace card and wrong for a one-word stat tile. A stat tile takes `CardShell` and composes its own rows. Same principle, opposite answers; that contrast is the test.

**A tinted band and a plain header are TWO COMPONENTS FOR TWO JOBS, and unifying them is the trap.** They look like the same thing with a setting turned off, and they are not: the band says "this is a title bar for an object", and type-and-spacing alone says "this is the label of a figure". A stat tile that grew a band would claim an identity it does not have; a fleet card that lost one would stop reading as a thing. **So a plain header is not a `SectionCard` with the tint disabled — there is no such prop, deliberately.** If you find yourself wanting one, you are about to merge two decisions that were made separately and correctly.

**Anatomy, fixed:**
- Title `text-sm font-medium text-content-primary`, truncating on a `min-w-0` row.
- Description `text-xs text-content-tertiary`, one line.
- Body `text-sm text-content-secondary`.
- Field labels `text-xs text-content-tertiary`; field values `text-xs text-content-primary`.
- Spacing is three values and no others: `p-3` inside a body, `gap-2` between a body's rows, `gap-3` between cards.

**A card body is NAMED REGIONS in a fixed order, never one flow — and the reason is wrapping, not tidiness.** The fleet card put the agent name, four counters and every attached ticket in one `flex-wrap` row, and the complaint it produced was that the card's contents sat "in random places": a five-ticket workspace flowed its chips out of the middle of a run of figures and down three ragged lines, while a one-ticket workspace read as a different component. **Wrapping is a property of a SET**, so a row holding three sets cannot wrap any of them correctly — chips want to wrap as chips, prose as prose, figures as a baseline. The fleet card is four regions (marks, task, error, ledger; the ledger is counters then tickets), each with its own wrap, and **a region with nothing in it renders nothing** — so the order is invariant while the height is not, which is exactly the property a wall of cards needs. Give every region a `data-testid`: the order is the contract, and it is the only part a static render can hold.

**Where the regions END is a decision too.** `SectionCard`'s body is `flex-1` and `CardGrid` stretches every card in a row to the tallest, so `mt-auto` on the last region pins it to the card's floor and lines the counters up ACROSS the row. Without it a card with a one-line task and a card with five tickets put their figures at two unrelated heights and the eye has to find each one — the same content, arranged so it cannot be compared.

**Overflow: a NAME truncates, a SENTENCE wraps.** Every cell that can overflow is `min-w-0`, and **truncated text always carries the full value in `title`** — truncation without a way to recover the value is data loss. But truncation only works where the head of the value identifies it. Clipping a sentence deletes its predicate, which is usually the half the row exists for: truncating a pace row kept "143.2% of the window at this rate" and cut the forecast it was qualifying. **Solve overflow in the ROW, not per string** — one grid, the naming cell `min-w-0 truncate`, the qualifying cell `shrink-0` and free to take a second line.

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

**Degraded is the state most often shipped as nothing, because refusing looks like rigour.** The usage trend's plan ceiling demanded that every account contribute and so drew no line at all on a real host — one idle account with nothing to extrapolate from erased a perfectly good cap. A stated partial bound is the honest answer, and it is the same rule as a bounded list's "Showing 100 of 2.5K" (§9): **draw what you can prove and name the horizon.** An unqualified `cap` on the line would have been read as the whole cap however the caption was worded, which is why the qualifier goes on the mark.

---

## 11. Empty, degraded and null states are QUIETER

**An absence of data is never the loudest thing on a screen.** The sharpest live failure: on `/usage`, `not measured` renders `text-2xl font-semibold text-muted-foreground` — 20px/600, the same size and weight as `35.9B`, the largest real figure on the page. The biggest, boldest text on the screen is the absence of data.

The rule, in tokens:

- **A null value renders one tier down and one step down from the value it replaces.** A display figure's absence is `text-lg text-content-tertiary`, never `text-2xl font-semibold`.
- **It is never `font-semibold`.** Weight is for things that are there.
- **It is always `--content-tertiary`.** Never primary, never coloured — an absence is not an error.
- **It names what is missing, scoped to the field.** "not measured" on the one figure that is unmeasured, never across a card holding real data beside it. Unmeasured is `unknown`, never `0` — a fabricated zero misleads a user about their own spend.
- **An empty *surface* is centred, `text-sm text-content-tertiary`, with one action** at `variant="outline"`. Not a primary: an empty state is not the screen's purpose.

---

## 12. The governing discipline

**An icon or a colour earns its place by adding recognition or encoding state. If it is doing neither, leave it off.**

The failure on one side is a screen with no entry point, where everything is the same size in the same near-white. The failure on the other side is an app that looks like a candy store. Both come from the same root cause: decisions made per screen instead of once.

Three questions before adding anything visual:

1. **Does this encode state a user could act on?** If yes, it is colour or a badge. If no, it is neutral text.
2. **Does this make the thing recognisable faster than reading it?** If yes, it is a glyph from section 7. If no, it is noise.
3. **Is there already a token, a component, or a table for this?** There almost always is. Reach for it.

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
| Rail (`shell/app-sidebar.tsx`) | Row titles 14→13px, metadata stays 12px | The 260px width: does the title still clip? **It did — the rail is 392px now, see below.** |
| Shell header | Page title `text-xl` 20→18px | Header stays `h-12`; the title must not look lost in it |
| Fleet cards | Titles 14→13px, subtitles 12px, badges 12px | Two-line card height; badge row wrapping |
| Work panel tabs | Tab labels 14→13px | Tab strip height and hit targets |
| Workspace transcript | **Nothing** — its prose is the 16px root default, not `text-base` | Adopting `text-base` is its own PR; it drops prose to 14px and inline code to 11.9px |
| Sessions table | Cells 14→13px, headers 12px | Column widths were tuned at 14px; `LABEL_COL`/`CAPPED_CELL` re-measure |
| Session detail | Same as transcript | — |
| Usage stat tiles | Figures `text-2xl` 24→20px | Tile height; the `not measured` case gets *worse-looking* until its own PR |
| Usage tables | Cells 14→13px | Same column re-measure |
| Login | Pairing code `text-3xl` 30→24px | It is the only thing on the screen; confirm it still reads as the subject |
| Account menu | Items 14→13px | Menu item hit targets stay ≥32px |

**The one thing PR 0 does not fix.** ~~The rail title needs 138px in a 123px slot at 14px. At 13px it needs **128px** — still 5px over. The title must also drop to `text-xs`, which needs **118px** and fits with room. Size reduction alone does not clear it; that is a surface delta, not a ramp property.~~

**SUPERSEDED.** The last sentence was right and its own prescription was wrong: this was never a ramp property, so it was fixed at the surface — by widening the rail, twice, now to `w-98`, not by shrinking the title. `text-xs` here would now be a size reduction solving a constraint that no longer exists, at the cost of collapsing the title into the metadata line under it. **The rail title stays `text-sm`.**

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
| Cards, fleet workspace cards, stat tiles | `raised` |
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
| **Exempt, and say so** | 98 of 103 elements are mono with raw ANSI colour. This is content. Do not "fix" it, and add a comment so the next reader does not. |
| **Rung** | `sunken`. A terminal is the clearest case for the downward direction — content the surface contains rather than presents. The ANSI exemption is about the *text*; the well it sits in is ours. |
| Type | The one Grove-owned string is the stream label at `font-mono text-xs uppercase` — the only sanctioned `uppercase` in the app, because it is a literal channel name |
| States | Disconnected and empty-buffer states get the quiet treatment; a blank terminal must not look like a failed render |

#### Work · Changes — done, and one row was UNREACHABLE

Its own strings are on the tiers. **"Commit subject primary, SHA and author tertiary" was struck as unreachable, not done:** the commit list is the vendored `elements/timeline`, and subject, SHA and date are styled inside it. `commitEvents` hands it `title` / `time` / `detail` as plain strings, so the call site has no seam to tier them through.

**That is the correct outcome rather than a gap to close.** Restyling a vendored component is the one move this app forbids, and there is no prop for it — so the honest options are to accept upstream's ranking or raise it upstream, never to reach in. **Before writing a tier row for a surface, check whether the surface actually owns the text**; three of the deltas on this page turned out to belong to `card.tsx` or to `elements/`, not to the tab.

#### Work · Files — `file-row.tsx`, `files-tab.tsx`

| Delta | Detail |
|---|---|
| Mono | `file-row.tsx`'s `+1 −0` and `files-tab.tsx`'s edit totals are mono at the CALL SITE, not through `CardStat` → sans + `tabular-nums`. Paths and filenames stay mono. |
| Exempt | `9px/700` inside `react-file-icon` is SVG artwork, not type. Leave it. |
| Content tiers | Filename primary, directory prefix tertiary, stats tertiary |

#### Work · Info — done

Badges and mono are done, and what is left mono here (`main`, the base ref) is correctly mono. The figures come through `CardStat`.

**The tier row was already satisfied by the primitive, which is worth knowing before you go looking for it.** §8 asks for tertiary field labels and primary values; `CardField`'s `dt` carries the tertiary and its `dd` **inherits** primary from the card body, which sets `text-sm` and no colour. So the values are primary because nothing overrides them, not because a class says so — correct, and invisible to a grep for `text-content-primary`. **Absence of a tier class is not absence of a tier.**

**The Identity and Branch cards went to four badges and two, and the round trip is the lesson.** One pass deleted three of Identity's four chips on the reasoning that runtime, placement and the agent's name are FIXED for the life of a workspace and §6 reserves badge chrome for what changes; the next pass put them back, because the tertiary metadata line that replaced them had no visible structure and read as jumbled prose. Both passes were right about badges as *signals* and only the second was right about the card as a *layout* — see §6, which now states the boundary job the tone table had always implied. Constants take `outline`, only the lifecycle status carries a tone, and Branch matches it with the glyph carrying the rank (`GitBranchIcon` for the branch that moves, `GitForkIcon` for the settled origin). **When a row of chips ranks nothing, the answer is a rank, not a demotion out of the row.**

The no-base case stayed a SENTENCE rather than becoming a chip: there is nothing to mark, so an edge drawn around an absence would claim one.

Two specifics from these passes worth keeping:

- **The runtime fallback is the one chip in that row that would still be a badge under the strictest reading.** A fallback is not a property — it records that the isolation contract the workspace *asked for* was not honoured, which is a state, and one only a respawn clears. It keeps `outline` anyway, because the row already has its toned mark.
- **The agent name is rendered exactly as configured, with no capitalisation applied.** Measured, it reads `Claude Code (via …)` — already prose, and the user's own words. Title-casing somebody's proper nouns is the same mistake as stripping characters out of a ticket title. `capitalize` is for the lower-case wire enums (`status`, `runtime`) and nothing else.

#### Work · Controls — done

The MCP-server names are a plain wrapped list at tertiary, mono kept because a server name is an identifier. `ModelChip` keeps its `secondary`/`outline` pair deliberately: that is a `selected` toggle, not a domain state, so a state table would be a table of one boolean.

**One delta here was struck as WRONG rather than done: "card descriptions secondary".** A `SectionCard`'s description is set inside `card.tsx`, and §8 fixes it at `text-xs text-content-tertiary` — so the row was asking a surface to override a shared anatomy from the outside, which it cannot do and should not want to. §8 wins; a description is metadata about the card, not a claim competing with its title. If that is ever wrong it is wrong for every card at once, and it changes in §8.

### Fleet create dialog — `components/grove/fleet/create-workspace-dialog.tsx`

**The clearest case of the undesigned default in the app.**

| Delta | Detail |
|---|---|
| Type | **31 of 53 elements sit at the 16px/400 browser root**, with no `text-*` utility anywhere. Give the dialog body `text-sm` and its title `text-lg`. |
| Content tiers | **51 of 53 elements are one tier.** Field labels tertiary, help text secondary, values primary. |
| Buttons | One primary (Create), one secondary (Cancel). Verify no third competes. |
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

This is also the one place Launch mixes two vendored composers, and the reason is worth keeping: the shell, toolbar, action group and send button all come from `components/elements/composer` — which ships `ComposerToolbar` as a `justify-between` row and is therefore the slot the control pills sit in — but its `ComposerInput` is a single-line `<input>`, and there is no multi-line input anywhere in the vendored elements tree. So the text area alone is assistant-ui's `ComposerPrimitive.Input`. That single substitution is the *only* reason the Launch surface mounts a runtime at all; anyone replacing it with `ComposerInput` must remove the runtime in the same change.

**§7 — Launch takes `SproutIcon`.** Not a rocket. A rocket is the stock glyph for anything named "launch" and would be exactly the undesigned default §0 warns about; Grove's vocabulary is a forest, Fleet already holds `TreesIcon`, and starting a workspace is planting one. The mark currently renders nowhere — `RAIL_ITEMS` excludes `/` because the brand mark links there — but `NavItem.icon` is required, and a required field still takes a decided value rather than the first plausible one.
