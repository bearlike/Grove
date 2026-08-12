import { describe, expect, it } from "vitest";

import {
  capacityFor,
  facetCeilings,
  seriesDomainMax,
  windowSeconds,
} from "@/components/grove/usage/tokens";
import type { BillingAccountView, SubscriptionWindowView } from "@/lib/grove/api";

/**
 * "How large is this window, in tokens?" — the one derivation the quota card
 * and the weekly-mix chart share.
 *
 * **The fixtures are the real payload, verbatim.** Every window below was
 * copied out of `GET /usage/quotas` against the running daemon on 2026-08-11,
 * with both providers configured, and it is the shape that matters rather than
 * the numbers: `used`/`limit`/`unit` are null on all four, `window_seconds` is
 * null on all three Claude windows, and two of the four sit at exactly 0% used.
 * A hand-written fixture would have filled at least one of those in and every
 * refusal below would have gone untested — which is precisely how the original
 * "every window reads Not measured" bug survived its own suite.
 */

function window(over: Partial<SubscriptionWindowView>): SubscriptionWindowView {
  return {
    scope: "weekly",
    label: "weekly_all",
    window_seconds: null,
    used_percent: null,
    remaining_percent: null,
    resets_at: null,
    limit: null,
    used: null,
    unit: null,
    observed_at: null,
    evidence: "provider_endpoint",
    projection: null,
    ...over,
  };
}

/** Claude's weekly-all window: the ONE facet on this host that yields an
 * estimate. 5,207,807,096 measured at 79% used → 6,592,160,881. */
const WEEKLY_ALL = window({
  scope: "weekly",
  label: "weekly_all",
  used_percent: 79,
  remaining_percent: 21,
  projection: {
    elapsed_percent: 46.827196253472216,
    resolved_window_seconds: 604_800,
    burn_rate: 1.6870538131810995,
    projected_percent: 168.70538131810994,
    verdict: "over",
    exhausts_at: "2026-08-12T14:34:54.974993Z",
    tokens_used: 5_207_807_096,
    tokens_available_estimate: 6_592_160_881,
  },
});

/** Claude's session window: a live percentage, and NO measured tokens, because
 * the index's newest event predates the five-hour window's start. */
const SESSION = window({
  scope: "session",
  label: "session",
  used_percent: 11,
  remaining_percent: 89,
  projection: {
    elapsed_percent: 76.72401472777779,
    resolved_window_seconds: 18_000,
    burn_rate: 0.14337101674135244,
    projected_percent: 14.337101674135244,
    verdict: "on_track",
    exhausts_at: null,
    tokens_used: null,
    tokens_available_estimate: null,
  },
});

/** Claude's per-model weekly sub-limit: 5.2 BILLION measured tokens inside its
 * bounds and 0% used, so the inversion divides by zero and refuses. */
const WEEKLY_SCOPED = window({
  scope: "model",
  label: "weekly_scoped",
  used_percent: 0,
  remaining_percent: 100,
  projection: {
    elapsed_percent: 46.82726908465609,
    resolved_window_seconds: 604_800,
    burn_rate: 0,
    projected_percent: 0,
    verdict: "on_track",
    exhausts_at: null,
    tokens_used: 5_207_807_096,
    tokens_available_estimate: null,
  },
});

/** Codex's weekly window — the one provider that publishes its own duration. */
const CODEX_WEEKLY = window({
  scope: "weekly",
  label: "primary",
  window_seconds: 604_800,
  used_percent: 0,
  remaining_percent: 100,
  evidence: "rollout",
  projection: {
    elapsed_percent: 8.379887768353173,
    resolved_window_seconds: 604_800,
    burn_rate: null,
    projected_percent: null,
    verdict: "unknown",
    exhausts_at: null,
    tokens_used: 209_322,
    tokens_available_estimate: null,
  },
});

function account(
  windows: SubscriptionWindowView[],
  over: Partial<BillingAccountView> = {},
): BillingAccountView {
  return {
    account_id: "claude_code-e4c72aa405bb",
    provider: "claude_code",
    label: ".claude",
    billing_mode: "subscription",
    status: "ok",
    windows,
    ...over,
  } as BillingAccountView;
}

