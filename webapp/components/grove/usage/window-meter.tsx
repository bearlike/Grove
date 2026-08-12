"use client";

import { InfoIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { CardFields } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";
import { approxDurationUntil, durationUntil, useNow } from "@/components/grove/relative-time";
import type { SubscriptionWindowProjection, SubscriptionWindowView } from "@/lib/grove/api";
import { cn } from "@/lib/utils";
import { AbbreviatedNumber } from "./abbreviated-number";
import { abbreviate, exact, humanize, percent, timestamp } from "./format";
import { CAPACITY_GAP_REASON, capacityFor, projectionVerdictTone } from "./tokens";

/**
 * One subscription, as ONE compact block rather than N full-height meters.
 *
 * **The trap this shape refuses to fall into: these percentages are NOT parts
 * of a whole.** `session`, `weekly_all` and `weekly_scoped` are independent
 * readings of DIFFERENT windows — a session at 2%, a weekly window at 68%, a
 * scoped weekly window at 0% — never three slices that sum to a budget. A
 * single stacked bar concatenating those fills would render ~70% and read as
 * "70% of one thing", which is a number nobody measured. So the shape is N
 * THIN TRACKS, each against its own 100%, sharing one label column and one
 * colour vocabulary — never one bar whose segments add up.
 *
 * **Colour is IDENTITY, not state (§4.1): which window this is, not something
 * that changes.** It rides the chart palette (`--chart-1`…`--chart-5`) rather
 * than the semantic set, and that same colour is carried into the label next
 * to each track, the bullet that describes it below, and the tooltip behind
 * its (i). The palette has five stops; a sixth facet draws with no colour at
 * all — see `facetAccent` — rather than repeating an earlier facet's hue,
 * which would claim an identity two different windows do not share. The word
 * label is always the carrier past that point, the same as it always was the
 * second one (§4.7).
 *
 * The tracks are the SCAN row: bar, percent, nothing else, so the block's
 * height is `facetCount × one thin row` — deterministic, and it degrades
 * gracefully past five facets because nothing about the row depends on having
 * a colour. Every other fact the old per-facet meter carried (remaining %,
 * reset time, the token pair, the pace verdict and its forecast sentence) now
 * lives in the bulleted list underneath, one item per facet in the same
 * order, so a track lines up with its bullet by position.
 */
export function WindowMeters({
  windows,
}: {
  windows: readonly SubscriptionWindowView[];
}): React.ReactNode {
  return (
    <div className="grid min-w-0 gap-2" data-testid="usage-quota-windows">
      <div className={TRACKS} data-testid="usage-quota-tracks">
        {windows.map((window, index) => (
          <FacetTrack
            key={`${window.scope}-${window.label}`}
            window={window}
            accent={facetAccent(index)}
          />
        ))}
      </div>
      {/* `SUPPLEMENTARY_RULE` draws the boundary between the SCAN tier above
          (bar, percent, nothing else) and the READ tier below (everything the
          old full-height meter said about each window) — the first of the
          card's two enforced rules; the second is at the account footer in
          `quota.tsx`, reusing this same export rather than a second literal. */}
      <ul className={cn(SUPPLEMENTARY_RULE, "flex flex-col gap-2")} data-testid="usage-quota-facets">
        {windows.map((window, index) => (
          <FacetSummary
            key={`${window.scope}-${window.label}`}
            window={window}
            accent={facetAccent(index)}
          />
        ))}
      </ul>
    </div>
  );
}

/**
 * The one row geometry for the tracks block, shared across every facet so
 * their label and percent columns land in one aligned grid rather than each
 * row picking its own widths.
 */
const TRACKS = "grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-2";

/**
 * The account card's ONE supplementary-tier boundary, reused everywhere a
 * block steps down from measurement to bookkeeping ABOUT the measurement.
 *
 * Two steps in this card are that kind of step, not a topic change: tracks
 * (what was measured) → facet insights (what it means) is the first, and the
 * whole windows block → spend/last-refresh footnote is the second, drawn at
 * the call site in `quota.tsx` with this same export. `border-t border-border`
 * is `components/grove/card.tsx`'s own divider vocabulary (`SectionCard`'s
 * header/body seam) rather than an invented rule, so the two nested boundaries
 * read as the same kind of line as the card's own header rule — one enforced
 * pattern, not a rule per block.
 */
export const SUPPLEMENTARY_RULE = "border-t border-border pt-2";

/** How many chart stops the identity palette has. Past this many facets there
 * is no colour left to hand out that would not repeat — and repeating one
 * would claim a shared identity two different windows do not have. */
const PALETTE_STOPS = 5;

/**
 * The facet's identity colour, or none once the palette runs out.
 *
 * Returns a `var(--chart-N)` reference rather than a resolved value, because
 * the value has to differ by theme and the CSS variable already does that.
 * `null` past the fifth facet is a deliberate degrade, not a bug: the caller
 * falls back to the vendored component's own default colour and a plain "·"
 * marker rather than wrapping the index back to `--chart-1`.
 */
function facetAccent(index: number): string | null {
  return index < PALETTE_STOPS ? `var(--chart-${index + 1})` : null;
}

/**
 * A figure GROVE CALCULATED, marked so it cannot be read as the provider's own
 * word.
 *
 * **Almost nothing on this card is published.** Verified against the live
 * daemon on 2026-08-11: of the five figures a weekly facet shows, exactly one
 * — the percentage — is the vendor's; the measured tokens are Grove's index,
 * the capacity is Grove's inversion of that percentage, the countdown is
 * Grove's clock and the pace is Grove's extrapolation. A reader deciding
 * whether to trust a number against their own bill needs that split, and no
 * amount of prose delivers it at a glance.
 *
 * **A dashed rule, matching the dashed strokes the chart already draws its
 * derived lines with** — the forecast and every ceiling. One vocabulary across
 * both surfaces, so "dashed means Grove worked this out" is learnable once.
 * Deliberately NOT the dotted rule `Explain` uses: that one means "there is a
 * definition behind this word", a different claim that appears on the same
 * rows.
 *
 * `title` is REQUIRED, not optional. A text decoration is a visual carrier and
 * §4.7's rule applies to it exactly as it applies to colour — the mark is the
 * second carrier and the sentence behind it is the first, which is also what
 * makes the figure legible to a screen reader and in a greyscale screenshot.
 */
export function Derived({
  children,
  title,
}: {
  children: React.ReactNode;
  title: string;
}): React.ReactNode {
  return (
    <span className={DERIVED_MARK} title={title} data-derived="true">
      {children}
    </span>
  );
}

/**
 * The mark itself, exported so the two elements that cannot be WRAPPED by
 * `Derived` still wear the identical rule.
 *
 * `ResetsIn` renders a `<time>` that must keep its own `dateTime` and `title`,
 * and the chart's notes mark whole rows. A second literal in either place is
 * how one surface ends up dotted and the other dashed, which would silently
 * merge this claim with the glossary's.
 */
export const DERIVED_MARK = "underline decoration-dashed underline-offset-2";

/**
 * The identity marker, reused in the track row, the bullet and the tooltip
 * header so the same colour means the same facet everywhere it appears.
 *
 * A square, not the vendored badge's pill — same reason `series.tsx`'s
 * tooltip swatch is square: a radius utility under `components/grove` fails
 * `lint:styling`, so the shape a caller CAN draw here is a plain rectangle.
 * The colour rides an inline custom property rather than a palette class for
 * the same reason.
 */
function Swatch({ accent }: { accent: string | null }): React.ReactNode {
  if (!accent) {
    return (
      <span aria-hidden className="w-2 shrink-0 text-center text-content-tertiary">
        ·
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className="size-2 shrink-0 bg-(--swatch)"
      style={{ "--swatch": accent } as React.CSSProperties}
    />
  );
}

/**
 * One facet's row in the tracks grid: label, its own bar against its own
 * 100%, and the percentage that bar is drawing.
 *
 * Returns a Fragment of three grid children rather than a wrapping element —
 * exactly like `CardFields`' `dt`/`dd` pairs — so every facet's cells land in
 * the SAME grid as its siblings and the three columns size to the widest
 * label, not to whichever facet happened to render first.
 */
function FacetTrack({
  window,
  accent,
}: {
  window: SubscriptionWindowView;
  accent: string | null;
}): React.ReactNode {
  const meter = meterFor(window);
  const title = humanize(window.label);

  return (
    <>
      <span
        className="flex min-w-0 items-center gap-1.5"
        title={title}
        data-testid="usage-quota-track"
      >
        <Swatch accent={accent} />
        <span className="truncate text-xs text-content-primary">{title}</span>
      </span>
      {meter ? (
        <Progress
          // Taller than the vendored default: this bar carries most of the
          // card's read weight (§3 of the brief — "give the progress bars
          // more of the card's allocated height"), and at the old `h-1.5` it
          // was thinner than the badge sitting two rows below it, which is
          // backwards for the block's own primary measurement.
          className="h-2"
          value={meter.used}
          aria-label={`${title} usage`}
          // `aria-valuenow` is supplied HERE because the vendored Progress
          // consumes `value` for the indicator transform and never forwards
          // it to the Radix root — so the bar draws correctly while the root
          // stays `indeterminate` and announces no value at all. Radix
          // spreads caller props last, so this lands without touching the
          // vendored file.
          aria-valuenow={Math.round(meter.used)}
          aria-valuetext={`${percent(meter.used)} used`}
          // The one place the identity colour reaches the vendored bar: both
          // the track (`bg-primary/20`) and the indicator (`bg-primary`) are
          // Tailwind classes that resolve `var(--color-primary)`, and a CSS
          // custom property set on the element itself shadows that lookup
          // for this one bar only — no vendored file touched, no colour
          // utility written in Grove code. Past the fifth facet `accent` is
          // `null` and the bar keeps the vendored default.
          style={accent ? ({ "--color-primary": accent } as React.CSSProperties) : undefined}
        />
      ) : (
        <span className="min-w-0 truncate text-xs text-content-tertiary">Not measured</span>
      )}
      <span className="shrink-0 text-xs tabular-nums text-content-tertiary">
        {/* Marked HERE too, even though the scan row is "bar, percent, nothing
            else": a derived percentage left plain on the row a reader actually
            scans is the one place the provenance claim would go unmade, and a
            rule that applies on the read tier but not the scan tier is not a
            rule. */}
        {meter === null ? null : meter.basis === "reported" ? (
          percent(meter.used)
        ) : (
          <Derived title={meter.provenance}>{percent(meter.used)}</Derived>
        )}
      </span>
    </>
  );
}

/**
 * One facet's entry: identity and verdict on one row, then every figure the
 * window supports as a LABELLED value.
 *
 * **The single joined sentence this replaced was the defect, not the style.**
 * It ran `34% used · 66% left · resets in 4d 22h · 12.6M used of ~37.1M
 * estimated · 41.2% of the window at this rate` — five independent facts in
 * one tertiary line, in a 366px column, so it wrapped to three rows of prose
 * with no entry point and nothing to scan down. Every figure now sits in a
 * `dt`/`dd` pair on ONE grid shared by every facet of the account, so the
 * values line up in a column a reader can run an eye down and a missing row is
 * visibly missing rather than silently absent from a sentence.
 *
 * **Four rows, fixed, in the order a reader asks the questions**: how much is
 * gone, how much there was, when it comes back, where the pace is heading.
 * `Resets` and `Pace` drop out when the window did not report them; `Used` and
 * `Capacity` always render, because "not enough signal" is an answer and a
 * blank is not (§11).
 */
function FacetSummary({
  window,
  accent,
}: {
  window: SubscriptionWindowView;
  accent: string | null;
}): React.ReactNode {
  const meter = meterFor(window);
  const title = humanize(window.label);
  const projection = window.projection;
  const now = useNow();

  return (
    <li
      className="flex min-w-0 flex-col gap-1"
      data-testid="usage-quota-facet"
      data-scope={window.scope}
      data-verdict={projection?.verdict}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
        <Swatch accent={accent} />
        {/* This is the one place a facet's own name is explained — not the
            compact track row just above, which the module's own comment
            already calls "bar, percent, nothing else": a second dotted-
            underline right beside the first would be the tooltip-on-every-
            noun failure the glossary module exists to avoid. */}
        <Explain term="quota_window" className="truncate text-xs font-medium text-content-primary">
          {title}
        </Explain>
        {humanize(window.scope) === title ? null : (
          <span className="shrink-0 text-xs text-content-tertiary">{window.scope}</span>
        )}
        {/* Verdict badge and detail trigger form ONE cluster at the row's
            trailing edge instead of the badge sitting mid-row against the
            identity text. A pill is unavoidably heavier than plain text —
            the vendored `Badge` has one size and it is not ours to shrink —
            so the fix is room, not restyling: pushed to its own end of the
            row with `ml-auto`, it reads as the row's status column rather
            than a fifth word wedged between the label and the scope. */}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {/* **`unknown` is a real, common answer and never an error.** A
              window that reported no duration — every Claude window today —
              or one that has barely begun cannot be judged, so it carries no
              badge at all rather than a warning tone or a fabricated `0%`:
              either would make the ordinary case look like a fault. */}
          {projection && projection.verdict !== "unknown" ? (
            <Badge variant={projectionVerdictTone(projection.verdict)}>
              {VERDICT_LABEL[projection.verdict]}
            </Badge>
          ) : null}
          <WindowDetail quota={window} meter={meter} accent={accent} />
        </span>
      </div>
      {/* `gap-x-2`, tighter than `CardFields`' own `gap-x-3`: these labels are
          one short word and the column is 366px, so the default gutter pushed
          every value a third of the way across the card away from its name. */}
      <CardFields className="gap-x-2 gap-y-0.5 pl-3.5" data-testid="usage-quota-facet-detail">
        <Figure label="Used">
          <UsedValue window={window} meter={meter} />
        </Figure>
        <Figure label="Capacity">
          <CapacityValue window={window} />
        </Figure>
        {window.resets_at ? (
          <Figure label="Resets">
            <ResetsIn iso={window.resets_at} />
          </Figure>
        ) : null}
        {projection ? (
          <Figure label="Pace">
            <PaceValue projection={projection} now={now} />
          </Figure>
        ) : null}
      </CardFields>
    </li>
  );
}

/**
 * One labelled figure inside a facet.
 *
 * **`CardField` is deliberately not reused**, and the reason is §8's overflow
 * rule rather than styling: its `dd` is `truncate`, which is right for a name
 * whose head identifies it and wrong for every value here — clipping
 * `143.2% of the window at this rate · forecast to reach the limit in ~2d`
 * deletes the forecast the figure was qualifying. A name truncates, a
 * qualifying value wraps. The tiers are §8's own (`text-xs` tertiary label,
 * `text-xs` primary value) so the two components still look identical.
 */
function Figure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-xs text-content-tertiary">{label}</dt>
      <dd className="min-w-0 text-xs tabular-nums text-content-primary">{children}</dd>
    </>
  );
}

