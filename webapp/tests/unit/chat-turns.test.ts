import { describe, it, expect } from "vitest";
import { chatItemsFromTurns } from "@/lib/grove/chat-turns";
import type { SessionTurnView } from "@/lib/grove/types";

const turn = (user_text: string, entries: SessionTurnView["entries"]): SessionTurnView => ({
  user_text,
  started_at: "2026-06-11T10:00:00Z",
  entries,
});

describe("chatItemsFromTurns", () => {
  it("flattens a turn into a user message followed by its entries", () => {
    const items = chatItemsFromTurns([
      turn("build the panel", [
        { role: "assistant", text: "Starting on it." },
        { role: "tool", text: "Edit app/page.tsx" },
      ]),
    ]);
    expect(items).toEqual([
      { kind: "message", role: "user", text: "build the panel" },
      { kind: "message", role: "assistant", text: "Starting on it." },
      { kind: "tools", calls: [{ name: "Edit", detail: "app/page.tsx" }] },
    ]);
  });

  it("collapses consecutive tool entries into one tools item, in order", () => {
    const items = chatItemsFromTurns([
      turn("go", [
        { role: "tool", text: "Read lib/grove/hooks.ts" },
        { role: "tool", text: "Bash npm test" },
        { role: "tool", text: "Edit app/page.tsx" },
      ]),
    ]);
    expect(items).toEqual([
      { kind: "message", role: "user", text: "go" },
      {
        kind: "tools",
        calls: [
          { name: "Read", detail: "lib/grove/hooks.ts" },
          { name: "Bash", detail: "npm test" },
          { name: "Edit", detail: "app/page.tsx" },
        ],
      },
    ]);
  });

  it("does not merge tool runs across an intervening message or note", () => {
    const items = chatItemsFromTurns([
      turn("go", [
        { role: "tool", text: "Read a.ts" },
        { role: "assistant", text: "Now editing." },
        { role: "tool", text: "Edit a.ts" },
        { role: "status", text: "compacting" },
        { role: "tool", text: "Bash npm test" },
      ]),
    ]);
    expect(items).toEqual([
      { kind: "message", role: "user", text: "go" },
      { kind: "tools", calls: [{ name: "Read", detail: "a.ts" }] },
      { kind: "message", role: "assistant", text: "Now editing." },
      { kind: "tools", calls: [{ name: "Edit", detail: "a.ts" }] },
      { kind: "note", tone: "status", text: "compacting" },
      { kind: "tools", calls: [{ name: "Bash", detail: "npm test" }] },
    ]);
  });

  it("never merges tool runs across turns — the turn head sits between them", () => {
    const items = chatItemsFromTurns([
      turn("go", [{ role: "tool", text: "Read a.ts" }]),
      turn("again", [{ role: "tool", text: "Edit a.ts" }]),
    ]);
    expect(items).toEqual([
      { kind: "message", role: "user", text: "go" },
      { kind: "tools", calls: [{ name: "Read", detail: "a.ts" }] },
      { kind: "message", role: "user", text: "again" },
      { kind: "tools", calls: [{ name: "Edit", detail: "a.ts" }] },
    ]);
  });

  it("a dropped empty entry does not break a tool run", () => {
    const items = chatItemsFromTurns([
      turn("go", [
        { role: "tool", text: "Read a.ts" },
        { role: "assistant", text: "" },
        { role: "tool", text: "Edit a.ts" },
      ]),
    ]);
    expect(items).toEqual([
      { kind: "message", role: "user", text: "go" },
      {
        kind: "tools",
        calls: [
          { name: "Read", detail: "a.ts" },
          { name: "Edit", detail: "a.ts" },
        ],
      },
    ]);
  });

  it("renders an empty user_text head as a continuation marker", () => {
    const items = chatItemsFromTurns([
      turn("", [{ role: "summary", text: "continued from a prior session" }]),
    ]);
    expect(items).toEqual([
      { kind: "continuation" },
      { kind: "note", tone: "summary", text: "continued from a prior session" },
    ]);
  });

  it("maps summary and status entries to notes with their tone", () => {
    const items = chatItemsFromTurns([
      turn("go", [
        { role: "status", text: "compacting" },
        { role: "summary", text: "did the thing" },
      ]),
    ]);
    expect(items.slice(1)).toEqual([
      { kind: "note", tone: "status", text: "compacting" },
      { kind: "note", tone: "summary", text: "did the thing" },
    ]);
  });

  it("splits a notification into the summary line and the remaining detail", () => {
    const items = chatItemsFromTurns([
      turn("go", [
        {
          role: "notification",
          text: "Background task completed: Explore\nFound 4 routes.\nAll green.",
        },
      ]),
    ]);
    expect(items[1]).toEqual({
      kind: "notification",
      summary: "Background task completed: Explore",
      detail: "Found 4 routes.\nAll green.",
    });
  });

  it("a single-line notification has empty detail", () => {
    const items = chatItemsFromTurns([
      turn("go", [{ role: "notification", text: "Background command finished: npm test" }]),
    ]);
    expect(items[1]).toEqual({
      kind: "notification",
      summary: "Background command finished: npm test",
      detail: "",
    });
  });

  it("a notification breaks a tool run like any rendered non-tool item", () => {
    const items = chatItemsFromTurns([
      turn("go", [
        { role: "tool", text: "Agent(Explore): map the webapp" },
        { role: "notification", text: "Background task completed: Explore" },
        { role: "tool", text: "Edit a.ts" },
      ]),
    ]);
    expect(items.slice(1)).toEqual([
      { kind: "tools", calls: [{ name: "Agent(Explore):", detail: "map the webapp" }] },
      {
        kind: "notification",
        summary: "Background task completed: Explore",
        detail: "",
      },
      { kind: "tools", calls: [{ name: "Edit", detail: "a.ts" }] },
    ]);
  });

  it("a one-word tool line becomes a name with empty detail", () => {
    const items = chatItemsFromTurns([turn("go", [{ role: "tool", text: "Bash" }])]);
    expect(items[1]).toEqual({ kind: "tools", calls: [{ name: "Bash", detail: "" }] });
  });

  it("splits a tool line on the first space only", () => {
    const items = chatItemsFromTurns([
      turn("go", [{ role: "tool", text: "Bash npm test -- --watch" }]),
    ]);
    expect(items[1]).toEqual({
      kind: "tools",
      calls: [{ name: "Bash", detail: "npm test -- --watch" }],
    });
  });

  it("drops empty-text entries", () => {
    const items = chatItemsFromTurns([turn("go", [{ role: "assistant", text: "" }])]);
    expect(items).toEqual([{ kind: "message", role: "user", text: "go" }]);
  });

  it("an unknown streamed role degrades to a quiet status note, never a throw", () => {
    const entries = [
      { role: "thinking", text: "hmm" },
    ] as unknown as SessionTurnView["entries"];
    const items = chatItemsFromTurns([turn("go", entries)]);
    expect(items[1]).toEqual({ kind: "note", tone: "status", text: "hmm" });
  });

  it("an entry-level user message renders as a user bubble", () => {
    const items = chatItemsFromTurns([turn("go", [{ role: "user", text: "interjection" }])]);
    expect(items[1]).toEqual({ kind: "message", role: "user", text: "interjection" });
  });

  it("empty input maps to no items", () => {
    expect(chatItemsFromTurns([])).toEqual([]);
  });

  it("maps a question entry to a question item carrying the structured payload", () => {
    const question = {
      id: "q-1",
      group_id: "g-1",
      kind: "single_select" as const,
      prompt: "Which migration strategy?",
      header: "Decision needed",
      options: [
        { label: "Big-bang cutover", description: "Faster, riskier" },
        { label: "Incremental", description: null },
      ],
      multiselect: false,
      answered: false,
      answer: null,
      source_tool: "AskUserQuestion",
    };
    const items = chatItemsFromTurns([
      turn("pick one", [{ role: "question", text: "Which migration strategy?", question }]),
    ]);
    expect(items[1]).toEqual({ kind: "question", question });
  });

  it("a question entry with no payload is dropped (render-hardening, not a throw)", () => {
    // The text-empty guard never runs for a question role (its prompt may be
    // empty); a payload-less question is the only drop case.
    const items = chatItemsFromTurns([
      turn("go", [{ role: "question", text: "orphaned" }]),
    ]);
    expect(items).toEqual([{ kind: "message", role: "user", text: "go" }]);
  });
});