describe("one window's capacity", () => {
  it("takes the daemon's inversion of the reported percentage, and calls it derived", () => {
    const reading = capacityFor(WEEKLY_ALL);
    expect(reading.known).toBe(true);
    if (!reading.known) return;
    expect(reading.capacity.total).toBe(6_592_160_881);
    expect(reading.capacity.basis).toBe("derived");
    expect(reading.capacity.unit).toBe("tokens");
    // 6.59B over a 7-day window is 941.7M a day.
    expect(reading.capacity.perDay).toBeCloseTo(941_737_268.71, 0);
  });

  it("refuses at 0% used, however many tokens were measured", () => {
    // The dominant real case, and the one most likely to be "fixed" into a
    // fabrication: `weekly_scoped` carries 5.2 BILLION measured tokens and
    // still cannot state a capacity, because there is nothing to extrapolate
    // from zero. Dividing by 0 yields Infinity, which renders as a number.
    const reading = capacityFor(WEEKLY_SCOPED);
    expect(reading).toEqual({ known: false, gap: "no_usage_yet" });
  });

  it("names the MISSING SIGNAL rather than one blanket refusal", () => {
    // Two facets on one account fail for opposite reasons, and a reader acts
    // on the difference: 0% used clears itself by spending, no measured tokens
    // points at Grove's own index instead.
    expect(capacityFor(SESSION)).toEqual({ known: false, gap: "no_measured_tokens" });
    expect(capacityFor(CODEX_WEEKLY)).toEqual({ known: false, gap: "no_usage_yet" });
  });

  it("prefers a provider's own published limit over any inversion", () => {
    const counted = window({
      used: 12_500,
      limit: 50_000,
      unit: "credits",
      window_seconds: 86_400,
      projection: { verdict: "unknown", tokens_available_estimate: 999 },
    });
    const reading = capacityFor(counted);
    expect(reading.known).toBe(true);
    if (!reading.known) return;
    expect(reading.capacity).toMatchObject({
      total: 50_000,
      basis: "published",
      unit: "credits",
      perDay: 50_000,
    });
  });

  it("keeps the capacity but drops the per-day figure with no resolvable duration", () => {
    // A window nobody timed still HAS a size; it just cannot be normalized, so
    // the card prints it and the chart draws nothing.
    const untimed = window({
      used_percent: 50,
      projection: {
        verdict: "unknown",
        resolved_window_seconds: null,
        tokens_used: 100,
        tokens_available_estimate: 200,
      },
    });
    const reading = capacityFor(untimed);
    expect(reading.known).toBe(true);
    if (!reading.known) return;
    expect(reading.capacity.total).toBe(200);
    expect(reading.capacity.perDay).toBeNull();
  });

  it("reads the RESOLVED duration, never the provider's own field", () => {
    // Claude publishes no duration at all — all three live windows report
    // `window_seconds: null` — and is projected against the operator's
    // `usage.quota.window_seconds` assertion, which only the projection knows.
    // Normalizing off the wire field would draw no line for that whole
    // provider.
    expect(WEEKLY_ALL.window_seconds).toBeNull();
    expect(windowSeconds(WEEKLY_ALL)).toBe(604_800);
    // And the wire field still answers for a provider that reports no
    // projection at all.
    expect(windowSeconds(window({ window_seconds: 3_600, projection: null }))).toBe(3_600);
  });

  it("treats a zero or negative duration as no duration", () => {
    expect(windowSeconds(window({ window_seconds: 0 }))).toBeNull();
  });
});

