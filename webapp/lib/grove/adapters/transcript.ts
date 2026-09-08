import type { ThreadMessageLike } from "@assistant-ui/react";

import type { AgentQuestionView, DigestEntryView, SessionTurnView } from "@/lib/grove/api";
import { messageAttachments, splitAttachments } from "./attachments";
import type { ToolCallView } from "./tool-call";
import type { AgentMessageData } from "./agent-message";

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
  mailbox: "data-mailbox",
  question: "data-question",
  fileEdit: "data-file-edit",
  continuation: "data-continuation",
  compaction: "data-compaction",
} as const;

/** The bare `data.by_name` render keys for {@link GROVE_DATA_PART}. */
export const GROVE_DATA_NAME = {
  note: "note",
  notification: "notification",
  mailbox: "mailbox",
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
 *
 * Deliberately NOT keyed on the prefix's tool-call ids. Uniqueness does not
 * need it — a cache hit claims its own ids before the tail is built, so the
 * tail can never reuse one — and computing such a key means serializing the
 * whole accumulated id set once per turn, which is O(turns x ids) of string
 * work on the hottest render path in the app, every poll, to stabilize a
 * suffix in a case a reference-preserving merge cannot actually produce.
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
  const toolCallIds = new Set<string>();
  let nextIndex = 0;

  for (const turn of turns) {
    const cached = turnMessageCache.get(turn);
    if (cached && cached.startIndex === nextIndex) {
      messages.push(...cached.messages);
      // Cache hits must claim their existing ids before a new tail is built:
      // rebuilding them just to learn those ids would remount an unchanged
      // prefix, while leaving them unclaimed lets the tail reuse one.
      rememberToolCallIds(cached.messages, toolCallIds);
      nextIndex += cached.messages.length;
      continue;
    }

    const startIndex = nextIndex;
    const turnMessages = buildTurnMessages(turn, startIndex, toolCallIds);
    turnMessageCache.set(turn, { startIndex, messages: turnMessages });
    messages.push(...turnMessages);
    nextIndex = startIndex + turnMessages.length;
  }

  return messages;
}

