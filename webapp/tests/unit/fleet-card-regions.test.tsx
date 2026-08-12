import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusBadge, AgentStateBadge, TicketChip, TodoBadge } from "@/components/grove/fleet/badges";
import { WorkspaceCard } from "@/components/grove/fleet/workspace-card";
import type { AgentState, WorkspaceActivity, WorkspaceStatus } from "@/components/grove/fleet/types";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TicketRef } from "@/lib/grove/api";
import { workspace } from "@/tests/fixtures/fleet";

/**
 * The fleet card's SHAPE and its COLOUR, which are one change and two failures.
 *
 * The shape failure: tickets, counters and the agent name shared a single
 * `flex-wrap` row, so a five-ticket card wrapped its chips out of the middle of
 * a run of figures and read as a different component from a one-ticket card.
 * Wrapping is a property of a set; three sets in one row cannot each wrap
 * correctly, and no type or lint can see it.
 *
 * The colour failure: every mark rendered in the same grey, so nothing on the
 * card said what was in flight and what was finished.
 *
 * Both are pinned through the RENDERED MARKUP rather than through the tables,
 * because both bugs were in the composition and the tables were already right.
 */

const ticket = (over: Partial<TicketRef> = {}): TicketRef => ({
  provider: "gitea",
  id: "498",
  kind: "issue",
  title: "The ticket's title",
  url: "https://example.invalid/issues/498",
  status: "open",
  assignee: null,
  ambiguous: false,
  ...over,
});

/**
 * The fleet fixture builds a bare workspace; these two widen it with the fields
 * the card's later regions read. Done here rather than in `tests/fixtures` so
 * this suite owns its own inputs — the fixture module is shared.
 */
function withTickets(base: WorkspaceActivity, refs: readonly TicketRef[]): WorkspaceActivity {
  return { ...base, state: { ...base.state, ticket_refs: [...refs] } };
}

function withTodo(base: WorkspaceActivity, completed: number, total: number): WorkspaceActivity {
  return {
    ...base,
    todo: { total, completed, in_progress: 0, pending: total - completed },
  };
}

/** The agent's current sentence, and the workspace's failure — both live on the
 * session's activity, so they are set the same way. */
function withActivity(
  base: WorkspaceActivity,
  over: Partial<WorkspaceActivity["sessions"][number]["activity"]>,
): WorkspaceActivity {
  const [first, ...rest] = base.sessions;
  return { ...base, sessions: [{ ...first, activity: { ...first.activity, ...over } }, ...rest] };
}

const card = (ws: WorkspaceActivity): string =>
  renderToStaticMarkup(
    <TooltipProvider>
      <WorkspaceCard workspace={ws} repoName="Grove" />
    </TooltipProvider>,
  );

/** Every `data-testid="card-*"` region, in the order the markup emits it. */
const regions = (html: string): string[] =>
  [...html.matchAll(/data-testid="(card-[a-z]+)"/g)].map((match) => match[1]);

describe("the card body is named regions, not one flow", () => {
  const busy = withTodo(
    withTickets(
      withActivity(workspace({ id: "w1", state: "working" }), {
        current_task: "Add a token-bucket rate limiter to the API gateway middleware.",
      }),
      [
        ticket({ id: "498" }),
        ticket({ id: "500" }),
        ticket({ provider: "linear", id: "ENG-233", status: "merged", kind: "pull_request" }),
        ticket({ provider: "github", id: "12", status: "closed" }),
        ticket({ provider: "github", id: "13", status: null }),
      ],
    ),
    2,
    5,
  );
  const quiet = workspace({ id: "w2" });

  it("renders the same regions in the same order however much a card carries", () => {
    // The defect this replaces: the ticket set and the counter set were the
    // same row, so "which region is this figure in" had no answer.
    expect(regions(card(busy))).toEqual([
      "card-marks",
      "card-task",
      "card-metrics",
      "card-tickets",
    ]);

    // A card with nothing attached keeps the ORDER and drops the regions with
    // nothing in them — it is shorter, not differently arranged.
    expect(regions(card(quiet))).toEqual(["card-marks", "card-metrics"]);
  });

  it("keeps every ticket out of the counter row", () => {
    const html = card(busy);
    const metrics = html.indexOf('data-testid="card-metrics"');
    const tickets = html.indexOf('data-testid="card-tickets"');

    expect(metrics).toBeGreaterThan(-1);
    expect(tickets).toBeGreaterThan(metrics);
    // Five chips, all of them in the tickets region and none before it.
    expect(html.slice(tickets).match(/data-testid="ticket-chip"/g)).toHaveLength(5);
    expect(html.slice(0, tickets)).not.toContain('data-testid="ticket-chip"');
  });

  it("pins the ledger to the card's floor so a row of cards lines up", () => {
    // `CardGrid` stretches every card in a row to the tallest; without this the
    // counters sit at whatever height the task above them happened to end.
    expect(card(busy)).toContain("mt-auto");
  });

  it("renders the error as its own region, above the ledger", () => {
    const failed = workspace({ id: "w3" });
    const html = card({
      ...failed,
      state: { ...failed.state, error_detail: "the agent exited 127" },
    });

    expect(regions(html)).toEqual(["card-marks", "card-error", "card-metrics"]);
    expect(html).toContain('role="alert"');
  });
});