/**
 * How much of the window is gone, and the evidence behind it.
 *
 * The percentage carries the provider's own claim where the provider made one
 * and Grove's arithmetic where it did not — which is exactly the split
 * `Meter.basis` exists to carry, and why the mark cannot be decided here.
 *
 * The evidence beside it is whichever count is real: a counting provider's own
 * `used` figure (reported), or Grove's measured tokens for the window's bounds
 * (derived). Never both, never a fabricated zero when neither exists.
 */
function UsedValue({
  window,
  meter,
}: {
  window: SubscriptionWindowView;
  meter: Meter | null;
}): React.ReactNode {
  if (!meter) {
    return (
      <span className="text-content-tertiary">
        not measured — neither a percentage nor a count
      </span>
    );
  }
  const measured = window.projection?.tokens_used;
  const counted = typeof window.used === "number" ? window.used : null;

  return (
    <>
      {meter.basis === "reported" ? (
        <span>{percent(meter.used)}</span>
      ) : (
        <Derived title={meter.provenance}>{percent(meter.used)}</Derived>
      )}
      {typeof window.remaining_percent === "number" ? (
        <span className="text-content-tertiary"> · {percent(window.remaining_percent)} left</span>
      ) : null}
      {counted !== null ? (
        <span data-testid="usage-quota-quantities">
          {" · "}
          {abbreviate(counted)}{" "}
          <span className="text-content-tertiary">{window.unit ?? "used"}</span>
        </span>
      ) : typeof measured === "number" ? (
        <span data-testid="usage-quota-quantities">
          {" · "}
          {/* `abbreviate` inside `Derived` rather than `AbbreviatedNumber`:
              that component mounts its own tooltip, which would put two hover
              affordances on one figure. One `title` carries both duties — the
              exact value §3 promises, and the provenance sentence §4.7's rule
              requires beside a visual mark. */}
          <Derived title={measuredTitle(measured)}>{abbreviate(measured)}</Derived>{" "}
          {/* The count sums every token class including cache reads, which
              routinely dwarf the rest, so the figure reads far larger than the
              fresh work it looks like it reports. */}
          <span className="text-content-tertiary">
            <Explain term="cache_read_tokens">tokens</Explain>
          </span>
        </span>
      ) : null}
    </>
  );
}

