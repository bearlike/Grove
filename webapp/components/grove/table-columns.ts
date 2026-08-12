/**
 * How this app's tables size their columns, and how their headers behave.
 *
 * Column geometry, not card anatomy — one module answering "how is a table laid
 * out here", so no surface has to re-derive it. It started under `usage/` when
 * the audit's two tables were the only tables; the session browser is the third,
 * and a rule serving more than one feature belongs above all of them.
 */

/**
 * A table column that should take exactly the width its digits need.
 *
 * `w-px` is not a one-pixel column: under `table-auto` a width smaller than the
 * content is read as "give me my minimum", so the browser sizes it to the
 * content and hands every spare pixel to the columns that did NOT ask. Without
 * it the numeric columns quietly claim the surplus — a three-digit session
 * count held 155px while the model name beside it ellipsised at 117px.
 */
export const NUMERIC = "w-px whitespace-nowrap text-right";

/**
 * The one column carrying a name — a model id, a project path.
 *
 * A PAIR, and both halves are load-bearing:
 *
 * `LABEL_COL` on the `<th>` asks for the REMAINDER. `width: 100%` on one column
 * of a `table-auto` layout is the idiom for "size every other column to its
 * content and give this one whatever is left" — it cannot overflow, because
 * what is left is by definition what fits.
 *
 * Two alternatives were measured and both pushed the table past its card at
 * 1440 (808px of table in a 760px card): a `max-w-[28rem]` ceiling, and a
 * `w-2/5` share. A fractional share is the subtler trap — the other columns
 * cannot shrink below their min-content, so demanding 40% for this one forces
 * the TABLE to grow until 40% is satisfied.
 *
 * `LABEL_CELL` on the `<td>` keeps `max-w-0`, which is what makes `truncate`
 * fire at all inside a table cell. On its own — which is how this shipped — it
 * is also the bug: it asks for ZERO width, so the auto layout gives the column
 * its minimum and hands the surplus to the numbers, ellipsising `bearlike/Grove`
 * in an 82px column while `Last active` sat on 209px. Paired with the remainder
 * on the header it means "take everything left over, then clip whatever still
 * does not fit" — which is the only honest reason to truncate.
 */
export const LABEL_COL = "w-full";
export const LABEL_CELL = "max-w-0 truncate";

/**
 * A SECOND long-value column, beside the one that took the remainder.
 *
 * EXACTLY ONE COLUMN MAY BE `LABEL_COL`. Two columns both asking for the
 * remainder do not split it — the auto layout hands nearly all of it to
 * whichever has the wider content and squeezes the other to its minimum.
 * Measured on the session browser at 1600px: Location took 845px and Branch was
 * left on 62px, which clipped `feat/usage-audit` to about four characters. That
 * is the very symptom `LABEL_COL` exists to prevent, reintroduced by using it
 * twice.
 *
 * So the second column asks for its content (`w-px` reads as "my minimum" under
 * `table-auto`) and caps itself instead. The cap is what makes `truncate` fire:
 * without it a single long branch name would widen the column and steal the
 * space back from the remainder.
 */
export const CAPPED_COL = "w-px whitespace-nowrap";
export const CAPPED_CELL = "max-w-56 truncate";

/**
 * ...and EXACTLY ONE COLUMN MUST BE. The rule cuts both ways, and the second
 * half cost a round trip to learn.
 *
 * A table whose every column is `w-px` has declared no remainder, but the
 * surplus does not evaporate — the browser hands it to the last column anyway.
 * Measured on the bash-commands table in a 760px card: moving `Command` off
 * `LABEL_COL` to stop it claiming ~63% of the width produced
 * `[80, 40, 55, 32, 50, 502]`, i.e. the same stretched-blank column relocated
 * from first to last and made **worse**, for a column whose usual value is one
 * em dash. The four numeric columns were byte-identical at 40/55/32/50 either
 * way, so the surplus never reached the columns it was supposedly freed for.
 *
 * **Choosing the remainder column is therefore a decision every table makes,
 * and declining to make it just picks the last one.** Two rules follow:
 *
 * - Do not give the remainder to a column of SHORT values. An identifier
 *   column of four-character words stretched across half a card reads as a
 *   layout bug, which is exactly the complaint that started this.
 * - Give it to the column that can spend width — prose, a note, a path. Blank
 *   space in a trailing tertiary note column reads as "nothing to note";
 *   blank space after a stretched identifier reads as broken.
 */

/**
 * §9's header treatment, which the vendored `TableHead` does not give you.
 *
 * It ships `font-medium text-foreground` and no size, so measured live a header
 * row reads **13px/500 at tier one** — the same weight and colour as the data
 * underneath it, which is how a header stops being chrome and starts competing
 * with the rows it labels. A column heading is metadata ABOUT the column:
 * present, not read.
 *
 * It supplies size and tier only. `font-medium` is left to the vendored
 * component rather than restated here — that is the composition rule, and it is
 * also why this constant looks like it is missing a weight.
 */
export const HEAD_CELL = "text-xs text-content-tertiary";

/**
 * A bounded scroll box whose table keeps its header row visible — the rule for
 * EVERY scrollable table in the app, defined once here so no page invents it.
 *
 * Put this on the element that owns the scroll; it needs no per-`th` class and
 * no change to the vendored `Table`.
 *
 * TWO THINGS ARE LOAD-BEARING, and the second is why a bare `sticky top-0`
 * fails silently:
 *
 * `[&_[data-slot=table-container]]:overflow-visible` — the vendored `Table`
 * wraps itself in its own `overflow-x-auto` div. CSS forces the other axis to
 * `auto` whenever one axis is not `visible`, so that inner div, not this one, is
 * the header's nearest scroll container — and it has no height bound, so it
 * never scrolls and a sticky header inside it never moves. Neutralising it makes
 * the outer box the single scroller, which is both what a frozen header needs
 * and one scroll idiom instead of two nested ones.
 *
 * `bg-background` — a sticky header is transparent by default, so rows scroll
 * THROUGH it. The token, never a literal: the header must match whatever surface
 * it is sitting on in either theme.
 *
 * The header's rule is left to the vendored `TableHeader` rather than restated
 * here: a collapsed-border table paints it from the first body row's top edge,
 * which stays put whether the header is sticky or not.
 */
export const FROZEN_TABLE_HEAD = [
  "overflow-auto",
  "[&_[data-slot=table-container]]:overflow-visible",
  "[&_thead]:sticky [&_thead]:top-0 [&_thead]:z-10 [&_thead]:bg-background",
].join(" ");
