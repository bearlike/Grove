import { readFileSync } from "node:fs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BoxIcon, GaugeIcon } from "lucide-react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChangesTab } from "@/components/grove/workspace/changes-tab";
import { InfoTab } from "@/components/grove/workspace/info-tab";
import {
  CardCell,
  CardDisclosure,
  CardRegion,
  SectionCard,
} from "@/components/grove/card";
import { WorkspaceCard } from "@/components/grove/fleet/workspace-card";
import { workspace } from "@/tests/fixtures/fleet";
import { FIXTURE_PEEK } from "../e2e/_fixtures";

/**
 * The card anatomy is a shared contract, not a look each tab arrives at on its
 * own: a reader has to be able to see where a card's header ends and its body
 * begins without parsing the words. `SectionCard` is where that is decided, so
 * these pin what it renders and then check two tabs inherit it unchanged.
 *
 * Classes are asserted here on purpose. The header/body separation IS a set of
 * rendered class names — there is no other artifact of it — and a tab quietly
 * regaining its own padding or type size is exactly the drift this file exists
 * to catch.
 */
function card(
  props: Partial<React.ComponentProps<typeof SectionCard>> = {},
): string {
  return renderToStaticMarkup(
    <SectionCard
      icon={<BoxIcon />}
      title="Divergence"
      description="against main"
      {...props}
    >
      <p>body</p>
    </SectionCard>,
  );
}

function slot(html: string, name: string): string {
  const match = html.match(new RegExp(`<div[^>]*data-slot="${name}"[^>]*>`));
  if (!match) throw new Error(`no ${name} in markup`);
  return match[0];
}

describe("SectionCard", () => {
  it("separates the lifted header from the body with its gradient contract and a rule", () => {
    const html = card();

    expect(slot(html, "card-header")).toContain("surface-header");
    expect(slot(html, "card-header")).toContain("px-3 py-1.5");
    expect(slot(html, "card-header")).not.toContain("bg-muted/40");
    expect(slot(html, "card-content")).toContain("border-t");
  });

  it("puts the header and the body in that order, exactly once each", () => {
    const html = card();

    expect(html.match(/data-slot="card-header"/g)).toHaveLength(1);
    expect(html.match(/data-slot="card-content"/g)).toHaveLength(1);
    expect(html.indexOf("card-header")).toBeLessThan(
      html.indexOf("card-content"),
    );
  });

  it("keeps the description as the first freely-wrapping body child", () => {
    const html = card();

    expect(slot(html, "card-title")).toContain("text-sm");
    expect(slot(html, "card-title")).toContain("font-medium");
    expect(slot(html, "card-description")).toContain("text-xs");
    expect(slot(html, "card-description")).toContain("text-content-tertiary");
    expect(html.indexOf("card-content")).toBeLessThan(
      html.indexOf("card-description"),
    );
    expect(html.indexOf("card-description")).toBeLessThan(
      html.indexOf("<p>body</p>"),
    );
    expect(slot(html, "card-content")).toContain("text-sm");
  });

  it("takes its radius and elevation from the vendored Card", () => {
    // The one place a shadow or radius may enter `components/grove`: by being
    // inherited rather than written. `lint:styling` enforces the other half.
    expect(slot(card(), "card")).toContain("rounded-xl");
    expect(slot(card(), "card")).toContain("shadow-sm");
  });

  it("keeps its own gutter unless the child draws one, and restores a flush description gutter", () => {
    expect(slot(card(), "card-content")).toContain("p-3");
    expect(slot(card({ flush: true }), "card-content")).toContain("p-0");
    expect(slot(card({ flush: true }), "card-description")).toContain(
      "px-3 pt-3",
    );
  });

  it("carries an icon and an optional action in the header", () => {
    const html = card({ action: <span>ACTION</span> });

    expect(slot(html, "card-title")).toBeTruthy();
    expect(html).toContain("lucide-box");
    expect(html).toContain("ACTION");
    expect(html).toContain('data-slot="card-action"');
  });

  it("renders no description row when there is nothing to say", () => {
    expect(card({ description: undefined })).not.toContain(
      'data-slot="card-description"',
    );
  });
});

describe("card radius roles", () => {
  it("keeps outer cards at the vendored container role while regions and cells select the inner role", () => {
    const region = renderToStaticMarkup(
      <CardRegion data-testid="region">facts</CardRegion>,
    );
    const cell = renderToStaticMarkup(
      <CardCell
        data-testid="cell"
        icon={<GaugeIcon aria-hidden />}
        label="Tool calls"
        value="1,248"
      />,
    );

    expect(slot(card(), "card")).toContain("rounded-xl");
    expect(slot(region, "card")).toContain("card-region");
    expect(slot(cell, "card")).toContain("card-region");
  });
});

