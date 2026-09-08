import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { WorkspaceHistoryView } from "@/lib/grove/api";
import { historyTimeline } from "@/lib/grove/adapters/history-timeline";
import { HistoryFeed } from "@/components/grove/workspace/history-feed";

const history: WorkspaceHistoryView = {
  name: null,
  names: [
    {
      title: "Release preparation",
      description: "The entire title description remains visible to the reader.",
      recorded_at: "2026-09-14T16:24:00Z",
    },
  ],
  progress: [
    {
      phase: "done",
      blocked: false,
      note: "Published the release and verified the preview.",
      ticket_key: null,
      recorded_at: "2026-09-14T16:42:00Z",
    },
    {
      phase: "future-phase",
      blocked: true,
      note: null,
      ticket_key: "gitea:128",
      recorded_at: "not-a-date",
    },
  ],
  tickets: [
    {
      ticket_key: "gitea:128",
      provider: "gitea",
      ticket_id: "128",
      kind: "issue",
      first_seen: "2026-09-14T16:18:00Z",
      last_seen: "2026-09-14T16:42:00Z",
    },
  ],
};

function render(
  view: WorkspaceHistoryView = history,
  ticketUrls: ReadonlyMap<string, string> = new Map(),
): string {
  return renderToStaticMarkup(
    <HistoryFeed events={historyTimeline(view)} ticketUrls={ticketUrls} />,
  );
}

describe("recorded history feed", () => {
  it("renders one semantic chronology with local date headings and complete recorded details", () => {
    const markup = render();

    expect(markup).toContain(">2026-09-14</h3>");
    expect(markup).toContain('data-testid="history-event"');
    expect(markup).toContain("Reported Done");
    expect(markup).toContain("Name recorded");
    expect(markup).toContain("Ticket first recorded");
    expect(markup).toContain("Published the release and verified the preview.");
    expect(markup).toContain("The entire title description remains visible to the reader.");
    expect(markup).toContain("gitea:128");
    expect(markup).toContain("Issue");
    expect(markup).toContain("Last observed");
  });

  it("opens only resolved http ticket URLs in a new tab", () => {
    const markup = render(
      history,
      new Map([["gitea:128", "https://tracker.example/issues/128"]]),
    );

    expect(markup).toContain('href="https://tracker.example/issues/128"');
    expect(markup).toContain('>Ticket first recorded · <a');
    expect(markup).toContain('>gitea:128</a>');
    expect(markup).toContain('target="_blank"');
    expect(markup).toContain('rel="noopener noreferrer"');
    expect(markup).toContain("underline decoration-dotted underline-offset-2");
    expect(markup).toContain('aria-description="Opens in a new tab"');
  });

  it("keeps unresolved ticket keys as text", () => {
    const markup = render(history, new Map([["gitea:128", "javascript:alert(1)"]]));

    expect(markup).not.toContain("javascript:alert");
    expect(markup).toContain("gitea:128");
  });

  it("keeps phase marks top-aligned and semantically marked", () => {
    const markup = render();

    expect(markup).toContain("history-feed-rail col-start-2 row-start-1 row-span-2 flex min-h-full justify-start");
    expect(markup).toContain('data-tone="done"');
    expect(markup).toContain('data-tone="blocked"');
  });

  it("renders all timestamp ages from the feed's shared clock", () => {
    const markup = render();

    expect(markup).not.toContain('data-testid="precise-age"');
    expect(markup).toContain("history-event-time");
    expect(markup).toContain("grid-cols-[7rem_1rem_minmax(0,1fr)]");
    expect(markup).toContain("whitespace-nowrap");
  });

  it("makes exact timestamps reachable while retaining unknown historical values", () => {
    const markup = render();

    expect(markup).not.toContain('tabindex="0"');
    expect(markup).toContain('aria-label="Recorded not-a-date"');
    expect(markup).toContain('title="not-a-date"');
    expect(markup).toContain("Unknown time");
    expect(markup).toContain("future-phase");
    expect(markup).toContain("Blocked");
  });

  it("renders the frozen filter and batching contract before mounting interactive browser state", () => {
    const entries = Array.from({ length: 51 }, (_, index) => ({
      recorded_at: `2026-09-14T${String(Math.floor(index / 60)).padStart(2, "0")}:${String(index % 60).padStart(2, "0")}:00Z`,
      phase: "implementing",
      blocked: false,
      note: `Recorded update ${index}`,
      ticket_key: null,
    }));
    const markup = render({ progress: entries });

    expect(markup).toContain('aria-label="Search history"');
    for (const label of ["All", "Progress", "Names", "Tickets"]) {
      expect(markup).toContain(`>${label}</button>`);
    }
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('aria-pressed="false"');
    expect(markup.match(/data-testid="history-event"/g)).toHaveLength(50);
    expect(markup).toContain("Showing 50 of 51 recorded events");
    expect(markup).toContain(">Show more</button>");
  });
});