/**
 * `progressAccent` is unit-tested next door; what a table cannot say is whether
 * the badge actually WEARS it, which is where the greyscale card came from.
 */
describe("the todo count is the one figure that carries a hue", () => {
  const todo = (completed: number, total: number): string =>
    renderToStaticMarkup(
      <TodoBadge todo={{ total, completed, in_progress: 0, pending: total - completed }} />,
    );

  it("reads amber in flight and green complete", () => {
    expect(todo(2, 5)).toContain("bg-warning");
    expect(todo(5, 5)).toContain("bg-success");
  });

  it("stays neutral before anything has been done", () => {
    // Zero of six is not progress; colouring it would claim work had begun.
    const html = todo(0, 6);
    expect(html).not.toContain("bg-warning");
    expect(html).not.toContain("bg-success");
  });

  it("survives the colour being removed", () => {
    // §4.7 — `--success` and `--destructive` are 12/255 apart in greyscale, so
    // the glyph and the fraction have to carry it alone.
    const html = todo(5, 5);
    expect(html).toContain("☑");
    expect(html).toContain("5/5");
  });
});

describe("a ticket chip marks its STATE and never its identity", () => {
  const chip = (over: Partial<TicketRef> = {}): string =>
    renderToStaticMarkup(<TicketChip ticket={ticket(over)} />);

  it("colours the glyph per the forge convention", () => {
    expect(chip({ status: "open" })).toContain("text-success");
    expect(chip({ status: "merged", kind: "pull_request" })).toContain("text-merged");
    expect(chip({ status: "closed" })).toContain("text-destructive");
    expect(chip({ status: null })).toContain("text-content-tertiary");
  });

  it("never tones the chip itself, whatever the tracker says", () => {
    // The chip is the ticket's IDENTITY — a fixed property, which §6 forbids a
    // tone. It also keeps a five-ticket row clear of the three-toned cap.
    for (const status of ["open", "merged", "closed", "draft", null]) {
      expect(chip({ status }), `status=${status}`).toContain('data-variant="outline"');
    }
  });

  it("spells the state out for a reader who cannot see the glyph", () => {
    expect(chip({ status: "merged", kind: "pull_request" })).toContain("pull request, merged");
  });

  it("shows the string the fleet search matches on", () => {
    // `matchesQuery` searches `provider#id`; a chip printing anything else
    // would show a name that cannot be pasted back into the filter.
    expect(chip({ provider: "linear", id: "ENG-233" })).toContain("linear#ENG-233");
  });

  it("is a plain chip, not a dead link, when the ref has no URL", () => {
    expect(chip({ url: null })).not.toContain("<a ");
  });
});

describe("the accents reach the badges, and only where the table says", () => {
  const IN_FLIGHT: readonly AgentState[] = ["starting", "working"];
  const RESTING: readonly AgentState[] = ["waiting", "blocked", "idle", "error", "unknown"];

  it("accents exactly the in-flight agent states", () => {
    for (const state of IN_FLIGHT) {
      const html = renderToStaticMarkup(<AgentStateBadge state={state} />);
      expect(html, state).toContain("bg-warning");
    }
    for (const state of RESTING) {
      const html = renderToStaticMarkup(<AgentStateBadge state={state} />);
      expect(html, state).not.toContain("bg-warning");
    }
  });

  it("accents provisioning and nothing else on the status axis", () => {
    const statuses: readonly WorkspaceStatus[] = [
      "active",
      "running",
      "provisioning",
      "idle",
      "paused",
      "offline",
      "orphaned",
      "error",
    ];
    for (const status of statuses) {
      const html = renderToStaticMarkup(<StatusBadge status={status} />);
      if (status === "provisioning") expect(html, status).toContain("bg-warning");
      else expect(html, status).not.toContain("bg-warning");
    }
  });

  it("never puts two loud marks in the card's mark row", () => {
    // One agent state is one value of one union, so amber and `destructive`
    // can never both land; the phase and runtime chips are `outline` by table.
    // The header's status badge holds the object's only `default`.
    for (const state of [...IN_FLIGHT, ...RESTING]) {
      const html = card(workspace({ id: `w-${state}`, state }));
      const marks = html.slice(html.indexOf('data-testid="card-marks"'));
      const row = marks.slice(0, marks.indexOf('data-testid="card-metrics"'));

      const toned =
        (row.match(/data-variant="(default|destructive|secondary)"/g) ?? []).length +
        (row.match(/bg-warning|bg-success/g) ?? []).length;
      expect(toned, `${state}: ${row}`).toBeLessThanOrEqual(1);
    }
  });
});
