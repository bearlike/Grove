import { describe, expect, it } from "vitest";

import { withOptimisticSend } from "@/lib/grove/hooks";
import type { WorkspaceQueueView } from "@/lib/grove/api";

/**
 * The decision `useSendMessage` used to get wrong: append the just-sent text
 * to a TURN cache that deletes itself on the very next refetch (see the
 * docstring in `mutations.ts`). Pulled out here as a pure function so the
 * corrected target — the queue, and only when there is an honest cache entry
 * to append to — is pinned without a query client or a DOM.
 */

const SUPPORTED: WorkspaceQueueView = { messages: [], supported: true };

describe("withOptimisticSend", () => {
  it("appends the sent text onto a supported, previously-empty queue", () => {
    const next = withOptimisticSend(SUPPORTED, "keep going", "2026-08-11T02:50:00Z");

    expect(next?.messages).toEqual([
      { text: "keep going", sent_at: "2026-08-11T02:50:00Z", position: 0 },
    ]);
  });

  it("positions a new message after whatever is already queued", () => {
    const primed: WorkspaceQueueView = {
      supported: true,
      messages: [{ text: "first", sent_at: "2026-08-11T02:00:00Z", position: 0 }],
    };

    const next = withOptimisticSend(primed, "second", "2026-08-11T02:01:00Z");

    expect(next?.messages.map((m) => m.position)).toEqual([0, 1]);
    expect(next?.messages.map((m) => m.text)).toEqual(["first", "second"]);
  });

  it("returns undefined unchanged when the queue was never fetched — nothing honest to draw", () => {
    expect(withOptimisticSend(undefined, "keep going", "2026-08-11T02:50:00Z")).toBeUndefined();
  });

  it("leaves an UNSUPPORTED queue untouched rather than inventing a row", () => {
    const unsupported: WorkspaceQueueView = { messages: [], supported: false };

    expect(withOptimisticSend(unsupported, "keep going", "2026-08-11T02:50:00Z")).toBe(unsupported);
  });
});
