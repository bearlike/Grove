import type { ThreadMessageLike } from "@assistant-ui/react";

import type { AgentQuestionView, DigestEntryView, SessionTurnView } from "@/lib/grove/api";
import type { ToolCallView } from "./tool-call";

/**
 * Grove's wire transcript → assistant-ui messages.
 *
 * Pure by contract, not by taste: no React, no fetching, no clock. The turn's
 * own `started_at` is the only time that enters, and it enters as data. That is
 * what lets the whole mapping be exercised with zero daemon.
 *
 * Tool calls become NATIVE assistant-ui `tool-call` content parts rather than a
 * stringified digest blob, so the vendored `ToolFallback` / `ToolGroup`
 * renderers work on them unmodified (webapp/ packed them into one opaque data
 * part, which is issue #368).
 */

/** The `data-*` part types Grove mints for transcript rows assistant-ui has no
 * native shape for. assistant-ui strips the `data-` prefix when it normalizes a
 * part, so the CONTENT type carries the prefix and the render registry
 * (`data.by_name`) keys on the bare name in {@link GROVE_DATA_NAME}. The two
 * tables are one contract — move them together. */
export const GROVE_DATA_PART = {
  note: "data-note",
  notification: "data-notification",
  question: "data-question",
  fileEdit: "data-file-edit",
  continuation: "data-continuation",
  compaction: "data-compaction",
} as const;

/** The bare `data.by_name` render keys for {@link GROVE_DATA_PART}. */
export const GROVE_DATA_NAME = {
  note: "note",
  notification: "notification",
  question: "question",
  fileEdit: "file-edit",
  continuation: "continuation",
  compaction: "compaction",
} as const;

export type GroveDataName = (typeof GROVE_DATA_NAME)[keyof typeof GROVE_DATA_NAME];

/** A quiet summary/status row the agent emitted. */
export interface NotePartData {
  tone: "summary" | "status";
  text: string;
}

/** A background-task notice: one summary line, then the optional full result. */
export interface NotificationPartData {
  summary: string;
  detail: string;
}

/** A historical (already-resolved) question — always read-only. The LIVE
 * pending question is never a message; see `runtime/`. */
export interface QuestionPartData {
  question: AgentQuestionView;
}

/** One file mutation, drawn as an always-visible diff. `path` is the full path
 * (tooltip); `displayPath` is worktree-relative (header).
 *
 * `tool` rides it because an edit IS a tool invocation: the diff is the payload
 * and the invocation is a separate fact about it (which call, still running or
 * settled, how long). Null for a provider that reports no per-call detail. */
export interface FileEditPartData {
  path: string;
  displayPath: string;
  oldText: string;
  newText: string;
  tool: ToolCallView | null;
}

/** A resumed or compacted session's head — the turn began with no fresh prompt. */
export type ContinuationPartData = Record<string, never>;

/**
 * A compaction boundary — the exact point the agent's context was dropped and
 * replaced by a summary. Rendered by `workspace/compaction-boundary.tsx`.
 *
 * Every field is independently absent, and each absence is a distinct claim,
 * never a default:
 * - `trigger: null` means this harness records no trigger at all (Codex today)
 *   — it must never be shown as "automatic", which would be a fabrication.
 * - `at: null` is an unknown instant, not "now".
 * - `droppedTokens: null` is unmeasured, never `0`.
 * - `summary: ""` means the harness carries no post-compaction summary; an
 *   empty string is not "collapse this open onto nothing".
 */
export interface CompactionPartData {
  trigger: "manual" | "auto" | null;
  at: string | null;
  droppedTokens: number | null;
  summary: string;
}

/** Every Grove transcript row is post-hoc, so each assistant message pins
 * `complete`. That keeps `thread.isRunning` off assistant-ui's last-message
 * heuristic — Grove's core model is steering a WORKING agent with follow-ups,
 * and a running thread disables the composer. */
const COMPLETE = { type: "complete", reason: "stop" } as const;

