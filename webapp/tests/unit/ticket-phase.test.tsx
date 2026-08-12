import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TicketRefsCard, TicketRow } from "@/components/grove/workspace/ticket-refs";
import { ticketPhaseKey, ticketPhases } from "@/components/grove/workspace/selectors";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { PhaseView, TicketProviderView, TicketRef } from "@/lib/grove/api";
import { groveKeys } from "@/lib/grove/hooks";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * GROVE'S PROGRESS ON A TICKET, BESIDE THE TRACKER'S OWN STATUS.
 *
 * The two can disagree and that disagreement is the information: an issue that
 * still reads `open` while Grove reports `delivering` is a task in flight, and a
 * reader who sees only one half cannot tell that from a workspace that stalled.
 *
 * The rule everything below defends is that ABSENCE OF A CLAIM IS NOT A CLAIM. A
 * ticket the agent has not reported on renders no mark at all — never `scoping`,
 * never an unfilled step zero, and never the workspace's own phase borrowed on
 * its behalf.
 */

const REPO = "/repos/acme/api";

function ref(overrides: Partial<TicketRef> = {}): TicketRef {
  return {
    provider: "gitea",
    id: "42",
    kind: "issue",
    title: "Widgets render twice on resize",
    url: "https://tracker.example/acme/api/issues/42",
    status: "open",
    assignee: null,
    ambiguous: false,
    ...overrides,
  };
}

function claim(overrides: Partial<PhaseView["tickets"][number]> = {}) {
  return { ticket: "gitea:42", phase: "implementing" as const, note: null, blocked: false, index: 2, ...overrides };
}

function phaseWith(...tickets: PhaseView["tickets"]): PhaseView {
  return { ...FIXTURE_PHASE, tickets };
}

function card({
  refs,
  phase,
  providers = [{ provider: "gitea", label: "Gitea", configured: true, context: "acme/api" }],
}: {
  refs: readonly TicketRef[];
  phase: PhaseView | null;
  providers?: TicketProviderView[];
}): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(groveKeys.ticketProviders(REPO), providers);
  // The phase mark mounts a Radix `Tooltip`, which the app provides for at the
  // root (`providers.tsx`); every other bare-tooltip test wraps the same way.
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <TicketRefsCard repoRoot={REPO} refs={refs} phase={phase} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("the join key", () => {
  it("is `provider:id`, the coordinate the wire writes", () => {
    expect(ticketPhaseKey({ provider: "gitea", id: "42" })).toBe("gitea:42");
    expect(ticketPhaseKey({ provider: "linear", id: "ENG-7" })).toBe("linear:ENG-7");
  });

  it("omits kind, so a ref that resolves to the other kind keeps its claim", () => {
    // A forge numbers issues and pull requests in one space, so a ref attached
    // as an issue can legitimately come back a pull request. The three-part
    // `ticketKey` moves under that correction; this one cannot.
    expect(ticketPhaseKey({ provider: "gitea", id: "42" })).toBe(
      ticketPhaseKey({ provider: "gitea", id: "42" }),
    );
  });
});

describe("ticketPhases", () => {
  it("has nothing to say when the agent has reported no phase at all", () => {
    expect(ticketPhases(null).size).toBe(0);
    expect(ticketPhases(undefined).size).toBe(0);
  });

  it("misses for a ticket the agent has not claimed", () => {
    const claims = ticketPhases(phaseWith(claim()));

    expect(claims.get("gitea:99")).toBeUndefined();
  });

  it("takes `total` from the parent rather than pinning a second copy", () => {
    // The per-ticket row is a position in the SAME order its parent counts, so
    // a Grove that grew a seventh phase would move both together.
    const claims = ticketPhases({ ...phaseWith(claim()), total: 7 });

    expect(claims.get("gitea:42")?.total).toBe(7);
  });

  it("keeps each ticket's own position, never the workspace's", () => {
    const claims = ticketPhases(
      phaseWith(claim({ index: 0, phase: "scoping" }), claim({ ticket: "gitea:9", index: 4, phase: "delivering" })),
    );

    expect(claims.get("gitea:42")?.phase).toBe("scoping");
    expect(claims.get("gitea:9")?.phase).toBe("delivering");
  });
});

