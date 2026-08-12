import type { VariantProps } from "class-variance-authority";

import type { badgeVariants } from "@/components/ui/badge";
import type {
  BillingAccountView,
  SubscriptionWindowProjection,
  SubscriptionWindowView,
  UsageCoverageView,
} from "@/lib/grove/api";
import { humanize } from "./format";

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

type SourceHealth = UsageCoverageView["sources"][number]["health"];
type AccountStatus = BillingAccountView["status"];
/** Every verdict that IS one. `unknown` is the absence of a verdict, not a
 * quiet member of the set — see `PROJECTION_VERDICT_TONE`. */
type ProjectionVerdict = Exclude<SubscriptionWindowProjection["verdict"], "unknown">;

/**
 * Every state word this page marks, one table per axis — the same shape
 * `fleet/tokens.ts` uses, and for the same reason: a variant chosen at a call
 * site is a variant that will disagree with the next call site. Both axes were
 * a `cond ? "outline" : "secondary"` ternary, which collapses six distinct
 * statuses into two and cannot say that `auth_expired` wants a human while
 * `unsupported` never will.
 *
 * A `Record` over the wire union rather than a lookup with a fallback: a status
 * the daemon adds should fail to compile here instead of rendering as whatever
 * the else-branch happened to be.
 */

/**
 * How complete one indexed source is.
 *
 * `unreadable` is the only `destructive` here because it is the only one that
 * contributes nothing at all. `degraded` still carries real data — the page
 * prints a sentence naming what it understates — so it stays quiet; the word
 * does the work, and the colour is only ever the second carrier.
 */
const SOURCE_HEALTH_TONE: Record<SourceHealth, BadgeVariant> = {
  ok: "outline",
  degraded: "secondary",
  unreadable: "destructive",
};

/**
 * Whether a quota reading can be trusted.
 *
 * The split is "does a human have to do something": `auth_expired`,
 * `rate_limited` and `unreachable` are all states where the number on screen is
 * not the number that matters and someone has to act. `unsupported` and `stale`
 * are settled facts about the provider or the cache — nothing to fix.
 */
const ACCOUNT_STATUS_TONE: Record<AccountStatus, BadgeVariant> = {
  ok: "outline",
  stale: "secondary",
  unsupported: "secondary",
  auth_expired: "destructive",
  rate_limited: "destructive",
  unreachable: "destructive",
};

/**
 * Whether this window's burn rate lands under its own limit.
 *
 * The calm → attention → danger progression, spelled entirely in tones §6
 * already defines. There is no fourth colour and no amber: the design system
 * refuses a hue with no state table behind it, and the badge tones already rank
 * three steps — hairline, filled, loud.
 *
 * **`on_track` is the quietest deliberately.** It is the answer on almost every
 * window almost all of the time, and a page where every healthy window carries a
 * filled badge has spent its whole colour budget saying "fine", leaving nothing
 * to say "not fine" with.
 *
 * **`unknown` is absent from this map on purpose, and that is the one thing to
 * read carefully.** It is not a quiet verdict; it is the absence of one, which
 * happens whenever a window reports no duration (every Claude window today) or
 * has barely begun. §11 puts an absence in tertiary prose rather than in badge
 * chrome, so it never reaches a tone at all. `Exclude` keeps the exhaustiveness
 * that matters: a verdict the daemon adds still fails to compile here.
 */
const PROJECTION_VERDICT_TONE: Record<ProjectionVerdict, BadgeVariant> = {
  on_track: "outline",
  tight: "secondary",
  over: "destructive",
};

export function sourceHealthTone(health: SourceHealth): BadgeVariant {
  return SOURCE_HEALTH_TONE[health];
}

export function accountStatusTone(status: AccountStatus): BadgeVariant {
  return ACCOUNT_STATUS_TONE[status];
}

export function projectionVerdictTone(verdict: ProjectionVerdict): BadgeVariant {
  return PROJECTION_VERDICT_TONE[verdict];
}

/* ───────────────────────── capacity: the ONE estimator ─────────────────────
 *
 * How large is one subscription window, in tokens?
 *
 * **This lives here, once, because the card and the chart ask the identical
 * question and a second implementation is how they come to disagree on
 * screen.** The card prints a facet's capacity as a figure; the chart draws
 * the same number as that facet's ceiling line, normalized to a day. Two
 * estimators would let a reader see `~6.6B` on the card and a line drawn from
 * something else, with nothing to say which was meant.
 *
 * **Capacity is an INVERSION, and no provider publishes it.** Verified against
 * the live daemon on 2026-08-11: every one of four real windows across two
 * accounts reported `used`/`limit`/`unit` as null and a `used_percent`
 * instead. So the only route to a token figure is `measured_tokens ÷
 * (used_percent / 100)` — which the daemon already computes as
 * `projection.tokens_available_estimate`, and which this module consumes
 * rather than re-deriving. Nothing here re-does the arithmetic; it classifies
 * it, normalizes it and says what is missing when it is absent.
 */

