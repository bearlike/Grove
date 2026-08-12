import { describe, expect, it } from "vitest";

import { GROVE_DATA_PART, messagesFromTurns } from "@/lib/grove/adapters";
import { TRANSCRIPT_TURNS } from "../fixtures/turns";

/** The `data-*` part type of a single-part message, for terse assertions. */
function partTypes(messages: ReturnType<typeof messagesFromTurns>): string[] {
  return messages.map((message) => {
    const content = message.content;
    if (typeof content === "string") return "text";
    return content[0]?.type ?? "empty";
  });
}

describe("messagesFromTurns", () => {
  const messages = messagesFromTurns(TRANSCRIPT_TURNS);

  it("opens each turn with its head — prompt, or a continuation marker when resumed", () => {
    expect(messages[0]).toMatchObject({ role: "user" });
    // Turn 2 carries no `user_text`; it must not silently vanish, or a resumed
    // session's transcript runs into the previous turn with no boundary.
    const continuations = partTypes(messages).filter((t) => t === GROVE_DATA_PART.continuation);
    expect(continuations).toHaveLength(1);
  });

  it("collapses a run of consecutive tool entries into ONE message with N parts", () => {
    // Two `tool` entries back to back — the vendored ToolGroup renderer groups
    // on parts within a message, so two messages would render two groups.
    const toolMessages = messages.filter(
      (m) => Array.isArray(m.content) && m.content.every((p) => p.type === "tool-call"),
    );
    expect(toolMessages).toHaveLength(1);
    expect(toolMessages[0].content).toHaveLength(2);
  });

  it("splits a tool digest line into name and args", () => {
    const tools = messages.flatMap((m) =>
      Array.isArray(m.content) ? m.content.filter((p) => p.type === "tool-call") : [],
    );
    expect(tools[0]).toMatchObject({ toolName: "Bash", argsText: "ls -la" });
    expect(tools[1]).toMatchObject({ toolName: "Read", argsText: "src/api/routes.py" });
  });

  it("gives every tool-call part a unique toolCallId", () => {
    const ids = messages.flatMap((m) =>
      Array.isArray(m.content)
        ? m.content.filter((p) => p.type === "tool-call").map((p) => p.toolCallId)
        : [],
    );
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("maps a file edit to a diff part carrying both paths", () => {
    const edit = messages.find(
      (m) => Array.isArray(m.content) && m.content[0]?.type === GROVE_DATA_PART.fileEdit,
    );
    expect(edit?.content[0]).toMatchObject({
      data: { displayPath: "src/api/health.py", oldText: "" },
    });
  });

  it("keeps todo entries OUT of the stream — the list is a pinned sibling", () => {
    // Two todo writes in the fixture. Either leaking in would stack near
    // duplicate cards down the transcript.
    expect(JSON.stringify(messages)).not.toContain("Mapping the router module");
  });

  it("drops blank-text entries rather than emitting empty bubbles", () => {
    const empties = messages.filter(
      (m) => Array.isArray(m.content) && m.content[0]?.type === "text" && m.content[0].text === "",
    );
    expect(empties).toHaveLength(0);
  });

  it("splits a notification into its summary line and detail body", () => {
    const note = messages.find(
      (m) => Array.isArray(m.content) && m.content[0]?.type === GROVE_DATA_PART.notification,
    );
    expect(note?.content[0]).toMatchObject({
      data: { summary: "Subagent finished", detail: "It reviewed 4 files and found no issues." },
    });
  });

  it("mints stable, unique ids so an append reconciles instead of remounting", () => {
    const ids = messages.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
    // Appending a turn must not renumber the messages already on screen.
    const grown = messagesFromTurns([...TRANSCRIPT_TURNS, { user_text: "next", started_at: null, entries: [] }]);
    expect(grown.slice(0, messages.length).map((m) => m.id)).toEqual(ids);
  });

  it("withholds createdAt for an unparseable timestamp rather than inventing one", () => {
    const [message] = messagesFromTurns([{ user_text: "hi", started_at: "not-a-date", entries: [] }]);
    expect(message.createdAt).toBeUndefined();
  });

  it("is pure — the same input twice yields deep-equal output", () => {
    expect(messagesFromTurns(TRANSCRIPT_TURNS)).toEqual(messages);
  });

  it("reuses an unchanged turn's messages BY REFERENCE when a window appends a tail", () => {
    // This is what makes the per-turn cache worth having: `mergeTurns`
    // (`adapters/turns.ts:90`) preserves the prefix turn OBJECTS across a poll
    // and only the tail is freshly parsed, so a poll tick must be able to skip
    // rebuilding everything before it.
    const before = messagesFromTurns(TRANSCRIPT_TURNS);
    const grownTurns = [...TRANSCRIPT_TURNS, { user_text: "next", started_at: null, entries: [] }];
    const after = messagesFromTurns(grownTurns);

    expect(after.length).toBeGreaterThan(before.length);
    // Every message object from the unchanged prefix is the SAME reference,
    // not merely deep-equal — that identity is what lets assistant-ui skip
    // re-rendering a message it recognizes.
    for (let i = 0; i < before.length; i++) {
      expect(after[i]).toBe(before[i]);
    }
    // Ids for that prefix are unchanged too, since nothing shifted in front of it.
    expect(after.slice(0, before.length).map((m) => m.id)).toEqual(before.map((m) => m.id));
  });

  it("rebuilds a turn instead of trusting the cache when the running index it was minted at shifts", () => {
    // A turn object that repeats but at a DIFFERENT starting index (its
    // predecessor's message count changed) must not be served from the cache
    // keyed at the old index — ids would silently collide with the new prefix.
    const repeatedTurn = TRANSCRIPT_TURNS[1];
    const asOnlyTurn = messagesFromTurns([repeatedTurn]); // minted from index 0
    const asSecondTurn = messagesFromTurns([TRANSCRIPT_TURNS[0], repeatedTurn]); // shifted
    const tail = asSecondTurn.slice(asSecondTurn.length - asOnlyTurn.length);
    // Same turn, different starting index: ids must continue from the prefix
    // in front of it, never repeat what `asOnlyTurn` minted in isolation.
    expect(tail.map((m) => m.id)).not.toEqual(asOnlyTurn.map((m) => m.id));
  });
});
