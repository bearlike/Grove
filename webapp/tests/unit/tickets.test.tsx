import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TicketRefsCard, TicketRow } from "@/components/grove/workspace/ticket-refs";
import {
  mergeTicket,
  providerLabel,
  sortTicketRefs,
  compareTicketRefs,
  ticketIdLabel,
  ticketKindLabel,
  ticketStatusTone,
} from "@/components/grove/workspace/selectors";
import type { TicketProviderView, TicketRef } from "@/lib/grove/api";
import { groveKeys } from "@/lib/grove/hooks";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * What this surface owes a reader, pinned.
 *
 * The bug it was built for was not a rendering bug: `ticket_refs` was empty on
 * a real host and BOTH the card and its contents returned null, so the whole
 * mechanism read as missing. So the first thing these assert is that the card
 * exists with nothing attached — an empty state is a state, not an absence.
 *
 * The rest are the rules that cannot be seen by looking at the happy path: a
 * cached value is never blanked by a live read, `ambiguous` is never cleared by
 * a tracker that has no opinion about it, and every state a row carries is
 * spelled as a WORD beside its colour.
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

function provider(overrides: Partial<TicketProviderView> = {}): TicketProviderView {
  return { provider: "gitea", label: "Gitea", configured: true, context: "acme/api", ...overrides };
}

/**
 * Render the card against a primed cache.
 *
 * `setQueryData` is the whole stub: the hooks, the merge, the keys and the
 * component all run for real, and only the network is replaced. No effects run
 * under `renderToStaticMarkup`, so nothing fetches.
 */
function card({
  refs,
  providers,
  resolved = [],
}: {
  refs: readonly TicketRef[];
  providers?: TicketProviderView[];
  resolved?: TicketRef[];
}): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (providers) client.setQueryData(groveKeys.ticketProviders(REPO), providers);
  for (const live of resolved) {
    client.setQueryData(groveKeys.ticket(REPO, live.provider, live.id), live);
  }
  // `phase={null}` throughout: every rule in this file is about the TRACKER's
  // half of a row. Grove's own per-ticket progress has its own file, and passing
  // it here would put a second variable in assertions that count badges.
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <TicketRefsCard repoRoot={REPO} refs={refs} phase={null} />
    </QueryClientProvider>,
  );
}

function row(ticket: TicketRef, resolving = false): string {
  return renderToStaticMarkup(<TicketRow ticket={ticket} resolving={resolving} />);
}

describe("the card with nothing attached", () => {
  it("still renders — the state the user was actually seeing", () => {
    const html = card({ refs: [], providers: [provider()] });

    expect(html).toContain('data-testid="tickets-card"');
    expect(html).toContain('data-testid="tickets-empty"');
    expect(html).toContain("Tickets");
  });

  it("says how to get one, with the command that does it", () => {
    const html = card({ refs: [], providers: [provider()] });

    expect(html).toContain("No issue or pull request is linked");
    expect(html).toContain("grove tickets attach");
  });

  it("names the trackers this project can talk to", () => {
    const html = card({ refs: [], providers: [provider({ context: "acme/api" })] });

    expect(html).toContain('data-testid="tickets-providers"');
    expect(html).toContain("Gitea");
    expect(html).toContain("acme/api");
  });

  it("distinguishes a project with no provider from one that has not linked yet", () => {
    expect(card({ refs: [], providers: [] })).toContain("No ticket provider is enabled");
  });

  it("shows a skeleton, not a claim, while the provider list is unknown", () => {
    const html = card({ refs: [] });

    expect(html).toContain('data-testid="tickets-providers-loading"');
    expect(html).not.toContain("No ticket provider is enabled");
  });

  it("carries no count badge when there is nothing to count", () => {
    expect(card({ refs: [], providers: [provider()] })).not.toContain('data-testid="ticket-count"');
  });
});