function measuredTitle(measured: number): string {
  return `${exact(measured)} tokens, summed by Grove from the sessions it has indexed inside this window's own bounds.`;
}

/**
 * How large this window is — the figure the whole card was missing.
 *
 * **No provider publishes a subscription token budget**, so on every real
 * subscription window this is Grove inverting its own measured tokens through
 * the percentage the provider reported. The `~` and the dashed rule both say
 * so, and the `title` names the two inputs so a reader can check the division
 * rather than take it.
 *
 * The gap branch is the ordinary case, not an error: two of this host's four
 * live windows sit at 0% used, where there is nothing to extrapolate from
 * however many tokens were measured. It names the missing SIGNAL rather than
 * printing a dash, because "—" and "we cannot tell you yet" are different
 * claims and only the second is true.
 */
function CapacityValue({ window }: { window: SubscriptionWindowView }): React.ReactNode {
  const reading = capacityFor(window);

  if (!reading.known) {
    return (
      <span className="text-content-tertiary" data-testid="usage-quota-capacity-gap">
        {CAPACITY_GAP_REASON[reading.gap]}
      </span>
    );
  }

  const { total, basis, unit } = reading.capacity;
  return (
    <span data-testid="usage-quota-capacity" data-basis={basis}>
      {basis === "published" ? (
        abbreviate(total)
      ) : (
        <Derived title={capacityTitle(window, total)}>~{abbreviate(total)}</Derived>
      )}
      {unit === null ? null : <span className="text-content-tertiary"> {unit}</span>}
    </span>
  );
}

