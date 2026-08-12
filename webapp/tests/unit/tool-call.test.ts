import { describe, expect, it } from "vitest";

import {
  asToolCall,
  formatToolDuration,
  messagesFromTurns,
  toolCallFields,
  toolCallStatus,
  toolCallStatusLabel,
  type ToolCallView,
} from "@/lib/grove/adapters";
import type { SessionTurnView } from "@/lib/grove/api";

/** A settled Bash call, as the daemon sends one. */
const BASH: ToolCallView = {
  name: "Bash",
  tool_use_id: "toolu_01AAA",
  status: "ok",
  input: { command: "rg -n 'todo' src\ncd src && ls", description: "Search" },
  input_truncated: false,
  result: "src/a.ts:12: todo\n",
  result_truncated: false,
  duration_ms: 1450,
};

function turn(entries: SessionTurnView["entries"]): SessionTurnView[] {
  return [{ user_text: "go", started_at: null, entries }];
}

function toolParts(turns: SessionTurnView[]) {
  return messagesFromTurns(turns).flatMap((message) =>
    Array.isArray(message.content)
      ? message.content.filter((part) => part.type === "tool-call")
      : [],
  );
}

describe("toolCallFields", () => {
  it("passes a string through VERBATIM — a command must render as the lines that ran", () => {
    const [command] = toolCallFields(BASH.input);
    expect(command).toEqual({ name: "command", value: "rg -n 'todo' src\ncd src && ls" });
    // The failure this pins: JSON-encoding it would put a literal \n on screen.
    expect(command?.value).not.toContain("\\n");
  });

  it("pretty-prints anything that is not a string", () => {
    const fields = toolCallFields({ limit: 20, paths: ["a", "b"], deep: { on: true } });
    expect(fields[0]).toEqual({ name: "limit", value: "20" });
    expect(fields[1]?.value).toBe('[\n  "a",\n  "b"\n]');
    expect(fields[2]?.value).toContain('"on": true');
  });

  it("survives provider data no JSON encoder can handle", () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(() => toolCallFields(cyclic)).not.toThrow();
    expect(toolCallFields({ nothing: undefined })[0]?.value).toBe("undefined");
  });

  it("reads a null input and an empty map the same way — as no fields", () => {
    expect(toolCallFields(null)).toEqual([]);
    expect(toolCallFields({})).toEqual([]);
  });
});

describe("toolCallStatus", () => {
  it("forks three ways, and running is its own state", () => {
    expect(toolCallStatus("running")).toEqual({ type: "running" });
    expect(toolCallStatus("ok")).toEqual({ type: "complete" });
    expect(toolCallStatus("error")).toEqual({ type: "incomplete", reason: "error" });
  });

  it("carries no error payload — the provider's text IS the response body", () => {
    expect(toolCallStatus("error")).not.toHaveProperty("error");
  });

  it("names every state in words, because colour is never the only carrier", () => {
    expect(toolCallStatusLabel("running")).toBe("Running");
    expect(toolCallStatusLabel("error")).toBe("Failed");
    expect(toolCallStatusLabel("ok")).toBe("Done");
  });
});

describe("formatToolDuration", () => {
  it("withholds a duration it does not have rather than printing a zero", () => {
    expect(formatToolDuration(null)).toBeNull();
    expect(formatToolDuration(undefined)).toBeNull();
  });

  it("matches the vendored ramp step for step", () => {
    expect(formatToolDuration(0)).toBe("<1s");
    expect(formatToolDuration(999)).toBe("<1s");
    expect(formatToolDuration(1450)).toBe("1.4s");
    expect(formatToolDuration(9999)).toBe("9.9s");
    expect(formatToolDuration(12_400)).toBe("12s");
    expect(formatToolDuration(125_000)).toBe("2m 5s");
  });

  it("clamps a negative span — container clocks run ahead of the browser's", () => {
    expect(formatToolDuration(-5)).toBe("<1s");
  });
});

describe("asToolCall", () => {
  it("accepts the wire shape", () => {
    expect(asToolCall(BASH)).toBe(BASH);
  });

  it("refuses anything else rather than throwing inside a transcript row", () => {
    for (const junk of [null, undefined, "Bash", 3, {}, { name: "Bash" }]) {
      expect(asToolCall(junk)).toBeNull();
    }
  });

  it("refuses a status outside the union — a wire enum is untrusted at runtime", () => {
    expect(asToolCall({ ...BASH, status: "cancelled" })).toBeNull();
  });
});