/** Claim the tool-call ids embedded in a cached turn before building its tail. */
function rememberToolCallIds(messages: readonly ThreadMessageLike[], toolCallIds: Set<string>): void {
  for (const message of messages) {
    if (!Array.isArray(message.content)) continue;
    for (const part of message.content) {
      if (part.type === "tool-call" && typeof part.toolCallId === "string") {
        toolCallIds.add(part.toolCallId);
      }
    }
  }
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
function buildTurnMessages(
  turn: SessionTurnView,
  startIndex: number,
  toolCallIds: Set<string>,
): ThreadMessageLike[] {
  const messages: ThreadMessageLike[] = [];
  let nextIndex = startIndex;
  const nextId = (): string => `grove-msg-${nextIndex++}`;

  const createdAt = parseTimestamp(turn.started_at);
  // The files a human attached ride the turn's own text as Grove's fenced
  // block — the daemon has no structured field for them — so they are lifted
  // back out into `attachments` here, the same field a just-sent message
  // carries. The reader then sees which files went with which message instead
  // of a paragraph of paths at the bottom of it.
  const body = turn.user_text ? splitAttachments(turn.user_text) : null;
  messages.push(
    body
      ? {
          role: "user",
          content: body.text ? [{ type: "text" as const, text: body.text }] : [],
          ...(body.attachments.length > 0
            ? { attachments: messageAttachments(body.attachments) }
            : {}),
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
      // The provider's `tool_use_id` remains the normal identity so an open
      // expander survives polling. Only a collision is suffixed with its stable
      // owning message and part position; rebuilding unchanged turns to find
      // collisions would defeat their reference-preserving cache entries.
      content: toolRun.map((part, i) => {
        const toolCallId = part.toolCallId ?? `${id}-${i}`;
        let uniqueToolCallId = toolCallId;
        if (toolCallIds.has(uniqueToolCallId)) {
          uniqueToolCallId = `${toolCallId}-${id}-${i}`;
          let duplicate = 2;
          while (toolCallIds.has(uniqueToolCallId)) {
            uniqueToolCallId = `${toolCallId}-${id}-${i}-${duplicate++}`;
          }
        }
        toolCallIds.add(uniqueToolCallId);
        return { ...part, toolCallId: uniqueToolCallId };
      }),
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
      // An open question belongs exclusively to the interactive footer. Letting
      // it into this historical stream would render the same interaction twice.
      if (!entry.question || !entry.question.answered) continue;
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
    // The board stays pinned outside the stream, but its recorded invocation
    // belongs in the tool timeline just like every other non-edit call.
    if (entry.role === "todo") {
      if (entry.tool) toolRun.push(toolCallPart(entry));
      continue;
    }
    if (entry.mailbox) {
      // NOT a flush. A mailbox delivery arrives INSIDE a run of tool calls —
      // the send that carried it is itself a tool call — so closing the run
      // here split one continuous sequence into two collapsible groups with a
      // loose card wedged between them, which is what the transcript looked
      // like until 2026-09-15. The `todo` role above already learned this: a
      // row that belongs to the run rides the run.
      //
      // The card still renders whole: `ToolCallPart` already draws an
      // `AgentMessage` rather than a tool row when it recognises one (that is
      // how an OUTGOING send has always rendered inside a group), so the part
      // carries the envelope on assistant-ui's own `artifact` slot and the
      // renderer reads it back.
      toolRun.push(
        mailboxPart(entry, {
          from: entry.mailbox.sender,
          to: entry.mailbox.recipient ?? "This session",
          subject: entry.mailbox.subject,
          body: entry.mailbox.body,
          // The wire's own answer, never a guess from which fields are filled
          // — see `AgentMessageData.handoff`. Absent on an older daemon's
          // payload, which decodes as the conservative `notice`.
          handoff: entry.mailbox.kind === "peer",
        }),
      );
      continue;
    }
    if (entry.role === "tool" && (entry.text || entry.tool)) {
      toolRun.push(toolCallPart(entry));
      continue;
    }
    // AN EDIT IS A TOOL CALL, so it belongs to the run rather than breaking it.
    // It used to flush — which split one continuous piece of work into a tool
    // group, a standalone diff card, and another tool group, so a run that read
    // a file, edited it and ran the tests read as three unrelated blocks. The
    // diff rides the part as `fileEdit` and the step renders the native card
    // when expanded, so nothing about the diff itself is lost.
    if (entry.role === "file_edit" && entry.file_edit) {
      toolRun.push(fileEditCallPart(entry, entry.file_edit));
      continue;
    }
    if (!entry.text) continue;
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
        // gets, never a blank card. (An edit WITH a payload never reaches here
        // — it stays in the tool run above, because an edit is a tool call.)
        messages.push(
          dataMessage(nextId(), GROVE_DATA_PART.note, { tone: "status", text: entry.text }),
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
  /**
   * An INCOMING peer delivery riding inside a tool run.
   *
   * Set only by {@link mailboxPart}. `ToolCallPart` reads it and draws the
   * mailbox card instead of a timeline row, which is what lets a delivery sit
   * in the same collapsible group as the calls around it rather than splitting
   * the run in two. An outgoing send needs no equivalent — it IS a tool call,
   * so the renderer recovers it from `artifact` through `outgoingAgentMessage`.
   */
  groveMailbox?: AgentMessageData;
  /**
   * The diff an EDIT call produced, riding inside a tool run.
   *
   * Same mechanism as `groveMailbox` and for the same reason: `artifact` is
   * contracted as the wire's `ToolCallView`, so a second meaning there is how
   * two readers come to disagree about what that field holds. `ToolCallPart`
   * reads this and expands the step into the native split-diff card.
   */
  groveFileEdit?: FileEditPartData;
};

/**
 * One incoming peer delivery, shaped as a group part.
 *
 * `type: "tool-call"` is what the vendored group renders, so a non-call row can
 * only ride the run by wearing that type. The envelope travels on its own key
 * rather than inside `artifact`, because `artifact` is contracted as the wire's
 * `ToolCallView` and a second meaning on one field is how two readers come to
 * disagree about what it holds.
 *
 * `toolName` falls back to a readable label rather than an empty string: it is
 * what the group's `title` attribute and any future census would print, and an
 * unnamed part reads as a bug in the timeline rather than as a message.
 */
function mailboxPart(entry: DigestEntryView, message: AgentMessageData): ToolCallPart {
  const tool = entry.tool;
  return {
    type: "tool-call",
    toolName: tool?.name || "Mailbox",
    argsText: "",
    ...(tool?.tool_use_id ? { toolCallId: tool.tool_use_id } : {}),
    ...(tool ? { artifact: tool } : {}),
    groveMailbox: message,
  };
}

/**
 * One file edit, shaped as a group part so it rides the run it belongs to.
 *
 * The diff travels on `groveFileEdit` rather than becoming its own
 * `data-file-edit` message, which is what folds an edit into the surrounding
 * timeline instead of splitting it. `toolName` prefers the provider's own name
 * (`Edit`, `Write`, `apply_patch`) so the catalog resolves the right verb and
 * mark; an edit the provider did not name still reads as one rather than as an
 * unknown tool.
 */
function fileEditCallPart(
  entry: DigestEntryView,
  edit: NonNullable<DigestEntryView["file_edit"]>,
): ToolCallPart {
  const tool = entry.tool;
  return {
    type: "tool-call",
    toolName: tool?.name || "Edit",
    argsText: edit.display_path,
    ...(tool?.tool_use_id ? { toolCallId: tool.tool_use_id } : {}),
    ...(tool?.result === null || tool?.result === undefined ? {} : { result: tool.result }),
    ...(tool?.status === "error" ? { isError: true } : {}),
    ...(tool ? { artifact: tool } : {}),
    groveFileEdit: {
      path: edit.path,
      displayPath: edit.display_path,
      oldText: edit.old_text,
      newText: edit.new_text,
      tool: tool ?? null,
    },
  };
}

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
    | AgentMessageData
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
