import { describe, expect, it } from "vitest";

// The LEAF module, not the `@/lib/grove/runtime` barrel. The barrel re-exports
// `./thread`, whose import chain reaches jsdom — which does not load on this
// host at all and takes the whole file's collection down with it, reporting
// ZERO tests rather than a failure. This module is pure by construction, so
// importing it directly is both the fix and the point.
import { echoLanded, type SendEcho } from "@/lib/grove/runtime/sending";
import type { SessionTurnView, WorkspaceQueueView } from "@/lib/grove/api";

/**
 * Pins when a just-sent prompt stops being "in flight".
 *
 * The rule is the risky half of the send echo: clear too eagerly and the row
 * disappears back into silence, clear too late and it sits above the composer
 * duplicating a message already on screen. `useGroveThread` around it is
 * bookkeeping.
 *
 * Node environment, no renderer: jsdom does not load on this host at all
 * (`webidl.util.markAsUncloneable is not a function`, thrown from undici), and
 * a file that asks for it collects ZERO tests while still looking green. That
 * is why every rule worth pinning here is a pure function.
 *
 * NEVER WRITE THE ENVIRONMENT PRAGMA IN PROSE. Vitest greps the whole file for
 * it, so merely *naming* the jsdom pragma in a comment — as this very comment
 * was about to — switches the file to jsdom and kills it. The tell is
 * `setup 0ms` in the run summary: the file died before the setup file ran, so
 * the failure is environment resolution, not anything in the test.
 */

const ECHO: SendEcho = { text: "run the tests", sentAt: "2026-08-11T12:00:00.000Z" };

function turn(userText: string, startedAt: string | null): SessionTurnView {
  return { user_text: userText, started_at: startedAt, entries: [] };
}

function queue(...texts: string[]): WorkspaceQueueView {
  return {
    supported: true,
    messages: texts.map((text, position) => ({ text, sent_at: ECHO.sentAt, position })),
  } as WorkspaceQueueView;
}

describe("echoLanded", () => {
  it("has not landed while neither the transcript nor the queue shows it", () => {
    expect(echoLanded(ECHO, [turn("something else", null)], queue())).toBe(false);
  });

  it("lands when the BUSY agent's harness queues it", () => {
    // The message never becomes a transcript turn while the agent is working —
    // the harness holds it — so the queue is the only place it can surface.
    expect(echoLanded(ECHO, [], queue("run the tests"))).toBe(true);
  });

  it("lands when the IDLE agent takes it straight into a turn", () => {
    expect(echoLanded(ECHO, [turn("run the tests", "2026-08-11T12:00:01.000Z")], null)).toBe(true);
  });

  it("is NOT landed by an identical prompt sent earlier", () => {
    // The case the timestamp guard exists for: re-sending "run the tests" must
    // not be cleared instantly by the previous send's turn, which is already
    // in the transcript. Without this, the second send is silent — exactly the
    // complaint the echo answers.
    expect(echoLanded(ECHO, [turn("run the tests", "2026-08-11T11:59:00.000Z")], null)).toBe(false);
  });

  it("treats an undated turn as a landing rather than pinning the echo forever", () => {
    // A turn with no recorded start cannot be placed in time. A stale echo
    // stuck above the composer is the more visible of the two failures.
    expect(echoLanded(ECHO, [turn("run the tests", null)], null)).toBe(true);
  });

  it("tolerates a transcript and a queue that have not loaded yet", () => {
    expect(echoLanded(ECHO, undefined, undefined)).toBe(false);
  });
});
