import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { QueuePanel } from "@/components/grove/workspace/queue-panel";
import type { WorkspaceQueueView } from "@/lib/grove/api";

/**
 * The card's whole job is telling three states apart that a naive
 * `messages.length === 0` check would conflate: nothing waiting, a harness
 * Grove cannot see into at all, and a real backlog. The first two render
 * identically (no card) but for different, explicit reasons in the code —
 * these pin that both guards fire, not just that the pixels match.
 */

function render(queue: WorkspaceQueueView): string {
  return renderToStaticMarkup(<QueuePanel queue={queue} />);
}

const SENT_AT = "2026-08-11T02:50:00Z";

describe("QueuePanel", () => {
  it("renders no card at all for an empty, SUPPORTED queue", () => {
    expect(render({ messages: [], supported: true })).toBe("");
  });

  it("renders no card at all for an UNSUPPORTED harness, even with a stray message", () => {
    // The wire contract says an unsupported queue is always an empty tuple,
    // but the guard must not rely on that — it checks `supported` on its own.
    expect(
      render({
        messages: [{ text: "should never happen", sent_at: SENT_AT, position: 0 }],
        supported: false,
      }),
    ).toBe("");
  });

  it("renders the card OPEN once there is a real backlog", () => {
    // Open, not collapsed: this card only exists when something is waiting, and
    // the complaint it answers was "I send a steer message and never see it". A
    // shut disclosure would reproduce that bug with a card in front of it.
    const html = render({
      messages: [{ text: "keep going", sent_at: SENT_AT, position: 0 }],
      supported: true,
    });

    expect(html).toContain('data-testid="queue-card"');
    expect(html).toContain('data-collapsed="false"');
    expect(html).toContain("Queued");
    expect(html).toContain("1 message waiting");
    expect(html).toContain("keep going");
  });

  it("gives the card a header — a decorative glyph plus a tint-and-rule boundary", () => {
    // "Queued" already says what this is, so the glyph carries no name of its
    // own (redundant with the label would be worse than no glyph at all).
    const html = render({
      messages: [{ text: "keep going", sent_at: SENT_AT, position: 0 }],
      supported: true,
    });
    const trigger = html.match(/<button[^>]*data-slot="collapsible-trigger"[^>]*>/)?.[0];

    expect(html).toContain("lucide-messages-square");
    expect(html).toMatch(/<svg[^>]*class="[^"]*lucide-messages-square[^"]*"[^>]*aria-hidden="true"/);
    expect(trigger).toBeTruthy();
    expect(trigger).toContain("bg-muted/40");
    // The rule only has something to divide once the list is open.
    expect(html).toContain("border-t");
  });

  it("pluralizes the waiting count", () => {
    const html = render({
      messages: [
        { text: "first", sent_at: SENT_AT, position: 0 },
        { text: "second", sent_at: SENT_AT, position: 1 },
      ],
      supported: true,
    });

    expect(html).toContain("2 messages waiting");
  });

  it("carries every message's text and a relative wait time", () => {
    const html = render({
      messages: [{ text: "run the migration next", sent_at: SENT_AT, position: 0 }],
      supported: true,
    });

    expect(html).toContain('data-testid="queue-message"');
    expect(html).toContain("run the migration next");
    expect(html).toContain('data-testid="relative-time"');
    expect(html).toContain(`dateTime="${SENT_AT}"`);
  });

  it("wraps a long message rather than truncating it — it is a sentence, not a name", () => {
    const long =
      "This is a much longer steering instruction that a reader typed while the agent was busy, and clipping it would discard the half it was sent for.";
    const html = render({ messages: [{ text: long, sent_at: SENT_AT, position: 0 }], supported: true });

    expect(html).toContain(long);
    expect(html).not.toContain("truncate");
    expect(html).toContain("break-words");
  });

  it("renders 'unknown' rather than a timestamp when the harness reports no send time", () => {
    const html = render({
      messages: [{ text: "typed straight into the pane", sent_at: null, position: 0 }],
      supported: true,
    });

    expect(html).toContain("unknown");
  });
});