/**
 * Per-turn build cache, keyed on the TURN OBJECT'S OWN IDENTITY.
 *
 * `SessionTurnView` carries no id and no version field (`user_text,
 * started_at, entries, sent_at`), so a content-derived cache key would risk
 * serving stale content if the daemon ever mutated a turn in place. Object
 * identity is safe instead, because `mergeTurns` (`adapters/turns.ts:90`)
 * PRESERVES the prefix of turns it already held, by reference, across a poll
 * — only the window actually returned is freshly parsed from JSON, which is a
 * new object. So: same reference in ⇒ same content ⇒ reuse; new reference in
 * ⇒ re-parsed ⇒ rebuild.
 *
 * The cached value also pins the STARTING index its messages were minted
 * from. Ids are a running counter (`grove-msg-N`): if an earlier turn's
 * message count ever changes, every later turn's ids shift, so a cache entry
 * is only valid when the running index at lookup time still matches the one
 * it was built at. A prefix change therefore misses and correctly rebuilds
 * the tail; turns only ever append in practice, so the common case is a hit.
 */
const turnMessageCache = new WeakMap<
  SessionTurnView,
  { startIndex: number; messages: ThreadMessageLike[] }
>();

/**
 * Flatten the wire's oldest-first turns into assistant-ui messages.
 *
 * One turn contributes its head (the human prompt, or a continuation marker)
 * followed by its entries. Consecutive tool entries collapse into ONE assistant
 * message carrying several `tool-call` parts, which is the shape the vendored
 * tool-group renderer groups on; any other entry closes the run, and a run never
 * spans turns because the head always lands between them.
 *
 * Message ids key on a running index so a pure append reconciles in place
 * instead of remounting every earlier message. Per-turn results are cached by
 * object identity (`turnMessageCache`) so a poll that returns the growing tail
 * turn reuses every earlier turn's messages BY REFERENCE instead of rebuilding
 * the whole transcript — this function stays pure and its output stays
 * deep-equal either way; only the object identity of the unchanged prefix
 * changes.
 */
export function messagesFromTurns(turns: readonly SessionTurnView[]): ThreadMessageLike[] {
  const messages: ThreadMessageLike[] = [];
  let nextIndex = 0;

  for (const turn of turns) {
    const cached = turnMessageCache.get(turn);
    if (cached && cached.startIndex === nextIndex) {
      messages.push(...cached.messages);
      nextIndex += cached.messages.length;
      continue;
    }

    const startIndex = nextIndex;
    const turnMessages = buildTurnMessages(turn, startIndex);
    turnMessageCache.set(turn, { startIndex, messages: turnMessages });
    messages.push(...turnMessages);
    nextIndex = startIndex + turnMessages.length;
  }

  return messages;
}

/**
 * Build one turn's messages, minting ids from `startIndex`.
 *
 * Split out of `messagesFromTurns` purely so a cache hit there can skip this
 * entirely — see `turnMessageCache`. Every message pushed below calls
 * `nextId()` exactly once (a tool run's N parts still mint one id for the one
 * message that carries them), so `messages.length` is always exactly the
 * count of ids consumed, which is what lets the caller advance its running
 * index by the returned array's length.
 */
