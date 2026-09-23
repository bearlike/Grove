import { describe, expect, it, vi } from "vitest";
import type { WorkspaceHistoryView } from "@/lib/grove/api";
import {
  filterHistory,
  historyTimeline,
} from "@/lib/grove/adapters/history-timeline";

const recorded: WorkspaceHistoryView = {
  name: null,
  names: [
    {
      title: "Release preparation",
      description: "Keep the complete description",
      recorded_at: "2026-09-14T16:24:00Z",
    },
    {
      title: "Documentation polish",
      description: null,
      recorded_at: "2026-09-13T16:24:00Z",
    },
  ],
  progress: [
    {
      phase: "handoff",
      blocked: false,
      note: "Published the release",
      ticket_key: null,
      recorded_at: "2026-09-14T16:42:00Z",
    },
    {
      phase: "verify",
      blocked: true,
      note: "Waiting for preview access",
      ticket_key: "gitea:128",
      recorded_at: "2026-09-14T16:30:00Z",
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

describe("recorded history chronology", () => {
  it("interleaves every source by actual time without mutating the wire", () => {
    const input = structuredClone(recorded);
    const events = historyTimeline(input);
    expect(events.map((e) => e.kind)).toEqual([
      "progress",
      "progress",
      "name",
      "ticket",
      "name",
    ]);
    expect(events.map((e) => e.recordedAt)).toEqual([
      "2026-09-14T16:42:00Z",
      "2026-09-14T16:30:00Z",
      "2026-09-14T16:24:00Z",
      "2026-09-14T16:18:00Z",
      "2026-09-13T16:24:00Z",
    ]);
    expect(input).toEqual(recorded);
    expect(events.find((e) => e.kind === "ticket")?.entry).toEqual(
      recorded.tickets![0],
    );
    expect(events.find((e) => e.kind === "name")?.entry).toEqual(
      recorded.names![0],
    );
  });

  it("compares instants rather than timestamp spellings", () => {
    const input = structuredClone(recorded);
    input.names![0]!.recorded_at = "2026-09-14T18:45:00+02:00";
    expect(historyTimeline(input)[0]?.entry).toMatchObject({
      title: "Release preparation",
    });
  });

  it("preserves wire ordering within ties and deterministically orders sources", () => {
    const input = structuredClone(recorded);
    input.names = input.names!.map((n) => ({
      ...n,
      recorded_at: "2026-09-14T16:42:00Z",
    }));
    const events = historyTimeline(input);
    expect(events.slice(0, 3).map((e) => e.kind)).toEqual([
      "progress",
      "name",
      "name",
    ]);
    expect(
      events.filter((e) => e.kind === "name").map((e) => e.entry.title),
    ).toEqual(["Release preparation", "Documentation polish"]);
  });

  it("retains a single name snapshot and tolerates absent collections", () => {
    expect(historyTimeline({ name: null })).toEqual([]);
    expect(historyTimeline({ names: [recorded.names![0]!] })).toHaveLength(1);
  });

  it("retains unknown phases, null notes and invalid dates without crashing", () => {
    const event = {
      ...recorded.progress![0]!,
      phase: "future-phase",
      note: null,
      recorded_at: "not-a-date",
    };
    const events = historyTimeline({ ...recorded, progress: [event] });
    expect(events.at(-1)?.entry).toEqual(event);
    expect(filterHistory(events, "all", "future-phase")).toHaveLength(1);
  });

  it("keeps existing identities stable when a new event arrives", () => {
    const ids = historyTimeline(recorded).map((e) => e.id);
    expect(ids).toHaveLength(5);
    const input = structuredClone(recorded);
    input.progress!.unshift({
      ...input.progress![0]!,
      note: "Newer report",
      recorded_at: "2026-09-14T17:00:00Z",
    });
    expect(
      historyTimeline(input)
        .slice(1)
        .map((e) => e.id),
    ).toEqual(ids);
  });

  it("keeps a ticket identity when its last-seen observation refreshes", () => {
    const before = historyTimeline(recorded).find(
      (event) => event.kind === "ticket",
    )!;
    const input = structuredClone(recorded);
    input.tickets![0]!.last_seen = "2026-09-14T17:00:00Z";
    const after = historyTimeline(input).find(
      (event) => event.kind === "ticket",
    )!;
    expect(after.id).toBe(before.id);
  });
});

describe("history search", () => {
  it("combines type filtering with case insensitive trimmed search", () => {
    const events = historyTimeline(recorded);
    expect(filterHistory(events, "progress", " GITEA:128 ")).toHaveLength(1);
    expect(filterHistory(events, "name", "complete description")).toHaveLength(
      1,
    );
    expect(filterHistory(events, "ticket", "128")).toHaveLength(1);
    expect(filterHistory(events, "all", "blocked")).toHaveLength(1);
    expect(filterHistory(events, "name", "release")).toHaveLength(1);
    expect(filterHistory(events, "all", "unmatched")).toEqual([]);
    expect(filterHistory(events, "all", " ")).toEqual(events);
  });

  it("searches the whole history including entries beyond the initial rendering batch", () => {
    const progress = Array.from({ length: 1000 }, (_, index) => ({
      ...recorded.progress![0]!,
      note: `Report ${index}`,
      recorded_at: new Date(Date.UTC(2026, 8, 14, 0, index)).toISOString(),
    }));
    const events = historyTimeline({ progress });
    expect(
      filterHistory(events, "all", "Report 0").map((e) => e.entry),
    ).toEqual([progress[0]]);
    expect(filterHistory(events, "all", "")).toHaveLength(1000);
  });

  it("matches ASCII identifiers independently of the browser locale", () => {
    const localeLowerCase = vi
      .spyOn(String.prototype, "toLocaleLowerCase")
      .mockImplementation(function (this: string) {
        return this.replaceAll("I", "ı").replaceAll("i", "i");
      });
    try {
      const events = historyTimeline({
        tickets: [{ ...recorded.tickets![0]!, ticket_key: "GITEA:ISSUE" }],
      });
      expect(filterHistory(events, "all", "gitea:issue")).toHaveLength(1);
    } finally {
      localeLowerCase.mockRestore();
    }
  });
});
