import type { ToolCallMessagePartStatus } from "@assistant-ui/react";

import type { DigestEntryView } from "@/lib/grove/api";

/**
 * One tool invocation's wire detail → the shapes the transcript renders.
 *
 * Pure, and separate from `transcript.ts` because it answers a different
 * question: not "which message does this entry become" but "what is TRUE about
 * the invocation". The distinction is load-bearing — the same invocation facts
 * ride a `tool` entry, a `file_edit` card and a `todo` write, so the mapping
 * cannot live inside the code that decides a message's role.
 *
 * THREE FACTS THIS MODULE REFUSES TO COLLAPSE, because the daemon went to real
 * trouble to keep them apart and every one of them looks like the others:
 *
 *   - `tool === null` — not a tool call at all (prose, a notification), or a
 *     provider that surfaces no per-call detail. It never means "running".
 *   - `status === "running"` — in flight. This is the spinner, and
 *     `result === null` is NOT the signal: a settled call that returned nothing
 *     is also null.
 *   - `result === null` with a settled status — it ran, and it returned nothing.
 *
 * And one thing this module deliberately does NOT do: infer failure from a
 * result body. A failed Codex tool reports `ok` with its error text inside,
 * because Codex has no structural error flag at all. Pattern-matching that text
 * would be the renderer correcting a provider's semantics, which is exactly
 * what an adapter must not do — it normalizes SHAPE, never meaning.
 */

/**
 * The wire's per-call detail.
 *
 * Reached through `DigestEntryView` rather than imported by name: the generated
 * `lib/grove/api/types.ts` alias list has not been regenerated since the daemon
 * added `ToolCallView`, and this module owns no file under `api/`.
 */
export type ToolCallView = NonNullable<DigestEntryView["tool"]>;

/** One request argument, already rendered to text so no two call sites can
 * format the same value differently. */
export interface ToolCallField {
  name: string;
  value: string;
}

/**
 * The request's field map, in wire order.
 *
 * Values arrive as `unknown` (a tool's schema is the provider's, not ours), so
 * strings pass through verbatim — a Bash `command` must render as the lines the
 * agent actually ran, not as a JSON blob with `\n` in it — and everything else
 * is pretty-printed. An empty map and an absent one are the same to a reader
 * and both yield `[]`; the CALLER distinguishes "no arguments" from "no detail",
 * because only it knows whether a `ToolCallView` existed at all.
 */
export function toolCallFields(input: ToolCallView["input"]): readonly ToolCallField[] {
  if (!input) return [];
  return Object.entries(input).map(([name, value]) => ({ name, value: renderValue(value) }));
}

function renderValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    // `JSON.stringify(undefined)` is `undefined`, not a string; a cyclic value
    // throws. Both are provider data, so neither may take the transcript down.
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

/**
 * Grove's three-state wire status → the vendored trigger's status union.
 *
 * This CANNOT come from the native part status, and the reason is worth
 * knowing before someone tries: assistant-ui derives a tool part's status from
 * its owning MESSAGE (`result === undefined ? message.status : complete`), and
 * every Grove transcript message pins `complete` on purpose — a running message
 * would set `thread.isRunning` and disable the composer, which is the one thing
 * Grove's steering model needs available while an agent works. So the status is
 * passed to the trigger as a prop, from the only place that knows it.
 *
 * `error` carries no `error` payload: the provider's own text is the response
 * body and the expander already renders it, so attaching it here would print
 * the same string twice under two different headings.
 */
export function toolCallStatus(status: ToolCallView["status"]): ToolCallMessagePartStatus {
  if (status === "running") return { type: "running" };
  if (status === "error") return { type: "incomplete", reason: "error" };
  return { type: "complete" };
}

/** The word beside the mark. Colour is never the only carrier of a state
 * (design-system §4.7), so every status says itself. */
export function toolCallStatusLabel(status: ToolCallView["status"]): string {
  if (status === "running") return "Running";
  return status === "error" ? "Failed" : "Done";
}

/**
 * A call's duration, or null while it runs.
 *
 * Mirrors the vendored `formatToolDuration` inside `assistant-ui/tool-fallback`
 * so the two never disagree on screen. It is reproduced rather than imported
 * because the vendored one is private AND unreachable: it is fed by
 * `useToolCallElapsed`, which reads `part.timing` — a pair of epoch stamps
 * Grove's wire does not carry. It reports an elapsed span, not a start.
 */
export function formatToolDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  const clamped = Math.max(0, ms);
  if (clamped < 1000) return "<1s";
  const seconds = clamped / 1000;
  if (seconds < 10) return `${(Math.floor(seconds * 10) / 10).toFixed(1)}s`;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}

/**
 * Narrow a message part's `artifact` back to the wire detail that was put there.
 *
 * The boundary this guard defends is real rather than ceremonial: `artifact` is
 * typed `unknown` by assistant-ui, and the message list is rebuilt from the wire
 * on every poll, so a daemon that stops sending `tool` must degrade to "no
 * detail" instead of throwing inside a transcript row.
 */
export function asToolCall(artifact: unknown): ToolCallView | null {
  if (typeof artifact !== "object" || artifact === null) return null;
  const candidate = artifact as Partial<ToolCallView>;
  if (typeof candidate.name !== "string" || typeof candidate.tool_use_id !== "string") return null;
  const status = candidate.status;
  if (status !== "running" && status !== "ok" && status !== "error") return null;
  return candidate as ToolCallView;
}