function buildTurnMessages(turn: SessionTurnView, startIndex: number): ThreadMessageLike[] {
  const messages: ThreadMessageLike[] = [];
  let nextIndex = startIndex;
  const nextId = (): string => `grove-msg-${nextIndex++}`;

  const createdAt = parseTimestamp(turn.started_at);
  messages.push(
    turn.user_text
      ? {
          role: "user",
          content: [{ type: "text", text: turn.user_text }],
          id: nextId(),
          ...(createdAt ? { createdAt } : {}),
        }
      : dataMessage(nextId(), GROVE_DATA_PART.continuation, {}, createdAt),
  );

  // Held open across consecutive `tool` entries so one Read/Bash/Edit run is
  // one message with N parts rather than N messages.
  let toolRun: ToolCallPart[] = [];
  const flushToolRun = (): void => {
    if (toolRun.length === 0) return;
    const id = nextId();
    messages.push({
      role: "assistant",
      // The provider's `tool_use_id` wins where it exists; the positional id
      // is only a fallback, so a correlation key never gets overwritten by an
      // ordinal that changes shape the moment a run is re-windowed.
      content: toolRun.map((part, i) => ({
        ...part,
        toolCallId: part.toolCallId ?? `${id}-${i}`,
      })),
      id,
      status: COMPLETE,
    });
    toolRun = [];
  };

  for (const entry of turn.entries) {
    // A question carries its payload in `question`, not `text` (a bare confirm
    // has an empty prompt), so it is read before the blank-text guard that
    // drops empties of every other role.
    if (entry.role === "question") {
      if (!entry.question) continue;
      flushToolRun();
      messages.push(
        dataMessage(nextId(), GROVE_DATA_PART.question, { question: entry.question }),
      );
      continue;
    }
    // A compaction boundary, like a question, is read ahead of the blank-text
    // guard below: the wire may carry no digest line for one at all, and the
    // whole point is to still render the cut mark. See `compactionPartData`
    // for why this is a cast rather than `entry.role === "compaction"`.
    const compaction = compactionPartData(entry);
    if (compaction) {
      flushToolRun();
      messages.push(dataMessage(nextId(), GROVE_DATA_PART.compaction, compaction));
      continue;
    }
    // The agent's todo list is a full REWRITE on every write, so only the
    // latest matters and it is pinned as a sibling of the stream — see
    // `adapters/todo.ts`. Keeping it out here is also what keeps it off the
    // note fallback below, which it would otherwise hit.
    if (entry.role === "todo") continue;
    if (!entry.text) continue;

    if (entry.role === "tool") {
      toolRun.push(toolCallPart(entry));
      continue;
    }
    flushToolRun();

    switch (entry.role) {
      case "user":
      case "assistant":
        messages.push({
          role: entry.role,
          content: [{ type: "text", text: entry.text }],
          id: nextId(),
          ...(entry.role === "assistant" ? { status: COMPLETE } : {}),
        });
        break;
      case "file_edit":
        // A payload-less edit degrades to the same quiet note an unknown role
        // gets, never a blank card.
        messages.push(
          entry.file_edit
            ? dataMessage(nextId(), GROVE_DATA_PART.fileEdit, {
                path: entry.file_edit.path,
                displayPath: entry.file_edit.display_path,
                oldText: entry.file_edit.old_text,
                newText: entry.file_edit.new_text,
                tool: entry.tool ?? null,
              })
            : dataMessage(nextId(), GROVE_DATA_PART.note, {
                tone: "status",
                text: entry.text,
              }),
        );
        break;
      case "summary":
      case "status":
        messages.push(
          dataMessage(nextId(), GROVE_DATA_PART.note, { tone: entry.role, text: entry.text }),
        );
        break;
      case "notification":
        messages.push(
          dataMessage(nextId(), GROVE_DATA_PART.notification, splitNotification(entry.text)),
        );
        break;
      default:
        // Wire enums are untrusted at runtime: a role this client's union
        // predates degrades to a quiet note, never a throw.
        messages.push(
          dataMessage(nextId(), GROVE_DATA_PART.note, { tone: "status", text: entry.text }),
        );
    }
  }
  flushToolRun();

  return messages;
}

/** A `tool-call` part before its id is assigned. `toolCallId` is optional here
 * and only here: a call the provider gave no `tool_use_id` falls back to the
 * owning message's id, which is not minted until the run closes. */
type ToolCallPart = {
  type: "tool-call";
  toolName: string;
  argsText: string;
  result?: string;
  isError?: boolean;
  toolCallId?: string;
  /** The wire detail, carried on assistant-ui's own UI-only slot. */
  artifact?: ToolCallView;
};

