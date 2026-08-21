import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BoxIcon } from "lucide-react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChangesTab } from "@/components/grove/workspace/changes-tab";
import { InfoTab } from "@/components/grove/workspace/info-tab";
import { CardDisclosure, SectionCard } from "@/components/grove/card";
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
function card(props: Partial<React.ComponentProps<typeof SectionCard>> = {}): string {
  return renderToStaticMarkup(
    <SectionCard icon={<BoxIcon />} title="Divergence" description="against main" {...props}>
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
  it("separates header from body with a tint and a rule", () => {
    const html = card();

    expect(slot(html, "card-header")).toContain("bg-muted/40");
    expect(slot(html, "card-content")).toContain("border-t");
  });

  it("puts the header and the body in that order, exactly once each", () => {
    const html = card();

    expect(html.match(/data-slot="card-header"/g)).toHaveLength(1);
    expect(html.match(/data-slot="card-content"/g)).toHaveLength(1);
    expect(html.indexOf("card-header")).toBeLessThan(html.indexOf("card-content"));
  });

  it("holds one type scale: title, description, body", () => {
    const html = card();

    expect(slot(html, "card-title")).toContain("text-sm");
    expect(slot(html, "card-title")).toContain("font-medium");
    expect(slot(html, "card-description")).toContain("text-xs");
    expect(slot(html, "card-content")).toContain("text-sm");
  });

  it("takes its radius and elevation from the vendored Card", () => {
    // The one place a shadow or radius may enter `components/grove`: by being
    // inherited rather than written. `lint:styling` enforces the other half.
    expect(slot(card(), "card")).toContain("rounded-xl");
    expect(slot(card(), "card")).toContain("shadow-sm");
  });

  it("keeps its own gutter unless the child draws one", () => {
    expect(slot(card(), "card-content")).toContain("p-3");
    expect(slot(card({ flush: true }), "card-content")).toContain("p-0");
  });

  it("carries an icon and an optional action in the header", () => {
    const html = card({ action: <span>ACTION</span> });

    expect(slot(html, "card-title")).toBeTruthy();
    expect(html).toContain("lucide-box");
    expect(html).toContain("ACTION");
    expect(html).toContain('data-slot="card-action"');
  });

  it("renders no description row when there is nothing to say", () => {
    expect(card({ description: undefined })).not.toContain('data-slot="card-description"');
  });
});

describe("CardDisclosure", () => {
  /**
   * `header` is off by default because a `CardDisclosure` is as often a ROW
   * inside another card's own headed body (the Files tab's per-file rows) as
   * it is a card's whole surface (the plan and queue cards) — see the prop's
   * docstring in `card.tsx` for the full per-call-site accounting.
   */
  function disclosure(open: boolean, header?: boolean): string {
    return renderToStaticMarkup(
      <CardDisclosure open={open} onOpenChange={() => {}} header={header} summary="Trigger">
        <p>detail</p>
      </CardDisclosure>,
    );
  }

  it("is a plain row by default — no tint, no boundary rule", () => {
    const html = disclosure(true);
    const trigger = html.match(/<button[^>]*data-slot="collapsible-trigger"[^>]*>/)?.[0];

    expect(trigger).toBeTruthy();
    expect(trigger).not.toContain("bg-muted/40");
    expect(html).not.toContain("border-t");
  });

  it("draws a SectionCard-style boundary when asked: tint on the trigger, rule where the body starts", () => {
    const html = disclosure(true, true);
    const trigger = html.match(/<button[^>]*data-slot="collapsible-trigger"[^>]*>/)?.[0];

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
      <WorkspaceCard workspace={workspace({ id: "w1", title: "Ship it" })} repoName="grove" />,
    );

    expect(slot(html, "card-header")).toContain("bg-muted/40");
    expect(slot(html, "card-content")).toContain("border-t");
    expect(slot(html, "card-title")).toContain("text-sm");
    // The agent's brand mark is this card's icon, and the counters moved into
    // the body — a footer would have been a third band to read.
    expect(html).toContain('data-testid="agent-mark"');
    expect(html).not.toContain('data-slot="card-footer"');
  });

  it("Changes gets it without styling anything itself", () => {
    const html = renderToStaticMarkup(<ChangesTab peek={FIXTURE_PEEK} commits={[]} />);
    const headers = html.match(/data-slot="card-header"[^>]*/g) ?? [];

    expect(headers).toHaveLength(2);
    for (const header of headers) expect(header).toContain("bg-muted/40");
    expect(html).toContain("Divergence");
    expect(html).toContain("Commits");
  });

  it("Info gets the same header for every card", () => {
    // Info's lifecycle buttons reach for react-query's client; nothing here
    // fetches, the provider is only what lets the tab mount.
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <InfoTab
          peek={FIXTURE_PEEK}
          activity={null}
          repoRoot={FIXTURE_PEEK.state.repo_root}
          privileged={{ state: FIXTURE_PEEK.state, onKilled: () => {} }}
        />
      </QueryClientProvider>,
    );
    const headers = html.match(/data-slot="card-header"[^>]*/g) ?? [];
    const contents = html.match(/data-slot="card-content"[^>]*/g) ?? [];

    expect(headers.length).toBeGreaterThan(3);
    expect(contents).toHaveLength(headers.length);
    for (const header of headers) expect(header).toContain("bg-muted/40");
    for (const content of contents) expect(content).toContain("border-t");
  });
});
