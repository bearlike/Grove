import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageQuota } from "@/components/grove/usage/quota";
import { WindowDetailRows } from "@/components/grove/usage/window-meter";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { SubscriptionWindowView, UsageQuotasView } from "@/lib/grove/api";

/**
 * The regression the whole rebuild started from: every live window read
 * "Not measured".
 *
 * These are the daemon's REAL shapes, copied from `GET /usage/quotas` on a host
 * with both providers configured — a subscription window reports percentages
 * and leaves `used`/`limit`/`unit` null, because there is no unit to count. A
 * fixture that filled those three in would have passed against the broken gate
 * too, which is exactly why the bug survived.
 *
 * Static markup rather than a DOM: these components take data as props and hold
 * no state, so SSR output is the whole contract and the suite stays on `node`.
 */
const LIVE: UsageQuotasView = {
  accounts: [
    {
      account_id: "claude_code-000000000000",
      provider: "claude_code",
      label: ".claude",
      billing_mode: "subscription",
      subscription: { plan: "max", label: "max", detail: "20x" },
      status: "ok",
      detail: null,
      windows: [
        {
          scope: "session",
          label: "session",
          window_seconds: null,
          used_percent: 1.0,
          remaining_percent: 99.0,
          resets_at: "2026-08-10T17:29:59Z",
          limit: null,
          used: null,
          unit: null,
          observed_at: "2026-08-10T16:40:32Z",
          evidence: "provider_endpoint",
          // Claude reports no window duration, so there is no start and nothing
          // to project against. This is the ordinary case on that provider, not
          // a degraded one — see the `unknown` tests below.
          projection: {
            elapsed_percent: null,
            resolved_window_seconds: 18_000,
            burn_rate: null,
            projected_percent: null,
            verdict: "unknown",
            exhausts_at: null,
            tokens_used: null,
            tokens_available_estimate: null,
          },
        },
        {
          scope: "weekly",
          label: "weekly_all",
          window_seconds: null,
          used_percent: 34.0,
          remaining_percent: 66.0,
          resets_at: "2026-08-15T10:59:59Z",
          limit: null,
          used: null,
          unit: null,
          observed_at: "2026-08-10T16:40:32Z",
          evidence: "provider_endpoint",
          projection: {
            elapsed_percent: null,
            resolved_window_seconds: 604_800,
            burn_rate: null,
            projected_percent: null,
            verdict: "unknown",
            exhausts_at: null,
            // Measured evidence rides even where the pace is withheld: a
            // measurement is not an extrapolation and does not share its gate.
            tokens_used: 12_600_000,
            tokens_available_estimate: 37_058_824,
          },
        },
      ],
      spend: null,
      observed_at: "2026-08-10T16:40:32Z",
      stale_seconds: null,
    },
  ],
  coverage: {
    sources: [],
    degraded_source_count: 0,
    cost_available: false,
    quota_available: true,
  },
};

function render(quotas: UsageQuotasView | undefined, failed = false): string {
  return renderToStaticMarkup(
    <TooltipProvider>
      <UsageQuota quotas={quotas} failed={failed} />
    </TooltipProvider>,
  );
}

