import { describe, expect, it } from "vitest";

import { messagesFromTurns } from "@/lib/grove/adapters";
import type { SessionTurnView, ToolCallView } from "@/lib/grove/api";

function tool(tool_use_id: string): ToolCallView {
  return {
    name: "Bash",
    tool_use_id,
    status: "ok",
    input: null,
    input_truncated: false,
    result: null,
    result_truncated: false,
    duration_ms: null,
  };
}

function toolEntry(tool_use_id: string): SessionTurnView["entries"][number] {
  return {
    role: "tool",
    text: "Bash true",
    question: null,
    file_edit: null,
    todo: null,
    tool: tool(tool_use_id),
  };
}

function toolParts(turns: readonly SessionTurnView[]) {
  return messagesFromTurns(turns).flatMap((message) =>
    Array.isArray(message.content)
      ? message.content.filter((part) => part.type === "tool-call")
      : [],
  );
}

describe("messagesFromTurns tool-call ids", () => {
  // The FIRST occurrence must keep the provider's own id, not merely differ
  // from the second. That id is what keeps a tool expander the user opened
  // open as the transcript grows, so a fix that renamed both — or renamed the
  // wrong one — would satisfy "the ids are unique" and still lose the thing
  // uniqueness was protecting.
  it("disambiguates duplicate provider ids within one turn, keeping the first", () => {
    const parts = toolParts([
      { user_text: "go", started_at: null, entries: [toolEntry("duplicate"), toolEntry("duplicate")] },
    ]);

    expect(parts).toHaveLength(2);
    expect(parts[0]?.toolCallId).toBe("duplicate");
    expect(parts[1]?.toolCallId).not.toBe("duplicate");
  });

  it("disambiguates duplicate provider ids across turns, keeping the first", () => {
    const parts = toolParts([
      { user_text: "first", started_at: null, entries: [toolEntry("duplicate")] },
      { user_text: "second", started_at: null, entries: [toolEntry("duplicate")] },
    ]);

    expect(parts).toHaveLength(2);
    expect(parts[0]?.toolCallId).toBe("duplicate");
    expect(parts[1]?.toolCallId).not.toBe("duplicate");
  });

  it("passes a provider id through byte-identical when it is unique", () => {
    const [part] = toolParts([
      { user_text: "go", started_at: null, entries: [toolEntry("toolu_01Unique")] },
    ]);

    expect(part?.toolCallId).toBe("toolu_01Unique");
  });

  it("keeps the positional fallback when a tool has no provider detail", () => {
    const [part] = toolParts([
      {
        user_text: "go",
        started_at: null,
        entries: [{ role: "tool", text: "Bash true", question: null, file_edit: null, todo: null }],
      },
    ]);

    expect(part?.toolCallId).toBe("grove-msg-1-0");
  });
});
