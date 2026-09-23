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
 * GROVE'S TICKET CLAIM MUST STAY ATTACHED TO THE TICKET THAT MADE IT.
 *
 * Tracker state says what a ticket is; Grove's phase says what the agent reports
 * doing about it. The two frequently disagree, which is useful. A ticket with no
 * claim is equally meaningful: it must say so, never borrow the workspace phase.
 * The ticket note also belongs here, with the row it explains, not in a second
 * report list on Task.
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
    draft: false,
    assignee: null,
    ambiguous: false,
    ...overrides,
  };
}

function claim(overrides: Partial<PhaseView["tickets"][number]> = {}) {
  return { ticket: "gitea:42", phase: "build" as const, note: null, blocked: false, index: 2, ...overrides };
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
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <TicketRefsCard repoRoot={REPO} refs={refs} phase={phase} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("the join key", () => {
  it("is provider:id, the coordinate the wire writes", () => {
    expect(ticketPhaseKey({ provider: "gitea", id: "42" })).toBe("gitea:42");
    expect(ticketPhaseKey({ provider: "linear", id: "ENG-7" })).toBe("linear:ENG-7");
  });

  it("omits kind, so a resolved kind correction keeps its claim", () => {
    expect(ticketPhaseKey({ provider: "gitea", id: "42" })).toBe("gitea:42");
  });
});

describe("ticketPhases", () => {
  it("has nothing to say when an agent has reported no ticket phases", () => {
    expect(ticketPhases(null).size).toBe(0);
    expect(ticketPhases(undefined).size).toBe(0);
  });

  it("takes total from the parent sequence rather than pinning a second copy", () => {
    expect(ticketPhases({ ...phaseWith(claim()), total: 7 }).get("gitea:42")?.total).toBe(7);
  });

  it("keeps each ticket's own position rather than the workspace position", () => {
    const claims = ticketPhases(
      phaseWith(claim({ index: 0, phase: "scope" }), claim({ ticket: "gitea:9", index: 4, phase: "deliver" })),
    );

    expect(claims.get("gitea:42")?.phase).toBe("scope");
    expect(claims.get("gitea:9")?.phase).toBe("deliver");
  });
});

describe("a ticket row", () => {
  function row(ticket: TicketRef, phase?: Parameters<typeof TicketRow>[0]["phase"]): string {
    return renderToStaticMarkup(
      <TooltipProvider>
        <TicketRow ticket={ticket} resolving={false} phase={phase} />
      </TooltipProvider>,
    );
  }

  it("states tracker status as a coloured word and Grove's claim separately", () => {
    const html = row(ref({ status: "open" }), { ...claim(), total: 6 });

    expect(html).toContain('data-testid="ticket-status"');
    expect(html).toContain("open");
    expect(html).toContain("text-success");
    expect(html).toContain('data-testid="ticket-claim"');
    expect(html).toContain('data-testid="phase-badge"');
    expect(html).toContain("Step 3 of 6");
  });

  it("calls a missing agent claim not reported, rather than inventing scope", () => {
    const html = row(ref());

    expect(html).not.toContain('data-testid="phase-badge"');
    expect(html).toContain('data-testid="ticket-claim"');
    expect(html).toContain("No phase reported");
    expect(html).not.toContain("Step 0 of 6");
  });

  it("renders the complete ticket note on its ticket row", () => {
    const html = row(ref(), { ...claim({ note: "Awaiting the reviewer’s exact answer" }), total: 6 });

    expect(html).toContain('data-testid="ticket-note"');
    expect(html).toContain("Awaiting the reviewer’s exact answer");
    expect(html).toContain("break-words");
  });

  it("marks a blocked ticket and only a blocked ticket", () => {
    expect(row(ref(), { ...claim({ blocked: true }), total: 6 })).toContain('data-blocked="true"');
    expect(row(ref(), { ...claim(), total: 6 })).not.toContain("data-blocked");
  });

  it("puts the tracker state before the agent's claim in reading order", () => {
    const html = row(ref({ status: "open" }), { ...claim(), total: 6 });

    expect(html.indexOf('data-testid="ticket-status"')).toBeLessThan(
      html.indexOf('data-testid="ticket-claim"'),
    );
  });
});

describe("the ticket card", () => {
  it("joins each claim only to its matching provider and id", () => {
    const html = card({
      refs: [ref({ id: "42" }), ref({ id: "9" })],
      phase: phaseWith(claim({ ticket: "gitea:42", note: "Reported here" })),
    });

    expect(html.match(/data-testid="phase-badge"/g)).toHaveLength(1);
    expect(html.match(/data-testid="ticket-note"/g)).toHaveLength(1);
    expect(html).toContain("Reported here");
  });

  it("does not lend the workspace phase to an unclaimed ticket", () => {
    const html = card({ refs: [ref()], phase: phaseWith() });

    expect(html).not.toContain('data-testid="phase-badge"');
    expect(html).toContain("No phase reported");
  });

  it("contains the aggregate here and never restores Task's obsolete report list", () => {
    const html = card({ refs: [ref()], phase: phaseWith(claim()) });

    expect(html).toContain('data-testid="ticket-rollup"');
    expect(html).not.toContain("Ticket reports");
    expect(html).not.toContain("ticket-phase-counts");
  });
});