function capacityTitle(window: SubscriptionWindowView, total: number): string {
  const measured = window.projection?.tokens_used;
  const used = window.used_percent;
  const claim = `Grove's estimate: about ${exact(total)} tokens for the whole window. No provider publishes a token budget.`;
  return typeof measured === "number" && typeof used === "number"
    ? `${claim} Derived from ${exact(measured)} measured tokens at ${percent(used)} used, so it is only as accurate as a percentage the provider rounds to whole numbers.`
    : claim;
}

/**
 * Where this pace lands by the window's own reset — a FORECAST, said in those
 * words.
 *
 * The wording this keeps was fixed deliberately and is not reopened here: the
 * earlier `limit reached <date>` was read as *the limit has already been
 * reached*, which is the opposite of what it means, so every phrase is
 * explicitly forward-looking and the span is relative because "in ~2 days" is
 * understood at a glance where a date has to be subtracted from today.
 *
 * `now === null` before mount, and the forecast degrades to the absolute
 * instant rather than disappearing — the same absolute-then-relative contract
 * `RelativeTime` keeps, so the server and the browser agree on first paint.
 */
function PaceValue({
  projection,
  now,
}: {
  projection: SubscriptionWindowProjection;
  now: number | null;
}): React.ReactNode {
  // **`unknown` is a real, common answer and never an error.** A window with
  // no resolvable duration, or one that has barely begun, cannot be judged.
  if (projection.verdict === "unknown") {
    return <span className="text-content-tertiary">not enough signal yet</span>;
  }

  const projected =
    typeof projection.projected_percent === "number" ? projection.projected_percent : null;

  return (
    <>
      {projected === null ? (
        <span className="text-content-tertiary">on this window&rsquo;s own pace</span>
      ) : (
        <>
          <Derived title={paceTitle(projection)}>{percent(projected)}</Derived>{" "}
          <span className="text-content-tertiary">of the window at this rate</span>
        </>
      )}
      {/* Set ONLY when the pace actually reaches the limit before the window
          rolls. A window with headroom leaves it empty rather than naming a
          moment the account never reaches. */}
      {projection.exhausts_at ? (
        <>
          {" · "}
          <span className="text-content-tertiary">forecast to reach the limit </span>
          {/* `~` is not decoration: `exhausts_at` is arithmetic over a
              percentage the providers round to whole numbers, so the instant
              is precise in type only. Two units plus the tilde is the honest
              pairing — a single rounded unit made "~1 day" span 24 to 47
              hours, an imprecision larger than the one it was protecting
              against. */}
          <Derived title={`Forecast: ${timestamp(projection.exhausts_at)}, extrapolated from the current pace. Grove's arithmetic, not a provider statement.`}>
            {now === null
              ? timestamp(projection.exhausts_at)
              : `in ${approxDurationUntil(projection.exhausts_at, now)}`}
          </Derived>
        </>
      ) : null}
    </>
  );
}

