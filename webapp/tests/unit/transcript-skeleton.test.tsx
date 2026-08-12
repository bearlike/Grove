import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TranscriptSkeleton } from "@/components/grove/workspace/transcript-skeleton";

/**
 * The transcript's loading state, pinned against the two wrong screens it
 * replaces.
 *
 * Before this component existed, a workspace or session-detail page with a
 * transcript still in flight fell through to `Thread`'s own zero-message
 * state — "How can I help you today?" plus a live composer — because "no
 * turns yet" and "no turns, period" both hand back a message count of zero.
 * These assertions pin the two properties that make `TranscriptSkeleton` a
 * legitimate stand-in instead: it looks like a conversation (several bars,
 * alternating alignment), and it never claims to be either of the states it
 * is not — not the new-session welcome, and not a failure.
 */
const markup = renderToStaticMarkup(<TranscriptSkeleton />);

describe("the transcript skeleton", () => {
  it("is marked as a loading placeholder", () => {
    expect(markup).toContain('data-testid="transcript-skeleton"');
    expect(markup).toContain('data-source="loading"');
  });

  it("stands in several turns, not one grey block", () => {
    const bars = markup.match(/data-slot="skeleton"/g) ?? [];
    expect(bars.length).toBeGreaterThan(3);
  });

  it("never claims to be the new-session welcome or a composer", () => {
    expect(markup).not.toContain("How can I help you today");
    expect(markup).not.toContain("Send a message");
    expect(markup).not.toContain("aui_composer-shell");
  });

  it("never claims to be an error", () => {
    expect(markup).not.toContain('role="alert"');
  });
});