describe("UsageQuota", () => {
  it("renders a meter for a percentage-only window instead of 'not measured'", () => {
    const html = render(LIVE);
    // The percentage is now a LABELLED figure rather than a clause in a
    // sentence — the `Used` field and its value, not "34% used".
    expect(html).toContain(">Used</dt>");
    expect(html).toContain("34%");
    expect(html).toContain("66% left");
    expect(html).not.toContain("not measured — neither a percentage nor a count");
  });

  it("renders one compact block per account: a track and a bullet per window", () => {
    // The account holds two windows (session, weekly_all) — one subscription,
    // ONE block, not two stacked full-height meters. Both the scan row (the
    // track) and the descriptive row (the bullet) carry every facet.
    const html = render(LIVE);
    expect(html.match(/data-testid="usage-quota-track"/g)).toHaveLength(2);
    expect(html.match(/data-testid="usage-quota-facet"/g)).toHaveLength(2);
  });

  it("drives the meter from used_percent", () => {
    // Radix Progress reports its own value, so the bar and the words cannot
    // disagree — a meter drawn at 0 beside the text "34% used" is the failure
    // this pins.
    expect(render(LIVE)).toContain('aria-valuenow="34"');
  });

  it("falls back to used/limit/unit for a provider that counts instead", () => {
    const counted: UsageQuotasView = {
      ...LIVE,
      accounts: [
        {
          ...LIVE.accounts[0]!,
          windows: [
            {
              ...LIVE.accounts[0]!.windows[0]!,
              used_percent: null,
              remaining_percent: null,
              used: 12_500,
              limit: 50_000,
              unit: "credits",
            },
          ],
        },
      ],
    };
    const html = render(counted);
    expect(html).toContain("12.5K");
    expect(html).toContain("50.0K");
    expect(html).toContain("credits");
    expect(html).toContain('aria-valuenow="25"');
  });

  it("says so ONLY when both representations are absent", () => {
    const blank: UsageQuotasView = {
      ...LIVE,
      accounts: [
        {
          ...LIVE.accounts[0]!,
          windows: [
            {
              ...LIVE.accounts[0]!.windows[0]!,
              used_percent: null,
              remaining_percent: null,
            },
          ],
        },
      ],
    };
    expect(render(blank)).toContain("not measured — neither a percentage nor a count");
  });

  describe("the track colour language", () => {
    it("gives each facet its own chart stop, in order", () => {
      // Identity colour (§4.1), not state: the first facet is `--chart-1`,
      // the second `--chart-2` — never a hue chosen by what the window is
      // doing right now.
      const html = render(LIVE);
      expect(html).toContain("--color-primary:var(--chart-1)");
      expect(html).toContain("--color-primary:var(--chart-2)");
    });

    it("carries the same colour into the bullet's swatch and the tooltip header", () => {
      // Three surfaces, one vocabulary: the track, the label beside it in the
      // bulleted list, and the detail tooltip all draw from the same
      // `--swatch`/`--color-primary` reference for a given facet.
      const html = render(LIVE);
      expect(html.match(/--swatch:var\(--chart-1\)/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    });

    it("stops handing out colour after the fifth facet rather than repeating one", () => {
      // A sixth facet would otherwise reuse `--chart-1`'s hue, which claims an
      // identity it does not share with the first facet. Past the palette the
      // marker degrades to a plain "·" and the bar keeps the vendored default
      // — still fully legible by its word label, never miscoded.
      const many: UsageQuotasView = {
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            windows: Array.from({ length: 6 }, (_, index) => ({
              ...LIVE.accounts[0]!.windows[0]!,
              scope: "other" as const,
              label: `facet_${index}`,
            })),
          },
        ],
      };
      const html = render(many);
      expect(html).toContain("--color-primary:var(--chart-5)");
      expect(html).not.toContain("--color-primary:var(--chart-6)");
      // Six tracks still render — the sixth just carries no chart colour.
      expect(html.match(/data-testid="usage-quota-track"/g)).toHaveLength(6);
    });
  });

  it("leads each card with the provider's vendored mark", () => {
    const html = render({
      ...LIVE,
      accounts: [
        LIVE.accounts[0]!,
        { ...LIVE.accounts[0]!, account_id: "codex-0", provider: "codex", label: ".codex" },
      ],
    });
    expect(html).toContain('data-brand="claude"');
    expect(html).toContain('data-brand="codex"');
  });

  describe("when the provider rate-limits the probe", () => {
    // The live failure shape: status flips and `detail` explains, while the
    // windows read minutes earlier are still the best answer anyone has.
    const throttled: UsageQuotasView = {
      ...LIVE,
      accounts: [
        {
          ...LIVE.accounts[0]!,
          status: "rate_limited",
          detail: "the usage endpoint is rate-limiting this account; retrying later",
        },
      ],
    };

    it("keeps the last-known windows on screen instead of blanking the card", () => {
      const html = render(throttled);
      expect(html).toContain("34% used");
      expect(html.match(/data-testid="usage-quota-facet"/g)).toHaveLength(2);
    });

    it("dates them, so a stale reading is never mistaken for a live one", () => {
      // The age is a `RelativeTime`, which is mount-gated: SSR paints the
      // absolute instant and the browser swaps in "3m ago". Pin the element and
      // its machine-readable instant, not either rendering.
      const html = render(throttled);
      expect(html).toContain('data-testid="relative-time"');
      expect(html).toContain('dateTime="2026-08-10T16:40:32Z"');
    });

    it("puts the failure BELOW the numbers, as a footnote", () => {
      // Above them it reads as though the plan had failed, rather than the probe.
      const html = render(throttled);
      expect(html.indexOf("rate-limiting")).toBeGreaterThan(html.indexOf("34% used"));
      expect(html).toContain("Last refresh:");
    });

    it("says what is actually unknown when no earlier reading survived", () => {
      const html = render({
        ...throttled,
        accounts: [{ ...throttled.accounts[0]!, windows: [] }],
      });
      expect(html).toContain("No earlier reading survived this failure");
      expect(html).not.toContain("This provider reports no window");
    });
  });

  describe("the subscription tier", () => {
    it("names the plan beside the account it belongs to", () => {
      // Verbatim — `label` is the provider's own wording and Grove owns no
      // vocabulary of vendor plan names to translate it into. Pinned as the
      // whole line, because WHERE it lands is half the requirement, and because
      // the plan STANDS IN FOR the billing mode rather than joining it: `max
      // 20x` entails `Subscription`, and printing both wrapped the line.
      expect(render(LIVE)).toContain("Claude code · max 20x");
      expect(render(LIVE)).not.toContain("· Subscription");
    });

    it("puts the identity in a slot that yields rather than a line that grows", () => {
      // A long plan name must truncate, not wrap. Structure, not length: the
      // identity cell is `min-w-0 truncate` inside a two-column grid and carries
      // the full value in `title`, so nothing can bleed out of the card.
      const long = "Enterprise Premium Unlimited Founders Edition";
      const html = render({
        ...LIVE,
        accounts: [
          { ...LIVE.accounts[0]!, subscription: { plan: null, label: long, detail: "500x" } },
        ],
      });
      expect(html).toContain(`title="Claude code · ${long} 500x"`);
      expect(html).toMatch(/class="truncate"[^>]*>Claude code · Enterprise/);
    });

    it("renders NOTHING when Grove could not tell", () => {
      const html = render({
        ...LIVE,
        accounts: [{ ...LIVE.accounts[0]!, subscription: null }],
      });
      // No chip, no placeholder, no "Unknown plan": this is the one field a
      // person reconciles against their own bill, so a fabricated tier is worse
      // than an absent one. The billing mode is what the line falls back to.
      expect(html).toContain("Claude code · Subscription");
      expect(html).not.toContain("20x");
      expect(html.toLowerCase()).not.toContain("unknown plan");
    });

    it("falls back to the slug when the provider gave no display string", () => {
      expect(
        render({
          ...LIVE,
          accounts: [
            { ...LIVE.accounts[0]!, subscription: { plan: "prolite", label: null, detail: null } },
          ],
        }),
      ).toContain("prolite");
    });
  });

  describe("the burn-rate projection", () => {
    /** A Codex-shaped window: it reports `window_seconds`, so it can be judged. */
    function projected(projection: NonNullable<SubscriptionWindowView["projection"]>): string {
      return render({
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            windows: [{ ...LIVE.accounts[0]!.windows[1]!, projection }],
          },
        ],
      });
    }

    const JUDGED: NonNullable<SubscriptionWindowView["projection"]> = {
      elapsed_percent: 28.5,
      burn_rate: 0.876,
      projected_percent: 87.6,
      verdict: "tight",
      exhausts_at: null,
      tokens_used: null,
      tokens_available_estimate: null,
    };

    it("marks a healthy pace QUIETLY — the common case must not shout", () => {
      const html = projected({ ...JUDGED, verdict: "on_track", projected_percent: 41.2 });
      expect(html).toContain("On track");
      // The figure and its qualifier are separate elements now — the figure so
      // it can carry the derived mark, the qualifier so it can step down a
      // tier and wrap without dragging the number with it.
      expect(html).toContain(">41.2%<");
      expect(html).toContain("of the window at this rate");
      // `outline` is the hairline tone. A healthy window that carried a filled
      // badge would spend the page's whole colour budget saying "fine".
      expect(html).toContain('data-slot="badge"');
      expect(html).not.toContain("bg-destructive");
    });

    it("escalates through the tones the design system already defines", () => {
      expect(projected(JUDGED)).toContain("Nearing limit");
      // NOT "Over": a customer reads that as "you have already gone over", which
      // is the opposite of a forecast about where this pace lands by reset.
      expect(projected({ ...JUDGED, verdict: "over", projected_percent: 143.0 })).toContain(
        "Projected over",
      );
      expect(projected({ ...JUDGED, verdict: "over" })).toContain("bg-destructive");
    });

    it("phrases exhaustion as a FORECAST, never as something already done", () => {
      const html = projected({
        ...JUDGED,
        verdict: "over",
        exhausts_at: "2026-08-14T09:00:00Z",
      });
      // `limit reached <date>` was read as "the limit HAS been reached". It is a
      // projection of where the current rate lands, and the words have to say so.
      expect(html).toContain("forecast to reach the limit");
      expect(html).not.toContain("limit reached");

      // A window with headroom leaves `exhausts_at` null, and a date nobody
      // reaches would read as the moment the account runs out.
      expect(projected(JUDGED)).not.toContain("forecast to reach the limit");
    });

    it("gives the forecast a RELATIVE span once the browser clock exists", () => {
      // SSR has no clock, so the sentence degrades to the absolute instant —
      // the same absolute-then-relative contract `RelativeTime` keeps. The
      // relative half is `approxDurationUntil`, pinned in its own suite.
      const html = projected({
        ...JUDGED,
        verdict: "over",
        exhausts_at: "2026-08-14T09:00:00Z",
      });
      expect(html).toMatch(/forecast to reach the limit <\/span><span[^>]*>[A-Z]/);
    });

    describe("when the verdict is unknown", () => {
      // Every Claude window today: the provider reports no duration, so there
      // is no window start and nothing to extrapolate from.
      it("is a real answer, not a warning and not 0%", () => {
        const html = render(LIVE);
        // Label plus value, not a sentence: `Pace / not enough signal yet`.
        expect(html).toContain(">Pace</dt>");
        expect(html).toContain("not enough signal yet");
        expect(html).not.toContain("0% projected");
        // No badge renders for an unknown verdict — the facet still carries
        // `data-verdict="unknown"` for anyone probing state, but no chip.
        expect(html).toContain('data-verdict="unknown"');
        expect(html).not.toMatch(/data-verdict="unknown"[^>]*>[\s\S]{0,80}data-slot="badge"/);
      });

      it("still reports the tokens it DID measure", () => {
        // A measurement is not an extrapolation and does not share its gate.
        expect(render(LIVE)).toContain("12.6M");
        expect(render(LIVE)).toContain("37.1M");
      });

      it("splits the pair across the two rows that answer two questions", () => {
        // The old shape ran `12.6M used of ~37.1M estimated` as one clause.
        // Measured spend is evidence about the PRESENT and belongs on `Used`;
        // the allowance is Grove's estimate of the window's SIZE and belongs
        // on `Capacity`, where it can be marked as derived on its own.
        const html = render(LIVE);
        expect(html).toContain('data-testid="usage-quota-quantities"');
        expect(html).toContain("12.6M");
        expect(html).toContain('data-testid="usage-quota-capacity"');
        expect(html).toContain("~37.1M");
      });

      it("still says so on a window that measured nothing else either", () => {
        // Caught on the composed page, not by a unit test: with the pace
        // sentence omitted, a Claude facet sat silent beside a Codex facet
        // wearing a "Tight" badge — and silence next to a verdict reads as the
        // GOOD verdict. Every projected facet states its own answer.
        const rows = render(LIVE).match(/data-testid="usage-quota-facet-detail"/g);
        expect(rows).toHaveLength(2);
      });
    });

    it("reports an absent token count as absent, never as zero", () => {
      const html = projected({ ...JUDGED, tokens_used: null });
      expect(html).not.toContain("0 tokens");
      // Nothing was measured, so the evidence half of `Used` does not render.
      expect(html).not.toContain('data-testid="usage-quota-quantities"');
    });

    it("keeps the measured half when only the ESTIMATE is missing", () => {
      const html = projected({ ...JUDGED, tokens_used: 12_600_000 });
      expect(html).toContain("12.6M");
      // …and says which signal the capacity is missing, rather than blanking.
      expect(html).toContain('data-testid="usage-quota-capacity-gap"');
    });

    it("never pairs an estimate with a fabricated zero on the used row", () => {
      const html = projected({ ...JUDGED, tokens_used: null, tokens_available_estimate: 5_000 });
      expect(html).not.toContain("0 used");
      expect(html).not.toContain('data-testid="usage-quota-quantities"');
      // The capacity still stands on its own — it is a different question.
      expect(html).toContain("~5.0K");
    });

    it("keeps the secondary detail behind ONE affordance instead of on the card", () => {
      // The trigger is on the card; the body is portalled and only mounts while
      // open, so it is asserted through `WindowDetailRows` below.
      expect(projected(JUDGED)).toContain('data-testid="usage-quota-detail"');
    });

    it("carries no verdict marker at all for a window the daemon did not project", () => {
      const html = render({
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            windows: [{ ...LIVE.accounts[0]!.windows[1]!, projection: null }],
          },
        ],
      });
      expect(html).not.toContain("data-verdict=");
      expect(html).not.toContain(">Pace</dt>");
      expect(html).not.toContain("forecast to reach the limit");
    });
  });

  /**
   * The secondary detail: short term/value rows, never a paragraph. Rendered
   * directly because Radix mounts tooltip content only while open.
   */
  describe("the detail tooltip", () => {
    const WINDOW = LIVE.accounts[0]!.windows[1]!;

    function rows(quota: SubscriptionWindowView): string {
      return renderToStaticMarkup(
        <TooltipProvider>
          <WindowDetailRows
          quota={quota}
          meter={{ used: 34, basis: "reported", provenance: "Reported by the provider." }}
        />
        </TooltipProvider>,
      );
    }

    it("carries the exact reset instant the meter row only shows relatively", () => {
      const html = rows(WINDOW);
      expect(html).toContain("Resets");
      expect(html).toContain("Aug 15, 2026");
    });

    it("explains the pace in interpretable phrases, not raw ratios", () => {
      const html = rows({
        ...WINDOW,
        projection: { ...WINDOW.projection!, elapsed_percent: 28.5, burn_rate: 0.876 },
      });
      // `1.0×` is on pace to finish exactly at the limit, so naming the baseline
      // is what makes the ratio mean anything by itself.
      expect(html).toContain("0.88× the even pace");
      expect(html).toContain("28.5% of the window");
      expect(html).toContain("Used");
    });

    it("says whose arithmetic the allowance is — the one thing a reader cannot infer", () => {
      expect(rows(WINDOW)).toContain("Allowance is Grove’s estimate");
      // Labelled as an estimate on the row itself too, not only in the note
      // under it: the row is what a reader reads.
      expect(rows(WINDOW)).toContain("Allowance (est.)");
    });

    it("omits that note when there is no estimate to qualify", () => {
      const html = rows({
        ...WINDOW,
        projection: { ...WINDOW.projection!, tokens_available_estimate: null },
      });
      expect(html).not.toContain("Allowance is Grove’s estimate");
    });

    it("drops every row whose field the window did not report", () => {
      const html = rows({ ...WINDOW, resets_at: null, evidence: "unknown", projection: null });
      expect(html).not.toContain("Resets");
      expect(html).not.toContain("Burn rate");
      expect(html).not.toContain("Read from");
      expect(html).not.toContain("Forecast");
    });
  });

  it("distinguishes an unconfigured profile from a failed read", () => {
    const empty = render({ ...LIVE, accounts: [] });
    expect(empty).toContain("No subscription profile is configured");
    expect(render(undefined, true)).toContain("Quota could not be read");
  });

  /**
   * The supplementary tier — anything ABOUT the reading rather than more of
   * it (the facet insights below the tracks, the spend/refresh footer below
   * everything) — gets ONE enforced boundary, not a rule invented per block.
   * Both call sites import `SUPPLEMENTARY_RULE` from `window-meter.tsx` rather
   * than writing their own `border-t`, so this pins the class making it to the
   * DOM at both seams rather than the export existing but going unused at one
   * of them.
   */
  describe("the supplementary-tier boundary", () => {
    it("draws it between the tracks and the facet insights", () => {
      const html = render(LIVE);
      expect(html).toMatch(
        /class="[^"]*\bborder-t\b[^"]*\bborder-border\b[^"]*" data-testid="usage-quota-facets"/,
      );
    });

    it("draws the SAME rule again between the windows block and the account footer", () => {
      // LIVE's own account carries neither `spend` nor `detail`, so this
      // needs a fixture that actually has something to footnote.
      const html = render({
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            spend: { amount: "12.34", currency: "USD", provenance: "actual" },
          },
        ],
      });
      expect(html).toMatch(
        /class="[^"]*\bborder-t\b[^"]*\bborder-border\b[^"]*" data-testid="usage-quota-footer"/,
      );
    });

    it("omits the footer boundary entirely when there is nothing to footnote", () => {
      // LIVE's account already carries neither `spend` nor `detail` — a rule
      // with nothing under it would draw a line for its own sake.
      expect(render(LIVE)).not.toContain('data-testid="usage-quota-footer"');
    });
  });

  /**
   * The card's structure, which is what the complaint was actually about: five
   * independent facts joined into one tertiary sentence, in a 366px column,
   * with no entry point and nothing to scan down.
   */
  describe("hierarchy, not prose", () => {
    it("gives every figure a label instead of joining them into a sentence", () => {
      const html = render(LIVE);
      for (const field of ["Used", "Capacity", "Resets", "Pace"]) {
        expect(html).toContain(`>${field}</dt>`);
      }
      // The joined clause is gone: no facet prints `34% used · 66% left` as
      // one run of prose.
      expect(html).not.toContain("34% used ·");
    });

    it("shares ONE grid across every facet of an account, so values line up", () => {
      // Per-facet `dl`s would each size their own label column and the values
      // would step in and out by a few pixels down the card.
      const html = render(LIVE);
      expect(html.match(/data-testid="usage-quota-facet-detail"/g)).toHaveLength(2);
      expect(html).toContain("grid-cols-[auto_minmax(0,1fr)]");
    });

    it("drops only the rows the window did not report, never Used or Capacity", () => {
      // A window with no reset instant and no projection still answers the two
      // questions the card exists for — and answers them with a stated gap
      // rather than a blank.
      const html = render({
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            windows: [
              { ...LIVE.accounts[0]!.windows[1]!, resets_at: null, projection: null },
            ],
          },
        ],
      });
      expect(html).toContain(">Used</dt>");
      expect(html).toContain(">Capacity</dt>");
      expect(html).not.toContain(">Resets</dt>");
      expect(html).not.toContain(">Pace</dt>");
    });
  });

  /**
   * Which figures are the provider's word and which are Grove's arithmetic.
   *
   * On the live host exactly one figure per facet is published — the
   * percentage — and a reader reconciling against their own bill has no other
   * way to tell. The mark is a dashed rule, deliberately distinct from the
   * DOTTED rule `Explain` uses for a glossary term, which appears on the same
   * rows.
   */
  describe("derived figures are marked, reported ones are not", () => {
    it("leaves the provider's own percentage unmarked", () => {
      const html = render(LIVE);
      expect(html).toMatch(/<dd[^>]*><span>34%<\/span>/);
    });

    it("marks a percentage GROVE computed, on the same row shape", () => {
      // `100 − remaining` is Grove subtracting. A card that marked the two
      // identically would be claiming the vendor said something it did not.
      const html = render({
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            windows: [
              { ...LIVE.accounts[0]!.windows[1]!, used_percent: null, remaining_percent: 66 },
            ],
          },
        ],
      });
      expect(html).toMatch(/data-derived="true"[^>]*>34%</);
    });

    it("marks the capacity, the countdown and the pace", () => {
      const html = render(LIVE);
      expect(html).toMatch(/data-testid="usage-quota-resets"/);
      // Every one of the three carries the mark AND a title, because a text
      // decoration is a visual carrier and cannot be the only one (§4.7).
      const marks = html.match(/data-derived="true"/g) ?? [];
      expect(marks.length).toBeGreaterThanOrEqual(2);
      expect(html).toContain("the countdown is Grove");
      expect(html).toContain("Grove&#x27;s estimate");
    });

    it("uses a DASHED rule, never the glossary's dotted one", () => {
      const html = render(LIVE);
      expect(html).toContain("underline decoration-dashed underline-offset-2");
      // The glossary term is on the same row and must stay distinguishable.
      expect(html).toContain("decoration-dotted");
    });

    it("leaves a provider's own published limit unmarked", () => {
      const counted: UsageQuotasView = {
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            windows: [
              {
                ...LIVE.accounts[0]!.windows[0]!,
                used_percent: null,
                remaining_percent: null,
                used: 12_500,
                limit: 50_000,
                unit: "credits",
                projection: null,
              },
            ],
          },
        ],
      };
      const html = render(counted);
      expect(html).toContain('data-basis="published"');
      // No tilde: this is the provider's number, not an inversion of one.
      expect(html).not.toContain("~50.0K");
    });
  });

  /**
   * The capacity figure itself — the thing the card never showed.
   */
  describe("capacity", () => {
    it("states the estimate for a facet that can produce one", () => {
      const html = render(LIVE);
      expect(html).toContain('data-testid="usage-quota-capacity"');
      expect(html).toContain('data-basis="derived"');
      expect(html).toContain("~37.1M");
    });

    it("names the missing SIGNAL rather than blanking or printing a dash", () => {
      // The live host's ordinary case, and the one most likely to be "fixed"
      // into a fabrication.
      const html = render({
        ...LIVE,
        accounts: [
          {
            ...LIVE.accounts[0]!,
            windows: [
              {
                ...LIVE.accounts[0]!.windows[1]!,
                used_percent: 0,
                remaining_percent: 100,
                projection: {
                  ...LIVE.accounts[0]!.windows[1]!.projection!,
                  tokens_available_estimate: null,
                },
              },
            ],
          },
        ],
      });
      expect(html).toContain("not enough signal — nothing used yet");
      expect(html).not.toContain('data-testid="usage-quota-capacity"');
    });

    it("distinguishes 'nothing used yet' from 'nothing measured'", () => {
      // Two facets on one account fail for opposite reasons and a reader acts
      // on the difference: the first clears itself by spending, the second
      // points at Grove's own index.
      const html = render(LIVE);
      expect(html).toContain("not enough signal — no tokens measured in this window");
    });
  });

  it("gives the progress bar more of the card's height than the vendored default", () => {
    // Was `h-1.5`: thinner than the badge two rows below it, which is
    // backwards for the block's own primary measurement.
    const html = render(LIVE);
    expect(html).toMatch(/data-slot="progress"[^>]*class="[^"]*\bh-2\b/);
  });

  it("clusters the verdict badge with its own detail trigger at the row's trailing edge", () => {
    // The badge no longer sits wedged between the label and the scope; both
    // it and the (i) trigger live inside one `ml-auto` span so the row reads
    // as identity-left, status-right rather than five items competing for
    // the same line.
    const html = projectedFor(LIVE, "over");
    expect(html).toMatch(
      /<span class="ml-auto flex shrink-0 items-center gap-1\.5">[\s\S]*?data-slot="badge"[\s\S]*?data-testid="usage-quota-detail"[\s\S]*?<\/span>/,
    );
  });
});

/** Renders `LIVE` with its second window's verdict swapped, for a test that
 * only cares about the badge/detail cluster and not the pace wording. */
function projectedFor(base: UsageQuotasView, verdict: "over" | "tight" | "on_track"): string {
  return renderToStaticMarkup(
    <TooltipProvider>
      <UsageQuota
        quotas={{
          ...base,
          accounts: [
            {
              ...base.accounts[0]!,
              windows: [
                {
                  ...base.accounts[0]!.windows[1]!,
                  projection: { ...base.accounts[0]!.windows[1]!.projection!, verdict },
                },
              ],
            },
          ],
        }}
        failed={false}
      />
    </TooltipProvider>,
  );
}