describe("the header gradient", () => {
  const globals = readFileSync(
    new URL("../../app/globals.css", import.meta.url),
    "utf8",
  );

  it("is header-only, bounded by named stops, and collapses to Canvas in forced colours", () => {
    // Two layers: the 1px catch-light first, then the band under it. The
    // highlight is what makes the band read as chrome rather than as a fill,
    // so a single-layer gradient here would be the regression, not a tidy-up.
    expect(globals).toMatch(
      /@utility surface-header\s*\{\s*background-image:\s*linear-gradient\(to bottom, var\(--surface-header-highlight\) 0 1px, transparent 1px\),\s*linear-gradient\(to bottom, var\(--surface-header-start\), var\(--surface-header-end\)\);\s*\}/,
    );
    expect(globals).toMatch(
      /@media \(forced-colors: active\)\s*\{\s*\.surface-header\s*\{\s*background: Canvas;/,
    );
    expect(globals).not.toMatch(
      /@utility card-region\s*\{[^}]*background-image/,
    );
  });

  it("pins the darker-stop contrast floors in both themes", () => {
    const themes = [
      { background: 0.985, primary: 0.141, icon: 0.495 },
      { background: 0.28, primary: 0.985, icon: 0.705 },
    ];
    const luminance = (lightness: number) => Math.pow(lightness, 3);
    const ratio = (a: number, b: number) => {
      const [lighter, darker] = [luminance(a), luminance(b)].sort(
        (x, y) => y - x,
      );
      return (lighter + 0.05) / (darker + 0.05);
    };

    for (const theme of themes) {
      expect(ratio(theme.primary, theme.background)).toBeGreaterThanOrEqual(
        4.5,
      );
      expect(ratio(theme.icon, theme.background)).toBeGreaterThanOrEqual(3);
    }
  });
});

const cardSource = readFileSync(
  new URL("../../components/grove/card.tsx", import.meta.url),
  "utf8",
);
const globalsSource = readFileSync(
  new URL("../../app/globals.css", import.meta.url),
  "utf8",
);

it("keeps header-only gradient selection out of disclosures", () => {
  expect(cardSource).toMatch(/header && "bg-muted\/40"/);
  expect(cardSource).not.toMatch(/header && "surface-header"/);
  expect(globalsSource).toContain("@utility surface-header");
});

// CardDisclosure is intentionally unchanged: it has its own header opt-in and
// is not a SectionCard header band. Its existing assertions below pin that seam.

describe("CardDisclosure", () => {
  /**
   * `header` is off by default because a `CardDisclosure` is as often a ROW
   * inside another card's own headed body (the Files tab's per-file rows) as
   * it is a card's whole surface (the plan and queue cards) — see the prop's
   * docstring in `card.tsx` for the full per-call-site accounting.
   */
  function disclosure(open: boolean, header?: boolean): string {
    return renderToStaticMarkup(
      <CardDisclosure
        open={open}
        onOpenChange={() => {}}
        header={header}
        summary="Trigger"
      >
        <p>detail</p>
      </CardDisclosure>,
    );
  }

  it("is a plain row by default — no tint, no boundary rule", () => {
    const html = disclosure(true);
    const trigger = html.match(
      /<button[^>]*data-slot="collapsible-trigger"[^>]*>/,
    )?.[0];

    expect(trigger).toBeTruthy();
    expect(trigger).not.toContain("bg-muted/40");
    expect(html).not.toContain("border-t");
  });

  it("draws a SectionCard-style boundary when asked: tint on the trigger, rule where the body starts", () => {
    const html = disclosure(true, true);
    const trigger = html.match(
      /<button[^>]*data-slot="collapsible-trigger"[^>]*>/,
    )?.[0];

    expect(trigger).toContain("bg-muted/40");
    expect(html).toContain("border-t");
  });

  it("keeps the tint on the trigger even collapsed — it is the card's header, not a body decoration", () => {
    const trigger = disclosure(false, true).match(
      /<button[^>]*data-slot="collapsible-trigger"[^>]*>/,
    )?.[0];

    expect(trigger).toContain("bg-muted/40");
  });
});

describe("every surface inherits the anatomy", () => {
  it("the fleet card is the same card, not a lookalike", () => {
    // The consolidation this file exists to hold: fleet used to hand-compose a
    // vendored `Card` at the default `gap-6 py-6` rhythm with its own footer,
    // so a workspace card and a panel card were visibly different objects.
    const html = renderToStaticMarkup(
      <WorkspaceCard
        workspace={workspace({ id: "w1", title: "Ship it" })}
        repoName="grove"
      />,
    );

    expect(slot(html, "card-header")).toContain("surface-header");
    expect(slot(html, "card-content")).toContain("border-t");
    expect(slot(html, "card-title")).toContain("text-sm");
    // The agent's brand mark is this card's icon, and the counters moved into
    // the body — a footer would have been a third band to read.
    expect(html).toContain('data-testid="agent-mark"');
    expect(html).not.toContain('data-slot="card-footer"');
  });

  it("Changes gets it without styling anything itself", () => {
    const html = renderToStaticMarkup(
      <ChangesTab peek={FIXTURE_PEEK} commits={[]} />,
    );
    const headers = html.match(/data-slot="card-header"[^>]*/g) ?? [];

    expect(headers).toHaveLength(2);
    for (const header of headers) expect(header).toContain("surface-header");
    expect(html).toContain("Divergence");
    expect(html).toContain("Commits");
  });

  it("Info gets the same header for every card", () => {
    // Info's ticket card reaches for react-query's client; nothing here
    // fetches, the provider is only what lets the tab mount.
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <InfoTab
          peek={FIXTURE_PEEK}
          activity={null}
          repoRoot={FIXTURE_PEEK.state.repo_root}
          identity={FIXTURE_PEEK.state}
        />
      </QueryClientProvider>,
    );
    const headers = html.match(/data-slot="card-header"[^>]*/g) ?? [];
    const contents = html.match(/data-slot="card-content"[^>]*/g) ?? [];

    expect(headers.length).toBeGreaterThan(3);
    expect(contents).toHaveLength(headers.length);
    for (const header of headers) expect(header).toContain("surface-header");
    for (const content of contents) expect(content).toContain("border-t");
  });
});