const SECONDS_PER_DAY = 86_400;

/**
 * Whose claim a capacity figure is.
 *
 * The whole reason this discriminator exists: `published` is the provider's
 * own word and `derived` is Grove's arithmetic over a percentage the provider
 * rounds to whole numbers. A surface must be able to mark the second without
 * marking the first — see the design system's provenance rule.
 */
export type CapacityBasis = "published" | "derived";

/**
 * Why a facet has no capacity figure. Every member is an ORDINARY state, not
 * an error, and the wording lives in `CAPACITY_GAP_REASON` so the card and the
 * chart notes cannot describe the same gap two ways.
 */
export type CapacityGap = "no_usage_yet" | "no_measured_tokens" | "not_reported";

/**
 * The reason, in the fewest words that still say which signal is missing.
 *
 * Scoped to the FIELD, per §11 — "not measured" across a facet that is
 * reporting a perfectly good percentage would be a claim about the whole
 * window rather than about its capacity.
 */
export const CAPACITY_GAP_REASON: Record<CapacityGap, string> = {
  // The dominant real case: two of this host's four windows sit at 0%. There
  // is nothing to extrapolate from zero, however many tokens were measured.
  no_usage_yet: "not enough signal — nothing used yet",
  no_measured_tokens: "not enough signal — no tokens measured in this window",
  not_reported: "not enough signal — no capacity reported",
};

export interface FacetCapacity {
  /** The window's whole allowance, in `unit`. */
  readonly total: number;
  readonly basis: CapacityBasis;
  /** `tokens` for a derived figure; whatever the provider counts for a
   * published one, and `null` when it counted without naming a unit. */
  readonly unit: string | null;
  /** The span this capacity covers, resolved the way the projection resolved
   * it. `null` when nobody — vendor or operator — said how long the window is. */
  readonly windowSeconds: number | null;
  /** The same allowance normalized to 24h, or `null` with no resolvable span.
   * This is the number a daily chart can draw a line at. */
  readonly perDay: number | null;
}

/** A capacity, or the reason there is not one. A union rather than a nullable
 * plus a second lookup, so a caller cannot pair a figure with a gap. */
export type CapacityReading =
  | { readonly known: true; readonly capacity: FacetCapacity }
  | { readonly known: false; readonly gap: CapacityGap };

/**
 * One window's capacity, or which signal is missing.
 *
 * Ordered by how much the answer is worth: a published limit is the provider's
 * own word and beats an inversion; an inversion beats nothing. The gap branch
 * asks `used_percent` FIRST because zero-used is the ordinary reason and it is
 * the one a reader can act on ("come back when you have spent something"),
 * where the missing-tokens reason points at Grove's own index instead.
 *
 * **The duration is the projection's RESOLVED span, never the wire's
 * `window_seconds`.** Claude publishes no duration at all — all three of its
 * live windows read `window_seconds: null` — and is projected against the
 * operator's `usage.quota.window_seconds` assertion, which only the projection
 * knows. Falling back to the wire field keeps a provider that does publish one
 * (Codex) working when it reports no projection.
 */
export function capacityFor(window: SubscriptionWindowView): CapacityReading {
  const projection = window.projection;
  const seconds = windowSeconds(window);

  if (typeof window.limit === "number" && window.limit > 0) {
    return {
      known: true,
      capacity: {
        total: window.limit,
        basis: "published",
        unit: window.unit ?? null,
        windowSeconds: seconds,
        perDay: perDay(window.limit, seconds),
      },
    };
  }

  const estimate = projection?.tokens_available_estimate;
  if (typeof estimate === "number" && estimate > 0) {
    return {
      known: true,
      capacity: {
        total: estimate,
        basis: "derived",
        unit: "tokens",
        windowSeconds: seconds,
        perDay: perDay(estimate, seconds),
      },
    };
  }

  return { known: false, gap: capacityGap(window, projection) };
}

function capacityGap(
  window: SubscriptionWindowView,
  projection: SubscriptionWindowProjection | null | undefined,
): CapacityGap {
  const used = usedPercent(window);
  if (used !== null && used <= 0) return "no_usage_yet";
  if (typeof projection?.tokens_used !== "number") return "no_measured_tokens";
  return "not_reported";
}

/** The window's own duration, resolved the way its projection resolved it. */
export function windowSeconds(window: SubscriptionWindowView): number | null {
  const seconds = window.projection?.resolved_window_seconds ?? window.window_seconds;
  return typeof seconds === "number" && seconds > 0 ? seconds : null;
}

/** What the provider says is used, whichever half of the pair it reported.
 * `remaining_percent` is the same statement inverted, so reading it is not a
 * derivation — it is the other half of one reported fact. */
function usedPercent(window: SubscriptionWindowView): number | null {
  if (typeof window.used_percent === "number") return window.used_percent;
  if (typeof window.remaining_percent === "number") return 100 - window.remaining_percent;
  return null;
}