describe("a row", () => {
  it("renders the id, the title and a clickable link", () => {
    const html = row(ref());

    expect(html).toContain("#42");
    expect(html).toContain("Widgets render twice on resize");
    expect(html).toContain('href="https://tracker.example/acme/api/issues/42"');
    expect(html).toContain('target="_blank"');
  });

  it("carries the full title in `title`, so truncation loses nothing", () => {
    expect(row(ref())).toContain('title="Widgets render twice on resize"');
  });

  it("says which provider and which kind, in words", () => {
    expect(row(ref({ provider: "github", kind: "pull_request" }))).toContain("GitHub");
    expect(row(ref({ provider: "github", kind: "pull_request" }))).toContain("pull request");
  });

  it("spells the state out beside its colour", () => {
    // The badge variant is the second carrier; the word is the first. A
    // greyscale screenshot of a merged PR must still read `merged`.
    const html = row(ref({ kind: "pull_request", status: "merged" }));

    expect(html).toContain("merged");
    expect(html).toContain('data-testid="ticket-status"');
  });

  it("is not a link when the ref has no url", () => {
    expect(row(ref({ url: null }))).not.toContain("<a ");
  });

  it("marks an uncertain association in words, not just a hue", () => {
    const html = row(ref({ ambiguous: true }));

    expect(html).toContain('data-testid="ticket-ambiguous"');
    expect(html).toContain("uncertain");
  });

  it("shows a skeleton only where there is no title to show", () => {
    expect(row(ref({ title: null }), true)).toContain('data-testid="ticket-title-loading"');
    // The rule that matters: a title Grove already holds is never replaced by a
    // loading shape, however stale it is.
    expect(row(ref(), true)).not.toContain('data-testid="ticket-title-loading"');
  });

  it("says what is missing rather than rendering an empty line", () => {
    expect(row(ref({ title: null }))).toContain("No title recorded");
  });

  it("prints no status badge when the tracker reported no state", () => {
    expect(row(ref({ status: null }))).not.toContain('data-testid="ticket-status"');
  });
});

describe("the list", () => {
  it("renders every attached ref, pull requests before issues", () => {
    const html = card({
      refs: [ref({ id: "9", kind: "pull_request", title: "Fix it" }), ref({ id: "42" })],
      providers: [provider()],
    });

    expect(html).toContain("2 linked");
    expect(html.indexOf("#9")).toBeLessThan(html.indexOf("#42"));
  });

  it("warns once, and says how to resolve it, when any link is uncertain", () => {
    const html = card({ refs: [ref({ ambiguous: true })], providers: [provider()] });

    expect(html).toContain('data-testid="tickets-ambiguous-note"');
    expect(html).toContain("grove tickets detach");
  });

  it("prefers the live read over the cached one", () => {
    const html = card({
      refs: [ref({ status: "open", title: "Stale title" })],
      providers: [provider()],
      resolved: [ref({ status: "closed", title: "Fresh title" })],
    });

    expect(html).toContain("Fresh title");
    expect(html).toContain("closed");
    expect(html).not.toContain("Stale title");
  });

  it("degrades to the cached ref when a provider has no credentials", () => {
    const html = card({
      refs: [ref()],
      providers: [provider({ configured: false })],
    });

    // Partial data renders; only the missing part says so, and it names the
    // provider whose token is missing rather than the card as a whole.
    expect(html).toContain("Widgets render twice on resize");
    expect(html).toContain('data-testid="tickets-degraded"');
    expect(html).toContain("Gitea");
    expect(html).toContain("no credentials configured");
  });

  it("accuses nobody of missing credentials before the check has run", () => {
    // The first paint of the real app said "Gitea, Linear have no credentials"
    // while the provider list was still in flight — an unknown rendered as a
    // verdict, about a provider that was configured all along.
    const html = card({ refs: [ref()] });

    expect(html).not.toContain('data-testid="tickets-degraded"');
    expect(html).not.toContain("no credentials configured");
  });

  it("waits rather than reporting a title it has not tried to fetch", () => {
    const html = card({ refs: [ref({ title: null })] });

    expect(html).toContain('data-testid="ticket-title-loading"');
    expect(html).not.toContain("No title recorded");
  });

  it("degrades the same way for a provider the repo does not list at all", () => {
    const html = card({ refs: [ref({ provider: "linear", id: "ENG-7" })], providers: [] });

    expect(html).toContain("ENG-7");
    expect(html).toContain('data-testid="tickets-degraded"');
    expect(html).toContain("Linear");
  });
});