function paceTitle(projection: SubscriptionWindowProjection): string {
  const burn = typeof projection.burn_rate === "number" ? projection.burn_rate.toFixed(2) : null;
  const elapsed =
    typeof projection.elapsed_percent === "number" ? percent(projection.elapsed_percent) : null;
  const base = "Grove's extrapolation of usage so far over elapsed time so far, held to the window's reset.";
  return elapsed === null || burn === null
    ? base
    : `${base} ${elapsed} of the window has elapsed, at ${burn}× the even pace.`;
}

/**
 * When this window rolls over, as a countdown.
 *
 * **The instant is the provider's; the countdown is Grove's**, so the value
 * carries the derived mark while its `title` holds the reported instant it was
 * computed from — the two halves of one row, neither of them lost.
 *
 * Mount-gated through the shared clock, so the server renders the absolute
 * instant and the browser replaces it. A duration read from the clock cannot
 * be produced identically on both sides of a hydration boundary.
 */
function ResetsIn({ iso }: { iso: string }): React.ReactNode {
  const now = useNow();
  const instant = timestamp(iso);
  return (
    <time
      dateTime={iso}
      title={`Resets ${instant} — the instant is the provider's; the countdown is Grove's, from your browser's clock.`}
      className={DERIVED_MARK}
      data-derived="true"
      data-testid="usage-quota-resets"
    >
      {now === null ? instant : `in ${durationUntil(iso, now)}`}
    </time>
  );
}