function perDay(total: number, seconds: number | null): number | null {
  return seconds === null ? null : (total * SECONDS_PER_DAY) / seconds;
}

/* ───────────────────────── the chart's ceiling lines ────────────────────── */

/** One facet's daily ceiling, carrying enough identity for the chart to label
 * it and for a note beneath the chart to name whose limit it is. */
export interface FacetCeiling {
  readonly accountId: string;
  readonly accountLabel: string;
  readonly facetLabel: string;
  /** What the line prints. The facet's own name while every drawn limit
   * belongs to ONE account, prefixed by the account as soon as they do not.
   *
   * The trigger is the SET spanning two accounts rather than two accounts
   * sharing a facet name, because the label is the only attribution a reader
   * gets on the `model` grouping: there are no per-account bars there to
   * borrow a colour from, so every line draws neutral and `Weekly all` alone
   * would not say whose. */
  readonly label: string;
  readonly perDay: number;
  readonly basis: CapacityBasis;
}

export interface FacetCeilings {
  readonly ceilings: readonly FacetCeiling[];
  /** Every (account, window) pair that HAD quota to report, drawn or not — the
   * denominator for "N of M limits". A facet at 0% used is a candidate that
   * cannot contribute, which is the ordinary partial case rather than an
   * error. */
  readonly candidates: number;
}

/**
 * Every facet's ceiling, across every configured profile.
 *
 * **One line per FACET, never one per account.** An account's facets are
 * independent limits over the same spend — a five-hour session budget, a
 * weekly total, a weekly per-model sub-limit — so collapsing them to the
 * longest one (which is what a per-account ceiling did) silently drops the
 * limits a reader is most likely to hit first. They are also not parts of a
 * whole and must never be summed.
 *
 * **A published limit in a unit that is not tokens draws no line**, because a
 * credit ceiling over a token bar is a comparison nobody can make. It still
 * renders on the card, where it stands on its own.
 */
export function facetCeilings(
  accounts: readonly BillingAccountView[] | undefined,
): FacetCeilings | null {
  const candidates = ceilingCandidates(accounts);
  if (candidates.length === 0) return null;

  const drawn = candidates.flatMap(({ account, window }) => {
    const reading = capacityFor(window);
    if (!reading.known) return [];
    const { perDay: value, basis, unit } = reading.capacity;
    if (value === null || value <= 0) return [];
    if (basis === "published" && unit !== "tokens") return [];
    return [
      {
        accountId: account.account_id,
        accountLabel: account.label,
        facetLabel: humanize(window.label),
        perDay: value,
        basis,
      },
    ];
  });

  // Disambiguation is a property of the SET, so it is resolved once here
  // rather than at a call site that can only see one line at a time.
  const spansAccounts = new Set(drawn.map((line) => line.accountId)).size > 1;

  return {
    ceilings: drawn.map((line) => ({
      ...line,
      label: spansAccounts ? `${line.accountLabel} ${line.facetLabel}` : line.facetLabel,
    })),
    candidates: candidates.length,
  };
}

/** Every (account, window) pair worth counting. A probe that never ran (no
 * windows) or plainly failed is not a gap in coverage — it is not a candidate
 * at all, and counting it would make the denominator report a failure as a
 * missing limit. */
function ceilingCandidates(
  accounts: readonly BillingAccountView[] | undefined,
): readonly { account: BillingAccountView; window: SubscriptionWindowView }[] {
  return (accounts ?? [])
    .filter((account) => account.status === "ok" || account.status === "stale")
    .flatMap((account) => account.windows.map((window) => ({ account, window })));
}

/**
 * How much headroom the y-axis keeps above whichever is taller — the tallest
 * day or the highest ceiling. Enough that a line at the very top is not drawn
 * on the plot's own edge, small enough not to compress the bars for nothing.
 */
const DOMAIN_HEADROOM = 1.05;

/**
 * The y-axis top, so a ceiling is COMPARED against rather than clipped away.
 *
 * This is the point of drawing the lines at all: a bar you cannot see beside
 * its own limit answers nothing. recharts' `ifOverflow="extendDomain"` moves
 * the domain per line and leaves the axis ticks to be re-derived from it, so
 * stating the top once is both cheaper and the only way the ceilings and the
 * bars are guaranteed to share a scale.
 *
 * `null` means "let recharts fit the bars", which is the honest answer when no
 * ceiling resolved — inventing a top from nothing would rescale the chart
 * against a limit that was never measured.
 */
export function seriesDomainMax(
  measuredMax: number | null,
  ceilings: readonly { readonly perDay: number }[],
): number | null {
  const highest = ceilings.reduce((max, line) => Math.max(max, line.perDay), 0);
  if (highest <= 0) return null;
  return Math.max(measuredMax ?? 0, highest) * DOMAIN_HEADROOM;
}
