import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WatchesPanel } from "@/components/grove/workspace/watches-panel";
import { orderWatches, watchSubject } from "@/lib/grove/adapters";
import type { WatchView } from "@/lib/grove/api";

/**
 * The card exists to answer one question per watch — is it still running, did
 * it fire, or did it run out of time — so these pin that every state reaches
 * the page as its own WORD, that running watches lead, and that a workspace
 * with no watches draws no card at all.
 */

const WS = "a".repeat(32);

function watch(overrides: Partial<WatchView> = {}): WatchView {
  return {
    id: `wch_${"0".repeat(32)}`,
    recipient: { workspace_id: WS, agent: "" },
    predicate: { kind: "ci", provider: "gitea", owner: "acme", repo: "widgets", head_sha: "abc1234def5678" },
    state: "pending",
    note: "",
    every: "PT30S",
    created_at: "2026-09-23T12:00:00Z",
    expires_at: "2026-09-23T12:15:00Z",
    next_due: "2026-09-23T12:00:30Z",
    settled_at: null,
    outcome: null,
    receipt: null,
    receipt_detail: null,
    ...overrides,
  };
}

function renderOpen(watches: WatchView[]): string {
  return renderToStaticMarkup(<WatchesPanel watches={{ watches }} defaultOpen />);
}

describe("WatchesPanel", () => {
  it("draws no card for a workspace that has registered no watch", () => {
    expect(renderToStaticMarkup(<WatchesPanel watches={{ watches: [] }} />)).toBe("");
  });

  it("names running, fired and expired in words, never by hue alone", () => {
    const html = renderOpen([
      watch({ id: `wch_${"1".repeat(32)}`, state: "pending" }),
      watch({
        id: `wch_${"2".repeat(32)}`,
        state: "fired",
        settled_at: "2026-09-23T12:05:00Z",
        outcome: { ok: true, summary: "All 4 checks passed.", url: null },
      }),
      watch({
        id: `wch_${"3".repeat(32)}`,
        state: "expired",
        settled_at: "2026-09-23T12:15:00Z",
        outcome: { ok: false, summary: "Deadline reached: the watch condition was NOT met.", url: null },
      }),
    ]);

    for (const state of ["pending", "fired", "expired"]) {
      expect(html).toContain(`data-state="${state}"`);
    }
    expect(html).toContain(">Running<");
    expect(html).toContain(">Fired<");
    expect(html).toContain(">Expired<");
    expect(html).toContain("1 running · 1 fired · 1 expired");
    // A running watch says when it gives up; a settled one shows its outcome.
    expect(html).toContain("Gives up");
    expect(html).toContain("All 4 checks passed.");
    expect(html).toContain("Deadline reached");
  });

  it("keeps cancelled and undeliverable as themselves rather than folding them in", () => {
    const html = renderOpen([
      watch({ id: `wch_${"4".repeat(32)}`, state: "cancelled", settled_at: "2026-09-23T12:01:00Z" }),
      watch({ id: `wch_${"5".repeat(32)}`, state: "undeliverable", settled_at: "2026-09-23T12:02:00Z" }),
    ]);
    expect(html).toContain(">Cancelled<");
    expect(html).toContain(">Undeliverable<");
    expect(html).not.toContain(">Fired<");
  });

  it("shows a running watch's note, which is what tells several apart", () => {
    const html = renderOpen([watch({ note: "PR #12 checks" })]);
    expect(html).toContain("PR #12 checks");
  });
});

describe("orderWatches", () => {
  it("puts every running watch ahead of every settled one, keeping daemon order within each", () => {
    // Newest-first from the daemon, with the running one OLDEST — the case a
    // plain pass-through would bury at the bottom.
    const settledNew = watch({ id: `wch_${"a".repeat(32)}`, state: "fired" });
    const settledOld = watch({ id: `wch_${"b".repeat(32)}`, state: "expired" });
    const running = watch({ id: `wch_${"c".repeat(32)}`, state: "pending" });

    expect(orderWatches([settledNew, settledOld, running]).map((row) => row.id)).toEqual([
      running.id,
      settledNew.id,
      settledOld.id,
    ]);
  });
});

describe("watchSubject", () => {
  it("names each kind of subject, shortening a commit to seven characters", () => {
    expect(watchSubject(watch().predicate)).toBe("CI on acme/widgets@abc1234");
    expect(watchSubject({ kind: "command", argv: ["make", "test"], terminal_exit_codes: [0], workspace_id: WS })).toBe(
      "Command: make test",
    );
    expect(watchSubject({ kind: "timer", at: "2026-09-23T13:00:00Z" })).toBe("Timer");
  });
});