/**
 * The verdict as a reader who does not know Grove would take it.
 *
 * `humanize(verdict)` used to render this straight off the wire slug, which
 * turned `over` into **"Over"** — and a customer reads that as *you have
 * already gone over*, which is the opposite of what it means. Every verdict
 * here is a FORECAST about where the current pace lands by the window's
 * reset; none of them describes the present. A one-word slug cannot carry
 * that tense, so the label says it instead of relying on the sentence beside
 * it to correct the impression the badge just made.
 *
 * The wire values are untouched — `on_track` / `tight` / `over` are a
 * published contract other clients read. This is presentation, and it is a
 * TABLE rather than a transform precisely because the mapping is a judgement
 * about wording that a general-purpose humanizer cannot make.
 */
const VERDICT_LABEL = {
  on_track: "On track",
  tight: "Nearing limit",
  over: "Projected over",
  unknown: "Pace unknown",
} as const satisfies Record<NonNullable<SubscriptionWindowProjection["verdict"]>, string>;

/**
 * The secondary detail, behind one affordance instead of on the card.
 *
 * WHY NOT `TooltipIconButton`, WHICH IS VENDORED AND DOES EXACTLY THIS. Its
 * `tooltip` prop is typed `string`, so it can carry a sentence and nothing
 * else; this content is a term/value table. The composition below is that
 * component's own anatomy — `Tooltip` + `TooltipTrigger asChild` + a ghost
 * icon `Button` — with a node where it hard-codes a string, so every part is
 * still vendored and nothing here restyles anything.
 *
 * The `accent` swatch in the header carries the same colour language as the
 * track and the bullet into the tooltip, so hovering the (i) still lands on
 * the facet it belongs to rather than a generic panel.
 */
