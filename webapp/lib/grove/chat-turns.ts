import type { AgentQuestionView, DigestEntryView, SessionTurnView } from "./types";

/**
 * Pure mapper from the wire's session turns to chat-panel render items —
 * the unit-test seam, kept apart from the React layer the same way
 * `applyDashboardEvent` is kept apart from `useActivityStream`.
 *
 * The wire delivers `SessionTurnView[]` oldest-first: a `user_text` prompt
 * (empty for a resumed/compacted session's head) plus ordered
 * `DigestEntryView` entries. The chat panel renders a flat list, so this
 * flattens — each item names its render shape (`message` → bubble,
 * `tools` → one collapsed tool-group block, `note` → quiet muted row,
 * `continuation` → marker). Consecutive tool entries within a turn collapse
 * into one `tools` item so a long Read/Bash/Edit run is one row, not N;
 * any rendered non-tool item breaks the run, and runs never merge across
 * turns (the turn head always renders between them).
 */
export interface ToolCall {
  name: string;
  detail: string;
}

export type ChatItem =
  | { kind: "message"; role: "user" | "assistant"; text: string }
  | { kind: "tools"; calls: ToolCall[] }
  | { kind: "note"; tone: "summary" | "status"; text: string }
  | { kind: "notification"; summary: string; detail: string }
  | { kind: "question"; question: AgentQuestionView }
  | { kind: "continuation" };

export function chatItemsFromTurns(turns: SessionTurnView[]): ChatItem[] {
  const items: ChatItem[] = [];
  for (const turn of turns) {
    if (turn.user_text) {
      items.push({ kind: "message", role: "user", text: turn.user_text });
    } else {
      // A resumed/compacted session's head — there was no fresh prompt.
      items.push({ kind: "continuation" });
    }
    for (const entry of turn.entries) {
      const item = itemFromEntry(entry);
      if (!item) continue;
      const prev = items[items.length - 1];
      if (item.kind === "tools" && prev?.kind === "tools") {
        // Consecutive tool calls fold into the open run. The turn head above
        // guarantees a run never spans turns.
        prev.calls.push(...item.calls);
      } else {
        items.push(item);
      }
    }
  }
  return items;
}

function itemFromEntry(entry: DigestEntryView): ChatItem | null {
  // A "question" entry carries its render payload in `question`, not `text`
  // (the prompt may be empty for a bare confirm), so it's handled before the
  // text-empty guard below that drops blank entries of every other role.
  if (entry.role === "question") {
    return entry.question ? { kind: "question", question: entry.question } : null;
  }
  if (!entry.text) return null;
  switch (entry.role) {
    case "user":
    case "assistant":
      return { kind: "message", role: entry.role, text: entry.text };
    case "tool": {
      // The wire carries one digest line per call, e.g. "Edit app/page.tsx" —
      // first token is the tool name, the remainder its target/arguments.
      const space = entry.text.indexOf(" ");
      const call: ToolCall =
        space === -1
          ? { name: entry.text, detail: "" }
          : { name: entry.text.slice(0, space), detail: entry.text.slice(space + 1) };
      return { kind: "tools", calls: [call] };
    }
    case "summary":
    case "status":
      return { kind: "note", tone: entry.role, text: entry.text };
    case "notification": {
      // A background-task notice the agent received: the wire packs one
      // summary line first, then (optionally) the subagent's full result on
      // the following lines — split once, render the rest behind a disclosure.
      const nl = entry.text.indexOf("\n");
      return nl === -1
        ? { kind: "notification", summary: entry.text, detail: "" }
        : {
            kind: "notification",
            summary: entry.text.slice(0, nl),
            detail: entry.text.slice(nl + 1).trim(),
          };
    }
    default:
      // Streamed data is untrusted at runtime: a role this client's union
      // predates degrades to a quiet note, never a throw (render-hardening rule).
      return { kind: "note", tone: "status", text: entry.text };
  }
}