describe("presentation rules", () => {
  it("orders pull requests before issues", () => {
    const ordered = sortTicketRefs([
      ref({ id: "9", kind: "pull_request" }),
      ref({ id: "42", kind: "issue" }),
    ]);

    expect(ordered.map((each) => each.id)).toEqual(["9", "42"]);
  });

  it("keeps an unresolved ticket above settled work", () => {
    expect(
      compareTicketRefs(
        ref({ id: "2", status: null }),
        null,
        ref({ id: "1", status: "closed" }),
        null,
      ),
    ).toBeLessThan(0);
  });

  it("orders the furthest live phase first, then done, then no claim", () => {
    expect(
      compareTicketRefs(
        ref({ id: "1" }),
        { ...FIXTURE_PHASE.tickets[0]!, phase: "delivering", index: 4 },
        ref({ id: "2" }),
        { ...FIXTURE_PHASE.tickets[0]!, phase: "implementing", index: 2 },
      ),
    ).toBeLessThan(0);
    expect(
      compareTicketRefs(
        ref({ id: "2" }),
        { ...FIXTURE_PHASE.tickets[0]!, phase: "done", index: 5 },
        ref({ id: "3" }),
        null,
      ),
    ).toBeLessThan(0);
  });

  it("orders numeric ids before lexical ids, each ascending", () => {
    expect(compareTicketRefs(ref({ id: "9" }), null, ref({ id: "42" }), null)).toBeLessThan(0);
    expect(compareTicketRefs(ref({ id: "42" }), null, ref({ id: "ENG-7" }), null)).toBeLessThan(0);
    expect(compareTicketRefs(ref({ id: "ENG-7" }), null, ref({ id: "ENG-9" }), null)).toBeLessThan(0);
  });

  it("writes an id the way its tracker does", () => {
    expect(ticketIdLabel({ id: "42" })).toBe("#42");
    expect(ticketIdLabel({ id: "ENG-123" })).toBe("ENG-123");
  });

  it("names every provider and kind from a table over the wire enum", () => {
    expect(providerLabel("gitea")).toBe("Gitea");
    expect(providerLabel("github")).toBe("GitHub");
    expect(providerLabel("linear")).toBe("Linear");
    expect(ticketKindLabel("pull_request")).toBe("pull request");
  });

  // SUPERSEDED CONTRACT, updated deliberately rather than worked around: `open`
  // used to be `default`, the loudest variant, and that was right while the
  // badge was the only mark on the row. The glyph now carries the state in
  // shape AND colour, so a solid pill beside it made three tickets shout one
  // fact twice — the same defect the fleet card had with `active` + `working`.
  // The badge is now the redundant WORD that survives greyscale, and it is
  // quiet; nothing on this card claims `default`.
  it("never spends the loudest variant, and never guesses", () => {
    expect(ticketStatusTone("open")).toBe("outline");
    expect(ticketStatusTone("OPEN")).toBe("outline");
    expect(ticketStatusTone("merged")).toBe("secondary");
    expect(ticketStatusTone("closed")).toBe("secondary");
    // A tracker's own vocabulary is open-ended; an unknown word is still marked
    // and still spelled, just not claimed to mean something.
    expect(ticketStatusTone("in review")).toBe("outline");
  });
});

describe("merging a live read onto a stored ref", () => {
  it("leaves the cached ref alone when nothing resolved", () => {
    const cached = ref();

    expect(mergeTicket(cached, undefined)).toEqual(cached);
  });

  it("never blanks a cached field with a live null", () => {
    const merged = mergeTicket(ref(), ref({ title: null, url: null, status: null }));

    expect(merged.title).toBe("Widgets render twice on resize");
    expect(merged.url).toBe("https://tracker.example/acme/api/issues/42");
    expect(merged.status).toBe("open");
  });

  it("never lets a tracker clear the uncertainty flag", () => {
    // The resolve route fetches by id, so it reports `ambiguous: false` for
    // everything. Taking that answer would silently erase the one signal asking
    // the user to confirm the link.
    expect(mergeTicket(ref({ ambiguous: true }), ref({ ambiguous: false })).ambiguous).toBe(true);
  });

  it("takes the tracker's answer for everything else", () => {
    const merged = mergeTicket(ref({ status: "open" }), ref({ status: "closed", assignee: "kim" }));

    expect(merged.status).toBe("closed");
    expect(merged.assignee).toBe("kim");
  });
});
