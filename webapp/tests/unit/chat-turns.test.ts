import { describe, it, expect } from "vitest";
import {
  chatItemToThreadMessage,
  chatItemsFromTurns,
  latestTodoFromTurns,
} from "@/lib/grove/chat-turns";
import type { AgentQuestionView, SessionTurnView, TodoListView } from "@/lib/grove/types";

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

  it("maps a file_edit entry to a standalone file-edit item carrying the payload", () => {
    const items = chatItemsFromTurns([
      turn("edit it", [
        {
          role: "file_edit",
          text: "Edit lib/foo.ts",
          file_edit: {
            path: "/repo/lib/foo.ts",
            display_path: "lib/foo.ts",
            old_text: "const a = 1;",
            new_text: "const a = 2;",
          },
        },
      ]),
    ]);
    expect(items[1]).toEqual({
      kind: "file-edit",
      path: "/repo/lib/foo.ts",
      displayPath: "lib/foo.ts",
      oldText: "const a = 1;",
      newText: "const a = 2;",
    });
  });

  it("never folds a file-edit into a surrounding tool run — it stays standalone", () => {
    // The core product invariant: a file edit is ALWAYS visible, never swallowed
    // into the collapsible "Used N tools" accordion. It also breaks the tool run
    // like any other rendered non-tool item.
    const items = chatItemsFromTurns([
      turn("go", [
        { role: "tool", text: "Read lib/foo.ts" },
        {
          role: "file_edit",
          text: "Edit lib/foo.ts",
          file_edit: {
            path: "/repo/lib/foo.ts",
            display_path: "lib/foo.ts",
            old_text: "a",
            new_text: "b",
          },
        },
        { role: "tool", text: "Bash npm test" },
      ]),
    ]);
    expect(items.slice(1)).toEqual([
      { kind: "tools", calls: [{ name: "Read", detail: "lib/foo.ts" }] },
      {
        kind: "file-edit",
        path: "/repo/lib/foo.ts",
        displayPath: "lib/foo.ts",
        oldText: "a",
        newText: "b",
      },
      { kind: "tools", calls: [{ name: "Bash", detail: "npm test" }] },
    ]);
  });

  it("a file_edit entry with no payload degrades to a quiet note, never a throw", () => {
    const items = chatItemsFromTurns([
      turn("go", [{ role: "file_edit", text: "Edit lib/foo.ts" }]),
    ]);
    expect(items[1]).toEqual({ kind: "note", tone: "status", text: "Edit lib/foo.ts" });
  });
});

const question = (group_id: string, id: string): AgentQuestionView => ({
  id,
  group_id,
  kind: "single_select",
  prompt: "Pick",
  header: null,
  options: [{ label: "A", description: null }],
  multiselect: false,
  answered: false,
  answer: null,
  source_tool: "AskUserQuestion",
});

describe("chatItemToThreadMessage", () => {
  it("maps a user prompt to a native text part keyed by its flat index", () => {
    const msg = chatItemToThreadMessage({ kind: "message", role: "user", text: "hi" }, 0);
    expect(msg.role).toBe("user");
    expect(msg.id).toBe("item-0");
    expect(msg.content).toEqual([{ type: "text", text: "hi" }]);
    expect(msg.metadata?.custom).toEqual({ kind: "message" });
    // User messages carry no status (assistant-only in assistant-ui).
    expect(msg.status).toBeUndefined();
  });

  it("maps an agent reply to a completed text part carrying its text in custom (for copy)", () => {
    const msg = chatItemToThreadMessage({ kind: "message", role: "assistant", text: "done" }, 3);
    expect(msg.role).toBe("assistant");
    expect(msg.id).toBe("item-3");
    expect(msg.content).toEqual([{ type: "text", text: "done" }]);
    expect(msg.status).toEqual({ type: "complete", reason: "stop" });
    expect(msg.metadata?.custom).toEqual({ kind: "message", text: "done" });
  });

  it("maps a tool run to ONE data-tools part carrying the whole calls array", () => {
    const calls = [
      { name: "Edit", detail: "a.ts" },
      { name: "Bash", detail: "npm test" },
    ];
    const msg = chatItemToThreadMessage({ kind: "tools", calls }, 1);
    expect(msg.role).toBe("assistant");
    expect(msg.content).toEqual([{ type: "data-tools", data: { calls } }]);
  });

  it("maps note / notification / question kinds to their own data-* parts", () => {
    expect(
      chatItemToThreadMessage({ kind: "note", tone: "status", text: "compacting" }, 2).content,
    ).toEqual([{ type: "data-note", data: { tone: "status", text: "compacting" } }]);
    expect(
      chatItemToThreadMessage({ kind: "notification", summary: "s", detail: "d" }, 2).content,
    ).toEqual([{ type: "data-notification", data: { summary: "s", detail: "d" } }]);
    const q = question("g-1", "q-1");
    expect(chatItemToThreadMessage({ kind: "question", question: q }, 2).content).toEqual([
      { type: "data-question", data: { question: q } },
    ]);
  });

  it("maps a file-edit item to its own data-file-edit part", () => {
    expect(
      chatItemToThreadMessage(
        {
          kind: "file-edit",
          path: "/repo/lib/foo.ts",
          displayPath: "lib/foo.ts",
          oldText: "a",
          newText: "b",
        },
        2,
      ).content,
    ).toEqual([
      {
        type: "data-file-edit",
        data: { path: "/repo/lib/foo.ts", displayPath: "lib/foo.ts", oldText: "a", newText: "b" },
      },
    ]);
  });

  it("maps a continuation head to a data-continuation marker", () => {
    expect(chatItemToThreadMessage({ kind: "continuation" }, 4).content).toEqual([
      { type: "data-continuation", data: {} },
    ]);
  });
});

describe("todo entries (#184)", () => {
  const list = (n: string): TodoListView => ({
    items: [{ content: n, status: "in_progress", active_form: null }],
  });

  it("never renders a todo entry inline — it's pinned above the composer", () => {
    // A todo entry has non-empty text (its summary), so guard it isn't picked up
    // by the default-note fallback: it must drop out of the flat item stream.
    const items = chatItemsFromTurns([
      turn("plan it", [
        { role: "assistant", text: "Here's the plan." },
        { role: "todo", text: "0/1 done · Ship it", todo: list("Ship it") },
      ]),
    ]);
    expect(items).toEqual([
      { kind: "message", role: "user", text: "plan it" },
      { kind: "message", role: "assistant", text: "Here's the plan." },
    ]);
  });

  it("latestTodoFromTurns returns the newest todo across turns", () => {
    const first = list("first plan");
    const second = list("second plan");
    const latest = latestTodoFromTurns([
      turn("a", [{ role: "todo", text: "…", todo: first }]),
      turn("b", [
        { role: "assistant", text: "revising" },
        { role: "todo", text: "…", todo: second },
      ]),
    ]);
    expect(latest).toBe(second);
  });

  it("degrades to null when no turn carries a todo", () => {
    expect(latestTodoFromTurns([turn("a", [{ role: "assistant", text: "hi" }])])).toBeNull();
    // A malformed todo entry (role but no payload) is ignored, never crashes.
    expect(latestTodoFromTurns([turn("a", [{ role: "todo", text: "x" }])])).toBeNull();
  });
});
