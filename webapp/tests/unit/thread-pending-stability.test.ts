import { describe, expect, it } from "vitest";

import { pendingContentKey, type PendingQuestionGroup } from "@/lib/grove/runtime/thread";

/**
 * Pins the half of the pending-stability fix that can actually be wrong.
 *
 * `snapshot` is a brand-new object on every ~1Hz SSE frame, so the `pending`
 * array `useGroveThread` derives from it is new every tick even when nothing
 * pending changed — and `transcript.tsx` lists `pending` in `ThreadPane`'s
 * memo deps, so an unstable reference defeats the "hold the thread still"
 * memo for as long as a question is on screen. `useStableByKey` fixes that by
 * holding the previous array whenever this key repeats.
 *
 * THE KEY IS THE RISKY HALF, which is why it is what this suite covers.
 * `useStableByKey` is four lines of ref bookkeeping that cannot plausibly
 * misbehave; a key that omitted a rendered field would instead pin a STALE
 * question card on screen for the life of the batch — a silent wrong answer
 * rather than a crash.
 *
 * Tested as a pure function rather than through a renderer ON PURPOSE: this
 * suite runs `environment: "node"` (vitest.config.ts:12) and jsdom does not
 * load at all under this Node build (`webidl.util.markAsUncloneable is not a
 * function`, thrown from undici via jsdom's api.js). A `@vitest-environment
 * jsdom` file here does not fail loudly — it collects ZERO tests and reports
 * an unhandled error, which reads like coverage while pinning nothing.
 */

/** A group carrying only the fields the key is allowed to look at. */
function group(groupId: string, prompt: string): PendingQuestionGroup {
  return {
    groupId,
    questions: [],
    presentation: { groupId, prompt },
  } as unknown as PendingQuestionGroup;
}

describe("pendingContentKey", () => {
  it("is identical for two ticks carrying the same batch", () => {
    // The poll-tick case: a structurally identical batch arrives in fresh
    // objects every second. Same key ⇒ `useStableByKey` holds the old array.
    expect(pendingContentKey([group("g1", "Pick a branch")])).toBe(
      pendingContentKey([group("g1", "Pick a branch")]),
    );
  });

  it("changes when a question's rendered content changes under a stable id", () => {
    // The dangerous case. Were the key the group id alone, the card would go
    // on rendering the OLD prompt for as long as the batch lived.
    expect(pendingContentKey([group("g1", "Pick a branch")])).not.toBe(
      pendingContentKey([group("g1", "Pick a base branch")]),
    );
  });

  it("changes when a batch appears, grows or resolves", () => {
    const none = pendingContentKey([]);
    const one = pendingContentKey([group("g1", "a")]);
    const two = pendingContentKey([group("g1", "a"), group("g2", "b")]);

    expect(new Set([none, one, two]).size).toBe(3);
  });

  it("distinguishes two groups that differ only by id", () => {
    expect(pendingContentKey([group("g1", "same prompt")])).not.toBe(
      pendingContentKey([group("g2", "same prompt")]),
    );
  });
});
