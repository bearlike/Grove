import type { ThreadMessageLike } from "@assistant-ui/react";
import type { QuestionInteraction } from "./question-plan";
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
  // One historical entry from `/turns` — always read-only (epic #74).
  | { kind: "question"; question: AgentQuestionView }
  | { kind: "continuation" };

// The LIVE pending question GROUP (Gitea #111) is deliberately NOT a `ChatItem`:
// `ThreadPrimitive.Messages` keys its children by INDEX, so a live interactive
// card at the tail would remount (losing in-flight selections) every time the
// transcript grows under it. The panel renders it as a stable sibling AFTER the
// message stream instead — see `PendingQuestionGroup` and `useGroveChatRuntime`.
export interface PendingQuestionGroup {
  /** Every question sharing one `group_id`, answered atomically in one submit
   * (a real `AskUserQuestion` batch can carry more than one). */
  questions: AgentQuestionView[];
  /** The answer callbacks + submit state, live from the SSE snapshot. */
  interactive: QuestionInteraction;
}

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

// ─── assistant-ui bridge (Phase D, #141) ─────────────────────────────────────

/**
 * The `data-*` message-part NAMES Grove models its non-text transcript rows as.
 * assistant-ui strips the `data-` prefix when it normalizes a `DataPrefixedPart`
 * (`{type:"data-tools"}` → `{type:"data", name:"tools"}`), so the CONTENT part
 * type carries the prefix and the render registry (`data.by_name`) keys on the
 * bare name. This registry is the integration contract between the pure mapper
 * below and the `MessagePrimitive.Parts` renderers in `components/chat/` — one
 * entry per non-text `ChatItem` kind. Keep the two columns in lockstep.
 */
export const CHAT_DATA_PART = {
  tools: "data-tools",
  note: "data-note",
  notification: "data-notification",
  question: "data-question",
  continuation: "data-continuation",
} as const;

/** The bare `data.by_name` keys (the `data-` prefix stripped) — the render
 * registry side of {@link CHAT_DATA_PART}. */
export const CHAT_DATA_NAME = {
  tools: "tools",
  note: "note",
  notification: "notification",
  question: "question",
  continuation: "continuation",
} as const;

/** Data payload for a `data-tools` part — the pre-grouped digest run. */
export interface ToolsPartData {
  calls: ToolCall[];
}
/** Data payload for a `data-note` part — a quiet summary/status row. */
export interface NotePartData {
  tone: "summary" | "status";
  text: string;
}
/** Data payload for a `data-notification` part — summary + collapsible detail. */
export interface NotificationPartData {
  summary: string;
  detail: string;
}
/** Data payload for a `data-question` part — a read-only historical question. */
export interface QuestionPartData {
  question: AgentQuestionView;
}

/** A completed assistant status — every Grove transcript row is post-hoc, so
 * pinning `complete` keeps `thread.isRunning` off the last-message heuristic
 * (Grove sources the working state from `agentState`, not the message stream). */
const COMPLETE = { type: "complete", reason: "stop" } as const;

/**
 * Pure mapper from ONE `ChatItem` to the assistant-ui `ThreadMessageLike` the
 * external-store runtime converts — the render-layer twin of `chatItemsFromTurns`
 * (which stays the wire→item mapper). One `ChatItem` becomes one message: the
 * two text kinds (`message` user/assistant) map to a native `text` part; the
 * historical non-text kinds each map to their own `data-*` part (see
 * {@link CHAT_DATA_PART}), rendered by a house component via `data.by_name`. The
 * tool-digest group is ONE `data-tools` part carrying the whole `calls` array
 * (Grove pre-groups in the mapper above, so assistant-ui's part-grouping
 * machinery is never invoked — research §4 "Resolved"). The LIVE pending
 * question group is NOT mapped here — it renders as a sibling of the message
 * stream (see {@link PendingQuestionGroup}).
 *
 * `id` keys on the flat index so the store reconciles rather than remounts on a
 * pure append (earlier indices stay fixed). The per-turn timestamp is layered on
 * by the runtime (`createdAt`), not here — this stays a pure `(item, index)`
 * function so it unit-tests like `chatItemsFromTurns`.
 */
export function chatItemToThreadMessage(item: ChatItem, index: number): ThreadMessageLike {
  const custom = { kind: item.kind } as Record<string, unknown>;
  switch (item.kind) {
    case "message":
      return {
        role: item.role,
        content: [{ type: "text", text: item.text }],
        id: `item-${index}`,
        // Status is assistant-only; the user prompt carries none. Stashing the
        // assistant text in custom gives the copy affordance a source without
        // re-deriving it from parts.
        ...(item.role === "assistant"
          ? { status: COMPLETE, metadata: { custom: { ...custom, text: item.text } } }
          : { metadata: { custom } }),
      };
    case "tools":
      return dataMessage(index, CHAT_DATA_PART.tools, { calls: item.calls }, custom);
    case "note":
      return dataMessage(index, CHAT_DATA_PART.note, { tone: item.tone, text: item.text }, custom);
    case "notification":
      return dataMessage(
        index,
        CHAT_DATA_PART.notification,
        { summary: item.summary, detail: item.detail },
        custom,
      );
    case "question":
      return dataMessage(index, CHAT_DATA_PART.question, { question: item.question }, custom);
    case "continuation":
      return dataMessage(index, CHAT_DATA_PART.continuation, {}, custom);
  }
}

/** One assistant message wrapping a single `data-*` part — the shared shape for
 * every non-text `ChatItem` kind. */
function dataMessage(
  index: number,
  type: (typeof CHAT_DATA_PART)[keyof typeof CHAT_DATA_PART],
  data: unknown,
  custom: Record<string, unknown>,
): ThreadMessageLike {
  return {
    role: "assistant",
    content: [{ type, data }],
    id: `item-${index}`,
    status: COMPLETE,
    metadata: { custom },
  };
}