describe("messagesFromTurns — the tool detail on the wire", () => {
  it("carries the whole ToolCallView across on the part's artifact", () => {
    const [part] = toolParts(
      turn([{ role: "tool", text: "Bash rg -n todo", question: null, file_edit: null, todo: null, tool: BASH }]),
    );
    expect(part).toMatchObject({ artifact: BASH, toolName: "Bash", result: BASH.result });
  });

  it("keeps the digest line's remainder as the collapsed summary", () => {
    const [part] = toolParts(
      turn([{ role: "tool", text: "Bash rg -n todo", question: null, file_edit: null, todo: null, tool: BASH }]),
    );
    expect(part).toMatchObject({ argsText: "rg -n todo" });
  });

  it("uses the provider's tool_use_id as the part id, so parallel calls stay addressable", () => {
    const parts = toolParts(
      turn([
        { role: "tool", text: "Bash a", question: null, file_edit: null, todo: null, tool: BASH },
        {
          role: "tool",
          text: "Read b",
          question: null,
          file_edit: null,
          todo: null,
          tool: { ...BASH, name: "Read", tool_use_id: "toolu_01BBB", status: "running", result: null, duration_ms: null },
        },
      ]),
    );
    expect(parts.map((p) => p.toolCallId)).toEqual(["toolu_01AAA", "toolu_01BBB"]);
  });

  it("leaves `result` UNSET for a running call and for one that returned nothing", () => {
    const parts = toolParts(
      turn([
        {
          role: "tool",
          text: "Bash sleep",
          question: null,
          file_edit: null,
          todo: null,
          tool: { ...BASH, tool_use_id: "r", status: "running", result: null, duration_ms: null },
        },
        {
          role: "tool",
          text: "Bash true",
          question: null,
          file_edit: null,
          todo: null,
          tool: { ...BASH, tool_use_id: "q", result: null },
        },
      ]),
    );
    // Identical natively — which is exactly why the renderer reads `artifact`
    // and never infers "running" from a missing result.
    expect(parts[0]).not.toHaveProperty("result");
    expect(parts[1]).not.toHaveProperty("result");
    expect(parts.map((p) => asToolCall(p.artifact)?.status)).toEqual(["running", "ok"]);
  });

  it("marks an error natively as well, without pattern-matching the body", () => {
    const [part] = toolParts(
      turn([
        {
          role: "tool",
          text: "Bash boom",
          question: null,
          file_edit: null,
          todo: null,
          tool: { ...BASH, status: "error", result: "command not found" },
        },
      ]),
    );
    expect(part).toMatchObject({ isError: true });
  });

  it("still renders a call from a provider that reports no detail", () => {
    const [part] = toolParts(
      turn([{ role: "tool", text: "Bash ls -la", question: null, file_edit: null, todo: null }]),
    );
    expect(part).toMatchObject({ toolName: "Bash", argsText: "ls -la" });
    expect(part).not.toHaveProperty("artifact");
    // The positional fallback still has to produce a unique id.
    expect(part?.toolCallId).toBeTruthy();
  });

  it("hands the invocation to a file-edit card too — the diff is the payload, not the call", () => {
    const [message] = messagesFromTurns(
      turn([
        {
          role: "file_edit",
          text: "Write a.py",
          question: null,
          todo: null,
          tool: { ...BASH, name: "Write", tool_use_id: "toolu_edit" },
          file_edit: { path: "/w/a.py", display_path: "a.py", old_text: "", new_text: "x\n" },
        },
      ]),
    ).filter((m) => Array.isArray(m.content) && m.content[0]?.type === "data-file-edit");
    expect(message?.content[0]).toMatchObject({ data: { tool: { tool_use_id: "toolu_edit" } } });
  });

  it("is still pure", () => {
    const turns = turn([
      { role: "tool", text: "Bash x", question: null, file_edit: null, todo: null, tool: BASH },
    ]);
    expect(messagesFromTurns(turns)).toEqual(messagesFromTurns(turns));
  });
});
