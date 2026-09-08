import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  ChecklistMeter,
  PhaseMeter,
  TicketProgressMeter,
} from "@/components/grove/workspace/phase-meter";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * A TASK TRACK IS A SEQUENCE, NOT A STACK OF STATUS ROWS.
 *
 * A docked panel needs every checkpoint labelled. The compact labels below the
 * container threshold preserve that vocabulary, while the card header owns the
 * current phase summary and a blocked report retains its reached position.
 */
function phase(override: Partial<typeof FIXTURE_PHASE> = {}): string {
  return renderToStaticMarkup(
    <PhaseMeter phase={{ ...FIXTURE_PHASE, ...override }} />,
  );
}

function slot(html: string, testId: string): string {
  const match = html.match(new RegExp(`<[^>]*data-testid="${testId}"[^>]*>`));
  if (!match) throw new Error(`no ${testId} in markup`);
  return match[0];
}

describe("PhaseMeter", () => {
  it("renders no invented first step when the agent has made no task claim", () => {
    expect(renderToStaticMarkup(<PhaseMeter phase={null} />)).toBe("");
  });

  it("keeps all six checkpoints in one horizontal track at every card width", () => {
    const html = phase();
    const track = html.match(
      /<ol class="([^"]*)" aria-label="Task phase">/,
    )?.[1];

    expect(track).toContain("flex");
    expect(track).not.toContain("flex-col");
    expect(track).not.toContain("flex-wrap");
    expect(html.match(/<li /g)).toHaveLength(6);
    expect(html.match(/bg-border/g)).toHaveLength(5);
  });

  it("keeps abbreviated and full labels behind their task-container thresholds", () => {
    const html = phase();
    const short = html.match(/<span class="([^"]*?)">Deliver<\/span>/)?.[1];
    const full = html.match(/<span class="([^"]*?)">Delivering<\/span>/)?.[1];

    expect(short).toContain("text-[10px]");
    expect(short).toContain("@min-[420px]/task:hidden");
    expect(full).toContain("hidden");
    expect(full).toContain("@min-[420px]/task:block");
    expect(full).toContain("font-bold");
  });

  it("preserves the phase while removing a blank report region", () => {
    const html = phase({ note: "   " });

    expect(slot(html, "phase-meter")).toContain('data-phase="delivering"');
    expect(html).toContain("Deliver");
    expect(html).not.toContain('data-testid="phase-note"');
  });

  it("keeps report prose in an enclosure that can wrap rather than crop", () => {
    const html = phase({
      note: "A detailed report that must remain readable at any width.",
    });
    const note = slot(html, "phase-note");

    expect(note).toContain("overflow-hidden");
    expect(note).not.toMatch(/\b(?:h-|min-h-|max-h-)/);
    expect(html).toContain(
      "A detailed report that must remain readable at any width.",
    );
  });

  it("marks blocked as a flag with words and an octagon, never a seventh phase", () => {
    const html = phase({ blocked: true, note: "Waiting for review" });

    expect(slot(html, "phase-meter")).toContain('data-blocked="true"');
    expect(html.match(/<li /g)).toHaveLength(6);
    expect(html).toContain('data-testid="phase-blocked"');
    expect(html).toContain("lucide-octagon-alert");
    expect(html).toContain("blocked");
    expect(html).toContain("Waiting for review");
  });

  it("still states a block with no note, rather than suppressing the warning with its region", () => {
    const html = phase({ blocked: true, note: "   " });

    expect(html).not.toContain('data-testid="phase-note"');
    expect(html).toContain('data-testid="phase-blocked"');
    expect(html).toContain("lucide-octagon-alert");
  });

  it("does not retain the obsolete population chips or second ticket report list", () => {
    const html = phase();

    expect(html).not.toContain("Ticket reports");
    expect(html).not.toContain("ticket-phase-counts");
    expect(html).not.toContain("phase-ticket-reports");
  });
});

describe("ChecklistMeter", () => {
  it("renders no fabricated zero-total meter", () => {
    expect(renderToStaticMarkup(<ChecklistMeter todo={null} />)).toBe("");
    expect(
      renderToStaticMarkup(
        <ChecklistMeter
          todo={{ total: 0, completed: 0, in_progress: 0, pending: 0 }}
        />,
      ),
    ).toBe("");
  });

  it("states the completed count, accessible percentage, and remaining work", () => {
    const html = renderToStaticMarkup(
      <ChecklistMeter
        todo={{ total: 8, completed: 3, in_progress: 2, pending: 3 }}
      />,
    );

    expect(html).toContain('data-testid="checklist-meter"');
    expect(html).toContain('data-testid="checklist-count"');
    expect(html).toContain("3 / 8 done");
    expect(html).toContain('aria-valuenow="38"');
    expect(html).toContain("2 in progress · 3 pending");
  });
});

describe("TicketProgressMeter", () => {
  const ROLLUP = {
    total: 4,
    reported: 3,
    unreported: 1,
    done: 1,
    blocked: 1,
    phases: {
      scoping: 0,
      planning: 1,
      implementing: 0,
      verifying: 1,
      delivering: 0,
      done: 1,
    },
    fraction: 0.45,
  } as const;

  it("labels the aggregate as phase progress and attributes its arithmetic", () => {
    const html = renderToStaticMarkup(<TicketProgressMeter rollup={ROLLUP} />);
    const percent = slot(html, "rollup-percent");

    expect(html).toContain('data-testid="ticket-rollup"');
    expect(html).toContain("Average ticket phase progress");
    expect(percent).toContain("decoration-dashed");
    expect(percent).toContain("title=");
    expect(html).toContain('data-testid="rollup-coverage"');
  });
});