describe("a row", () => {
  function row(ticket: TicketRef, phase?: Parameters<typeof TicketRow>[0]["phase"]): string {
    return renderToStaticMarkup(
      <TooltipProvider>
        <TicketRow ticket={ticket} resolving={false} phase={phase} />
      </TooltipProvider>,
    );
  }

  it("shows both halves: what the ticket says and how far Grove has got", () => {
    const html = row(ref({ status: "open" }), { ...claim(), total: 6 });

    expect(html).toContain('data-testid="ticket-status"');
    expect(html).toContain("open");
    expect(html).toContain('data-testid="phase-badge"');
    expect(html).toContain("3/6");
  });

  it("reads as NOT REPORTED with no claim — not scoping, not an empty meter", () => {
    const html = row(ref());

    expect(html).not.toContain('data-testid="phase-badge"');
    expect(html).not.toContain("scoping");
    expect(html).not.toContain("0/6");
    // And the row is otherwise untouched, which is the actual contract: a
    // workspace on an older daemon must look exactly as it did before.
    expect(html).toContain("#42");
    expect(html).toContain('data-testid="ticket-status"');
  });

  it("marks a blocked ticket, and marks only that one", () => {
    expect(row(ref(), { ...claim({ blocked: true }), total: 6 })).toContain(
      'data-blocked="true"',
    );
    expect(row(ref(), { ...claim(), total: 6 })).not.toContain("data-blocked");
  });

  it("stays inside the badge budget with every mark at once", () => {
    // uncertain + tracker status + Grove's phase + blocked, all on one row.
    const html = row(ref({ ambiguous: true, status: "merged" }), {
      ...claim({ blocked: true }),
      total: 6,
    });

    // §6: at most one `default` per object — this card spends none at all.
    expect(html).not.toContain('data-variant="default"');
    // §6: at most three TONED badges. `uncertain` and the phase mark are
    // hairline outlines when calm, so the toned ones here are the tracker's
    // `secondary` and blocked's `destructive` — two, with a step to spare.
    expect(html.match(/data-variant="destructive"/g)).toHaveLength(1);
    expect(html.match(/data-variant="secondary"/g)).toHaveLength(1);
  });

  it("puts the tracker's word before Grove's claim", () => {
    // Reading order: what it IS, then what we have done about it.
    const html = row(ref({ status: "open" }), { ...claim(), total: 6 });

    expect(html.indexOf('data-testid="ticket-status"')).toBeLessThan(
      html.indexOf('data-testid="phase-badge"'),
    );
  });
});

describe("the card", () => {
  it("marks the claimed ticket and leaves the unclaimed one bare", () => {
    const html = card({
      refs: [ref({ id: "42" }), ref({ id: "9" })],
      phase: phaseWith(claim({ ticket: "gitea:42" })),
    });

    expect(html.match(/data-testid="phase-badge"/g)).toHaveLength(1);
  });

  it("never lends the workspace's own phase to a ticket nobody claimed", () => {
    // The workspace IS at `delivering` — the Task card above says so — and that
    // says nothing about this issue. Inventing progress here is the one failure
    // this join must not have.
    const html = card({ refs: [ref()], phase: phaseWith() });

    expect(html).not.toContain('data-testid="phase-badge"');
    expect(html).toContain("#42");
  });

  it("draws no marks at all against a workspace that has reported nothing", () => {
    expect(card({ refs: [ref()], phase: null })).not.toContain('data-testid="phase-badge"');
  });

  it("joins on provider AND id, so one provider's #42 is not another's", () => {
    const html = card({
      refs: [ref({ provider: "github", id: "42" })],
      phase: phaseWith(claim({ ticket: "gitea:42" })),
      providers: [{ provider: "github", label: "GitHub", configured: true, context: "acme/api" }],
    });

    expect(html).not.toContain('data-testid="phase-badge"');
  });
});