describe("every facet's ceiling, across every profile", () => {
  it("draws one line per FACET, not one per account", () => {
    // The behaviour this replaced collapsed an account to its longest window,
    // which silently dropped the session budget a reader hits first.
    const result = facetCeilings([
      account([
        window({
          label: "session",
          used_percent: 50,
          projection: {
            verdict: "unknown",
            resolved_window_seconds: 18_000,
            tokens_used: 50,
            tokens_available_estimate: 100,
          },
        }),
        window({
          label: "weekly_all",
          used_percent: 50,
          projection: {
            verdict: "unknown",
            resolved_window_seconds: 604_800,
            tokens_used: 350,
            tokens_available_estimate: 700,
          },
        }),
      ]),
    ]);
    expect(result?.ceilings.map((line) => line.label)).toEqual(["Session", "Weekly all"]);
    // 100 over 5h is 480/day; 700 over a week is 100/day. Never summed — these
    // are independent limits over the same spend, not parts of a whole.
    expect(result?.ceilings.map((line) => Math.round(line.perDay))).toEqual([480, 100]);
  });

  it("counts every candidate facet in the denominator, drawn or not", () => {
    // The live host exactly: four windows across two accounts, one of which
    // can state a capacity. A denominator of 2 (accounts) would understate how
    // much of the picture is missing.
    const result = facetCeilings([
      account([SESSION, WEEKLY_ALL, WEEKLY_SCOPED]),
      account([CODEX_WEEKLY], { account_id: "codex-7d8bc84c9eaa", provider: "codex" }),
    ]);
    expect(result?.candidates).toBe(4);
    expect(result?.ceilings).toHaveLength(1);
    expect(result?.ceilings[0]?.facetLabel).toBe("Weekly all");
  });

  it("skips an account whose probe failed rather than counting its facets uncovered", () => {
    // A failed probe never had anything to say, so it is not a gap in
    // coverage — it is absent from the denominator entirely.
    const result = facetCeilings([
      account([WEEKLY_ALL]),
      account([SESSION], { account_id: "b", status: "auth_expired" }),
    ]);
    expect(result?.candidates).toBe(1);
  });

  it("keeps a stale account, because its last-good windows are still the answer", () => {
    expect(facetCeilings([account([WEEKLY_ALL], { status: "stale" })])?.ceilings).toHaveLength(1);
  });

  it("prefixes the account as soon as the drawn set spans two profiles", () => {
    // Not "as soon as two facet names collide": on the `model` grouping there
    // are no per-account bars to borrow a colour from, so every line draws
    // neutral and the label is the ONLY attribution a reader gets. Two lines
    // reading `Weekly all` and `Session` would each be unattributable even
    // though neither name repeats.
    const spanning = facetCeilings([
      account([WEEKLY_ALL], { account_id: "a", label: "Personal" }),
      account([{ ...WEEKLY_ALL, label: "session" }], { account_id: "b", label: "Work" }),
    ]);
    expect(spanning?.ceilings.map((line) => line.label)).toEqual([
      "Personal Weekly all",
      "Work Session",
    ]);
    // One account: the prefix would be the same word on every row.
    expect(
      facetCeilings([account([WEEKLY_ALL, { ...WEEKLY_ALL, label: "session" }])])?.ceilings.map(
        (line) => line.label,
      ),
    ).toEqual(["Weekly all", "Session"]);
  });

  it("draws no line for a published limit counted in something other than tokens", () => {
    // A credit ceiling over a token bar is a comparison nobody can make. It
    // still renders on the card, where it stands on its own.
    const result = facetCeilings([
      account([window({ used: 1, limit: 50_000, unit: "credits", window_seconds: 86_400 })]),
    ]);
    expect(result?.ceilings).toHaveLength(0);
    expect(result?.candidates).toBe(1);
  });

  it("is absent when no account carries a quota window at all", () => {
    expect(facetCeilings([])).toBeNull();
    expect(facetCeilings(undefined)).toBeNull();
    // An account with no windows contributes no CANDIDATE either — a probe
    // that returned nothing is not a limit Grove failed to draw, so the notes
    // must not report "0 of 1 limits" and imply one was lost.
    expect(facetCeilings([account([])])).toBeNull();
  });
});

describe("the y-axis top", () => {
  it("clears the highest ceiling, so a limit is compared against and not clipped", () => {
    // The whole point of drawing the lines: bars of 5M beside a 940M limit
    // must both be on the plot, or the comparison the card exists for is
    // off-screen.
    const top = seriesDomainMax(5_000_000, [{ perDay: 941_737_268 }]);
    expect(top).not.toBeNull();
    expect(top!).toBeGreaterThan(941_737_268);
  });

  it("clears the tallest STACK when the bars outgrow every limit", () => {
    expect(seriesDomainMax(2_000, [{ perDay: 100 }])).toBeGreaterThan(2_000);
  });

  it("hands the axis back to recharts when no ceiling resolved", () => {
    // Inventing a top from nothing would rescale the chart against a limit
    // that was never measured.
    expect(seriesDomainMax(5_000, [])).toBeNull();
    expect(seriesDomainMax(null, [])).toBeNull();
  });
});
