import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { AgentMessage } from "@/components/grove/workspace/agent-message";
import type { AgentMessageData } from "@/lib/grove/adapters/agent-message";

const message = (over: Partial<AgentMessageData> = {}): AgentMessageData => ({
  from: "a".repeat(32),
  to: "b".repeat(32),
  subject: "request",
  body: "Please review PR #42",
  ...over,
});

/**
 * The vendored element's own anatomy, which is what says it is composed rather
 * than reimplemented. Its documented root carries `data-slot="agent-handoff"`.
 */
const HANDOFF_SLOT = 'data-slot="agent-handoff"';

/**
 * BOTH envelope kinds draw the same element, and the kind is carried by a
 * testid rather than by a different component.
 *
 * Until 2026-09-15 a notice was a hand-composed row — a mail glyph, two raw ids
 * around an arrow — so the two sat within a few transcript rows of each other
 * looking like different features. They state the same shape of fact (something
 * addressed this session), so they take the same vocabulary.
 *
 * `handoff-agent` is on BOTH, and deliberately: it is the readability floor,
 * not the kind marker. Measured on the real card, the vendored dim reads
 * 4.33:1 — under the 4.5:1 minimum — so withholding it from notices would have
 * bought a tier distinction by making one sender unreadable.
 */
it("begins a peer delivery with the mailbox mark before its handoff details", () => {
  const html = renderToStaticMarkup(<AgentMessage message={message({ handoff: true })} />);
  const mailboxMark = html.indexOf('data-testid="mailbox-card-icon"');
  const handoff = html.indexOf(HANDOFF_SLOT);

  expect(mailboxMark).toBeGreaterThanOrEqual(0);
  expect(mailboxMark).toBeLessThan(handoff);
  expect(html).toContain('data-slot="app-icon-fallback"');
  expect(html).toContain("agent-handoff-summary");
  expect(html).not.toContain("agent-notice-summary");
});

it("draws a harness notice with the SAME element", () => {
  const html = renderToStaticMarkup(<AgentMessage message={message({ handoff: false })} />);
  expect(html).toContain(HANDOFF_SLOT);
  expect(html).toContain("agent-notice-summary");
  expect(html).not.toContain("agent-handoff-summary");
});

it("treats an absent flag as a notice, so an older daemon cannot claim a handoff", () => {
  const html = renderToStaticMarkup(<AgentMessage message={message()} />);
  expect(html).toContain("agent-notice-summary");
  expect(html).not.toContain("agent-handoff-summary");
});

it("keeps every sender pill above the contrast floor, whichever kind it is", () => {
  // The vendored element dims the sender to `text-foreground/45`, which reads
  // 4.33:1 on this card. `handoff-agent` is what lifts it, so both kinds carry
  // it — the ratio itself is a browser measurement, recorded on the component.
  for (const handoff of [true, false]) {
    const html = renderToStaticMarkup(<AgentMessage message={message({ handoff })} />);
    expect(html).toContain("handoff-agent");
  }
});

/**
 * The colour census EXEMPTS this element, so this is what guards the override.
 *
 * `elements-agent-handoff` bakes an "in transit" blue into its unsettled arrow
 * and recipient pill as literal JSX, and Grove always renders `settled={false}`
 * — so that blue is the permanent steady state, not a transient decoration.
 * `globals.css` repaints it under `.handoff-agent`, but the e2e census skips
 * this subtree (a class census reads emitted source, never what a later rule
 * paints over it), which leaves the override with nothing asserting it.
 *
 * Both halves are pinned here because either one alone is vacuous: that the
 * vendored blue is still emitted (the day upstream drops it, the CSS becomes
 * dead weight aimed at nothing) and that our rule still selects the elements
 * carrying it (the day the vendor reshuffles its layout, the selectors miss and
 * the blue silently returns). The RENDERED result stays a browser measurement.
 */
it("keeps the scoped override aimed at the vendored transit colour", () => {
  const html = renderToStaticMarkup(<AgentMessage message={message({ handoff: true })} />);
  const css = readFileSync(
    new URL("../../app/globals.css", import.meta.url),
    "utf8",
  );

  expect(html).toMatch(/(?:text|bg)-blue-/);
  expect(css).toContain('[data-slot="agent-handoff"].handoff-agent > div > svg');
  expect(css).toContain('[data-slot="agent-handoff"].handoff-agent > div > span:last-child');
});

/**
 * `settled` must stay false — measured, not preferred.
 *
 * The vendored element already dims the sender pill to `text-foreground/45`;
 * `settled` compounds a further `opacity-45` on top, which measured **1.78:1**
 * against the app's real dark theme (4.5:1 is the floor) and rendered the
 * sender's name unreadable. A source assertion is the right instrument here
 * because the value is a literal in our own composition — the CONTRAST that
 * justifies it can only be measured in a browser, and is recorded above.
 */
it("never compounds the sender pill's dim into an unreadable one", () => {
  const html = renderToStaticMarkup(<AgentMessage message={message({ handoff: true })} />);
  // The vendored element emits `opacity-45` on the from-pill only when settled.
  expect(html).toContain(HANDOFF_SLOT);
  expect(html).not.toContain("opacity-45");
});

it("shortens the ids in the pills", () => {
  const html = renderToStaticMarkup(<AgentMessage message={message({ handoff: true })} />);
  expect(html).toContain("aaaaaaaaaa");
  // The full 32-char id would make the pill unreadable.
  expect(html).not.toContain("a".repeat(32));
});

/**
 * The summary line is Grove's own words about the delivery, never the peer's.
 *
 * The disclosure is closed here and Radix does not render closed content, so
 * this can only assert the body's ABSENCE from the summary — which is exactly
 * the claim being made. That the body renders when opened is the disclosure's
 * own long-standing behaviour, covered by the existing mailbox tests.
 */
it("never lifts the peer's body into the handoff line", () => {
  const secret = "the egress test is flaky because DOCKER-USER is empty";
  const html = renderToStaticMarkup(
    <AgentMessage message={message({ handoff: true, body: secret })} />,
  );
  expect(html).toContain('data-testid="agent-handoff-summary"');
  expect(html).not.toContain(secret);
});