function WindowDetail({
  quota,
  meter,
  accent,
}: {
  quota: SubscriptionWindowView;
  meter: Meter | null;
  accent: string | null;
}): React.ReactNode {
  const label = `${humanize(quota.label)} details`;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={label} data-testid="usage-quota-detail">
          <InfoIcon aria-hidden />
        </Button>
      </TooltipTrigger>
      <TooltipContent className="w-72">
        <div className="mb-1.5 flex items-center gap-1.5">
          <Swatch accent={accent} />
          <span className="text-xs font-medium">{humanize(quota.label)}</span>
        </div>
        <WindowDetailRows quota={quota} meter={meter} />
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * The tooltip's body, EXPORTED so it can be rendered on its own.
 *
 * Radix portals its content and mounts it only while open, so nothing inside
 * a `TooltipContent` exists in an SSR render — which is this suite's whole
 * idiom. Testing it through the trigger would need a DOM plus a hover, and
 * testing it by reaching for a private symbol would make the private name a
 * contract that moves silently. A named export is the honest seam: the rows
 * are a pure function of the window, and that is exactly what the tests pin.
 */
export function WindowDetailRows({
  quota,
  meter,
}: {
  quota: SubscriptionWindowView;
  meter: Meter | null;
}): React.ReactNode {
  const projection = quota.projection;
  const reading = capacityFor(quota);
  const capacity = reading.known ? reading.capacity : null;

  return (
    <>
      <CardFields className="gap-y-0.5">
          {meter ? <Detail label="Used">{percent(meter.used)}</Detail> : null}
          {typeof projection?.elapsed_percent === "number" ? (
            <Detail label="Elapsed">
              {percent(projection.elapsed_percent)} of the window
            </Detail>
          ) : null}
          {typeof projection?.burn_rate === "number" ? (
            // `1.0×` is exactly on pace to finish at the limit, so stating the
            // baseline is what makes the number mean anything on its own.
            <Detail label="Burn rate">
              {projection.burn_rate.toFixed(2)}× the even pace
            </Detail>
          ) : null}
          {quota.resets_at ? (
            <Detail label="Resets">{timestamp(quota.resets_at)}</Detail>
          ) : null}
          {projection?.exhausts_at ? (
            <Detail label="Forecast">{timestamp(projection.exhausts_at)}</Detail>
          ) : null}
          {typeof projection?.tokens_used === "number" ? (
            <Detail label="Measured">
              <AbbreviatedNumber value={projection.tokens_used} /> tokens
            </Detail>
          ) : null}
          {/* Through `capacityFor`, NOT off the projection field directly.
              The card's Capacity row and this one are the same claim about
              the same window, and reading the wire twice is how the two come
              to disagree the day a published `limit` starts arriving. */}
          {capacity === null ? null : (
            <Detail label={capacity.basis === "published" ? "Allowance" : "Allowance (est.)"}>
              {capacity.basis === "published" ? "" : "~"}
              <AbbreviatedNumber value={capacity.total} /> {capacity.unit ?? ""}
            </Detail>
          )}
          {quota.evidence === "unknown" ? null : (
            <Detail label="Read from">{humanize(quota.evidence)}</Detail>
          )}
        </CardFields>
        {capacity?.basis === "derived" ? (
          // One sentence, not a paragraph, and it is the one thing a reader
          // cannot infer: no provider publishes a token budget, so this
          // figure is Grove's arithmetic and only as accurate as a rounded
          // percentage.
          <p className="mt-1.5 max-w-full">
            Allowance is Grove&rsquo;s estimate from the measured tokens, not a published
            budget.
          </p>
        ) : null}
    </>
  );
}

/**
 * One term/value row on the tooltip's INVERTED surface.
 *
 * Carries no colour at all, so both cells inherit `TooltipContent`'s own
 * `text-background` and the ranking comes from the columns. `CardField`
 * cannot be used here: its tertiary term is tuned against `--background` and
 * measured 3.25:1 on this inverted surface, under AA.
 */
function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className="min-w-0 tabular-nums">{children}</dd>
    </>
  );
}

