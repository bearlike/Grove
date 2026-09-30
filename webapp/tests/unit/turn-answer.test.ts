import { describe, expect, it } from "vitest";

import { GROVE_TIME_GAP, GROVE_TURN_ANSWER, messagesFromTurns } from "@/lib/grove/adapters";
import type { DigestEntryView, SessionTurnView } from "@/lib/grove/api";
import { turnAnswerOf } from "@/lib/grove/runtime/feedback";

const STARTED = "2026-06-09T08:00:00Z";

function entry(role: DigestEntryView["role"], text: string): DigestEntryView {
  return { role, text } as DigestEntryView;
}

function answers(turn: SessionTurnView): string[] {
  return messagesFromTurns([turn]).flatMap((message) => {
    const startedAt = turnAnswerOf(message.metadata?.custom ?? {});
    return startedAt ? [`${JSON.stringify(message.content)}@${startedAt}`] : [];
  });
}

describe("the turn answer marker", () => {
  it("rides only the LAST assistant text, never an earlier one or a note", () => {
    // Two assistant texts with a status note after: a bare "last message" or
    // "first assistant" rule picks the wrong row.
    const marked = answers({
      user_text: "go",
      started_at: STARTED,
      entries: [entry("assistant", "thinking aloud"), entry("assistant", "done"), entry("status", "idle")],
    });
    expect(marked).toEqual([`${JSON.stringify([{ type: "text", text: "done" }])}@${STARTED}`]);
  });

  it("is absent when the turn reported no start time — there is nothing to rate it by", () => {
    expect(answers({ user_text: "go", started_at: null, entries: [entry("assistant", "done")] })).toEqual([]);
  });

  it("never lands on a continuation head, which is an assistant DATA message", () => {
    const messages = messagesFromTurns([{ user_text: "", started_at: STARTED, entries: [] }]);
    expect(messages.some((m) => m.metadata?.custom?.[GROVE_TURN_ANSWER])).toBe(false);
  });

  it("coexists with the time separator, which rides the turn HEAD, not the answer", () => {
    const messages = messagesFromTurns([
      { user_text: "go", started_at: STARTED, entries: [entry("assistant", "done")] },
    ]);
    const [head, answer] = messages;
    expect(head?.metadata?.custom?.[GROVE_TIME_GAP]).toBe(true);
    expect(answer?.metadata?.custom?.[GROVE_TURN_ANSWER]).toBe(STARTED);
  });
});
