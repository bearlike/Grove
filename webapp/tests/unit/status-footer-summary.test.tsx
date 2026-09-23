import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusFooterSummary } from "@/components/grove/shell/status-footer-summary";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AccountSummary, FleetCounts, FooterContext, SystemFacts } from "@/lib/grove/adapters/footer";

/**
 * What the NARROW band renders (#815).
 *
 * The defect this pins was measured on a 390px phone: the band's complete text
 * was `1 0 1 Claude max 20x 7d 100% …`, because every session label sits behind
 * `lg:inline` and both account summaries stay inline at every width. Bare
 * coloured digits carry their meaning in hue alone, which §4.7 forbids, and the
 * account names truncated to `Clau…` — the reading survived and the thing it
 * was a reading OF did not.
 *
 * A CLOSED Radix sheet mounts no content, so these assertions are about the
 * STRIP. The sheet's completeness is the adapter's question and a browser's.
 */

const context: FooterContext = {
  project: "Grove",
  subpath: "PA",
  branch: "feat/footer",
  worktree: "footer-ui",
};

const accounts: AccountSummary[] = [
  {
    accountId: "a", label: "someone@example.com", shortLabel: "Claude max 20x",
    provider: "claude_code", percent: 8, window: "7d", status: "ok", stale: false,
  },
  {
    accountId: "b", label: "other@example.com", shortLabel: "Claude max 5x",
    provider: "claude_code", percent: 100, window: "7d", status: "ok", stale: false,
  },
];

const system: SystemFacts = {
  version: "0.0.9",
  startedAt: "2026-09-18T08:00:00Z",
  updateAvailable: false,
  latestVersion: null,
  restartRequired: false,
};

function render(
  counts: FleetCounts,
  opts: {
    accounts?: AccountSummary[];
    connected?: boolean;
    context?: FooterContext;
  } = {},
): string {
  return renderToStaticMarkup(
    <TooltipProvider>
      <StatusFooterSummary
        context={opts.context ?? context}
        counts={counts}
        accounts={opts.accounts ?? accounts}
        system={system}
        connected={opts.connected ?? true}
      />
    </TooltipProvider>,
  );
}

/** The strip's own text, tags stripped — what a reader actually sees. */
function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

describe("every value keeps its word", () => {
  it("never renders a bare count, which is the reported defect", () => {
    // The literal failure: `1 0 1` beside three glyphs. A digit with no word
    // next to it means hue is the only thing distinguishing the three counts.
    const html = text(render({ working: 1, idle: 0, blocked: 1 }));
    expect(html).not.toMatch(/^\s*1\s+0\s+1\b/);
    expect(html).toContain("1 blocked");
  });

  it("names the project, the session state and the quota state, and nothing else", () => {
    expect(text(render({ working: 1, idle: 0, blocked: 1 })))
      .toBe("Grove 1 blocked 1 at limit");
  });

  it("drops the branch, worktree, sub-path and version from the strip", () => {
    // Not an oversight: they are the values that forced truncation. All four
    // are in the sheet, which is one tap away.
    const html = render({ working: 1, idle: 0, blocked: 1 });
    expect(html).not.toContain("feat/footer");
    expect(html).not.toContain("footer-ui");
    expect(html).not.toContain("v0.0.9");
  });
});

describe("the one count it shows is the most severe one", () => {
  it("reports blocked even when working and idle are larger", () => {
    // The whole point of the severity rule: a strip showing the first of a
    // list could print `9 working` while an agent sat blocked.
    expect(text(render({ working: 9, idle: 7, blocked: 1 }))).toContain("1 blocked");
  });

  it("wears the same tone its fleet card does", () => {
    // AGENT_TONE.blocked is destructive. Scoped to the SESSIONS value, because
    // the exhausted account beside it carries that class too — a document-wide
    // colour census cannot tell which value wore the colour.
    //
    // Slice from the WRAPPER, not the glyph: the tone class is on the span
    // that contains the mark, so slicing forward from `lucide-octagon-alert`
    // starts after the only thing being asserted and can never pass.
    const html = render({ working: 0, idle: 0, blocked: 2 }, { accounts: [] });
    const glyph = html.indexOf("lucide-octagon-alert");
    const session = html.slice(html.lastIndexOf("<span", glyph));
    expect(session).toContain("text-destructive");
    // The count rides its own `tabular-nums` span, so `2 blocked` is never one
    // string in the markup — read the slice's TEXT, the way a reader does.
    expect(text(session)).toContain("2 blocked");
  });

  it("falls through to working, then idle, then says so", () => {
    expect(text(render({ working: 3, idle: 5, blocked: 0 }))).toContain("3 working");
    expect(text(render({ working: 0, idle: 5, blocked: 0 }))).toContain("5 idle");
    expect(text(render({ working: 0, idle: 0, blocked: 0 }))).toContain("no sessions");
  });
});