/**
 * One digest entry → one native tool-call part.
 *
 * TWO SOURCES, and the split matters. `entry.text` is a rendered digest line
 * ("Edit app/page.tsx") whose first token is the tool name and whose remainder
 * is a scannable target; `entry.tool` is the structured invocation — request,
 * response, duration, running state, correlation key. The line is always there,
 * the detail is not, so the line stays the collapsed summary and the detail
 * fills the expander.
 *
 * WHY `artifact` CARRIES IT. The native part has fields for a name, a result
 * and an error flag, and Grove fills all three so anything reading the part
 * generically (grouping, addressing by `toolCallId`) still works. But most of
 * the detail has no native home — the running/error status (assistant-ui
 * derives a part's status from its MESSAGE, which Grove pins `complete` to keep
 * the composer alive), the duration (its `timing` wants two epoch stamps the
 * wire does not carry), and the two truncation flags. `artifact` is
 * assistant-ui's declared UI-only slot and it survives `fromThreadMessageLike`
 * untouched, so the whole detail rides there as ONE object rather than being
 * reassembled by the renderer from two places.
 *
 * The native `args` is deliberately left unset: it is typed `ReadonlyJSONObject`
 * and the wire's `input` is a field map of `unknown`, so filling it would mean
 * a cast for a field nothing here reads — the request comes off `artifact`.
 *
 * `tool_use_id` becomes the part's `toolCallId`, so several calls issued in one
 * assistant turn stay individually addressable however they interleave — and an
 * expander a user opened stays open when the transcript grows under it, because
 * the identity is the provider's and not this run's ordinal.
 */
function toolCallPart(entry: DigestEntryView): ToolCallPart {
  const space = entry.text.indexOf(" ");
  const line =
    space === -1
      ? { toolName: entry.text, argsText: "" }
      : { toolName: entry.text.slice(0, space), argsText: entry.text.slice(space + 1) };

  const tool = entry.tool;
  if (!tool) return { type: "tool-call", ...line };

  return {
    type: "tool-call",
    // The provider's own name for the call beats the digest line's first token,
    // which is that name already rendered — but only when it is non-empty.
    toolName: tool.name || line.toolName,
    argsText: line.argsText,
    ...(tool.tool_use_id ? { toolCallId: tool.tool_use_id } : {}),
    // `undefined`, not null: assistant-ui reads `result === undefined` as "no
    // result", and null would render an empty body as though it were one.
    ...(tool.result === null || tool.result === undefined ? {} : { result: tool.result }),
    ...(tool.status === "error" ? { isError: true } : {}),
    artifact: tool,
  };
}

/**
 * A compaction boundary's payload, read straight off the generated view.
 *
 * `compaction` rides only the fetched TURN view and not the activity digest —
 * a summary is the largest single thing a transcript holds — so an entry that
 * IS a compaction may still carry no payload. Every field therefore defaults to
 * the honest "absent" value rather than a fabricated one; see
 * `CompactionPartData`'s own docstring for what each null means. In particular
 * `dropped_tokens` is a DELTA for this one compaction, never the transcript's
 * running total, and null means the record did not say.
 */
function compactionPartData(entry: DigestEntryView): CompactionPartData | null {
  if (entry.role !== "compaction") return null;
  const payload = entry.compaction;
  return {
    trigger: payload?.trigger ?? null,
    at: payload?.at ?? null,
    droppedTokens: payload?.dropped_tokens ?? null,
    summary: payload?.summary ?? "",
  };
}

/** The wire packs a notification's summary on the first line and the subagent's
 * full result on the rest. */
function splitNotification(text: string): NotificationPartData {
  const newline = text.indexOf("\n");
  return newline === -1
    ? { summary: text, detail: "" }
    : { summary: text.slice(0, newline), detail: text.slice(newline + 1).trim() };
}

/** One assistant message wrapping a single `data-*` part — the shared shape for
 * every row assistant-ui has no native part for. */
function dataMessage(
  id: string,
  type: (typeof GROVE_DATA_PART)[keyof typeof GROVE_DATA_PART],
  data:
    | NotePartData
    | NotificationPartData
    | QuestionPartData
    | FileEditPartData
    | ContinuationPartData
    | CompactionPartData,
  createdAt?: Date,
): ThreadMessageLike {
  return {
    role: "assistant",
    content: [{ type, data }],
    id,
    status: COMPLETE,
    ...(createdAt ? { createdAt } : {}),
  };
}

/** A wire timestamp, or undefined when absent or unparseable — assistant-ui
 * defaults `createdAt` itself, so withholding it is better than inventing one. */
function parseTimestamp(value: string | null): Date | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed;
}