type Meter = {
  used: number;
  /** Whose claim this percentage is. `reported` only where the provider itself
   * published a used percentage; every other branch computes one. */
  basis: "reported" | "derived";
  /** What a derived percentage was computed FROM, for the mark's `title`.
   * Present on both bases so the field cannot be forgotten when a branch
   * changes sides. */
  provenance: string;
};

/**
 * The window's three possible representations, narrowed to one meter — and
 * WHOSE each one is.
 *
 * A percentage is preferred because it is what a subscription window IS; the
 * counted form is kept because a token-budget provider does report
 * `used`/`limit`/`unit`, and deriving a percentage from those is exact.
 *
 * **Only the first branch is the provider's own word.** `100 − remaining` and
 * `used ÷ limit` are both Grove subtracting and dividing, and a card that
 * marked the three identically would be claiming the vendor said something it
 * did not. This is the whole reason `basis` rides on the meter rather than
 * being decided at the call site, which can only see the result.
 */
function meterFor(quota: SubscriptionWindowView): Meter | null {
  if (typeof quota.used_percent === "number") {
    return {
      used: clamp(quota.used_percent),
      basis: "reported",
      provenance: "Reported by the provider.",
    };
  }

  if (typeof quota.remaining_percent === "number") {
    return {
      used: clamp(100 - quota.remaining_percent),
      basis: "derived",
      provenance: `Grove's arithmetic: the provider reported ${percent(quota.remaining_percent)} remaining and no used figure.`,
    };
  }

  if (typeof quota.used === "number" && typeof quota.limit === "number" && quota.limit > 0) {
    return {
      used: clamp((quota.used / quota.limit) * 100),
      basis: "derived",
      provenance: `Grove's arithmetic: ${exact(quota.used)} of ${exact(quota.limit)} ${quota.unit ?? "units"} reported by the provider.`,
    };
  }

  return null;
}

function clamp(value: number): number {
  return Math.min(100, Math.max(0, value));
}