describe("the quota slot can still report an exhausted account", () => {
  it("names the limit rather than a count, since no account is shown", () => {
    // The phone shows no account summaries at all, so this is the ONLY thing
    // that can report exhaustion; a plain `2 accounts` would imply all is well.
    const html = render({ working: 1, idle: 0, blocked: 0 });
    expect(text(html)).toContain("1 at limit");
    expect(text(html)).not.toContain("2 accounts");
  });

  it("counts accounts when every one of them is healthy", () => {
    const healthy = accounts.map((a) => ({ ...a, percent: 4 }));
    expect(text(render({ working: 1, idle: 0, blocked: 0 }, { accounts: healthy })))
      .toContain("2 accounts");
  });

  it("prints no account identity, the same rule the wide strip holds", () => {
    // The band is permanently visible, screenshares included.
    expect(render({ working: 1, idle: 0, blocked: 0 })).not.toContain("@");
  });
});

describe("a quota reading is rounded for DISPLAY only", () => {
  it("prints at most one decimal", () => {
    // A generic provider published `23.63`, which is five characters of
    // precision in a band this narrow. Pre-existing on `main`; fixed at the
    // shared seam because the sheet inherits the same formatter.
    const noisy = [{ ...accounts[0], percent: 23.63, stale: true }];
    expect(text(render({ working: 1, idle: 0, blocked: 0 }, { accounts: noisy })))
      .not.toContain("23.63");
  });

  it("still compares the RAW value, so 99.6% is not reported as at limit", () => {
    // Rounding before the threshold would claim a limit the provider never
    // reported — the one way this formatter could become a lie.
    const near = [{ ...accounts[0], percent: 99.6 }];
    const html = text(render({ working: 1, idle: 0, blocked: 0 }, { accounts: near }));
    expect(html).toContain("near limit");
    expect(html).not.toContain("at limit");
  });
});

describe("a disconnected daemon says so rather than reporting zero", () => {
  const down = render({ working: 0, idle: 0, blocked: 0 }, { connected: false });

  it("names the state in warning and publishes no counts", () => {
    expect(text(down)).toContain("offline");
    expect(down).toContain("text-warning");
    expect(text(down)).not.toContain("no sessions");
    expect(text(down)).not.toContain("accounts");
  });
});

describe("it stays a single reachable control inside the band", () => {
  it("exposes exactly one trigger, named for what it opens", () => {
    const html = render({ working: 1, idle: 0, blocked: 1 });
    expect(html.match(/data-testid="footer-summary-trigger"/g) ?? []).toHaveLength(1);
    expect(html).toContain('aria-label="Show full status"');
  });

  it("mounts no sheet content while closed", () => {
    // A closed Radix sheet renders nothing — so the emails, branch and version
    // inside it cannot leak into the persistent strip.
    expect(render({ working: 1, idle: 0, blocked: 1 }))
      .not.toContain('data-testid="footer-summary-details"');
  });

  it("hides every decorative glyph and names no raw palette class", () => {
    const html = render({ working: 1, idle: 0, blocked: 1 });
    expect(html).not.toMatch(/<svg(?![^>]*aria-hidden)/);
    expect(html).not.toMatch(/(bg|text|border)-(zinc|slate|gray|neutral|red|green|blue|amber)-\d{2,3}/);
  });

  it("omits the project when no project is in scope", () => {
    const bare = render(
      { working: 1, idle: 0, blocked: 0 },
      { context: { project: null, subpath: null, branch: null, worktree: null } },
    );
    expect(text(bare)).toBe("1 working 1 at limit");
  });
});
