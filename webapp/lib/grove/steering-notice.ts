import { GroveProtocolError } from "./client";

/**
 * Map a steering failure (send a message / interrupt / answer a question) to
 * a quiet inline notice — refusals are expected, not exceptional. Used by the
 * chat panel's runtime (`useGroveChatRuntime`) so every steering refusal —
 * send, interrupt, or a live-question answer — reads identically instead of
 * each call site inventing its own wording for the same 409/501 envelope.
 */
export function refusalNotice(err: unknown, verb: "send" | "interrupt" | "answer"): string {
  if (err instanceof GroveProtocolError && (err.status === 409 || err.status === 501)) {
    // The daemon's typed refusal (agent not running / adapter can't steer /
    // the question's tool_use_id is no longer the pending one).
    return `Steering unavailable — ${err.message}`;
  }
  const message = err instanceof Error ? err.message : String(err);
  const action =
    verb === "send" ? "send the message" : verb === "interrupt" ? "interrupt the agent" : "submit the answer";
  return `Could not ${action} — ${message}`;
}
