import type { ToolCallView } from "./tool-call";

/** Only protocol metadata supplies identity; never infer an agent from prose. */
export interface AgentMessageData {
  from: string | null;
  to: string | null;
  subject: string | null;
  body: string;
  state?: string;
  /**
   * Whether this is a real agent-to-agent handoff, from the wire's own `kind`.
   *
   * Never inferred from the fields being populated: a task notice carries a
   * task id in `from` and nothing in `to`, which is indistinguishable from a
   * peer message whose recipient went unrecorded. Only the daemon knows which
   * protocol delivered it, so only the daemon may say.
   */
  handoff?: boolean;
}

/**
 * A workspace id, shortened the way every other Grove surface shortens one.
 *
 * A 32-char hex id is the honest address and an unreadable pill, so the mark
 * shows the same 10-char prefix `grove ls` and the rail print. Anything that is
 * not a bare id (an `id/agent` slot, a teammate's display name) is left exactly
 * as it arrived — truncating a name would be inventing a different one.
 */
export function agentLabel(value: string): string {
  return /^[0-9a-f]{32}$/.test(value) ? value.slice(0, 10) : value;
}

/**
 * The one in-flight delivery state.
 *
 * A constant rather than a literal in two files because the card reads it to
 * decide the handoff's `settled` styling — two copies of the word is how the
 * card silently stops recognising the state the adapter emits.
 */
export const SENDING = "Sending";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** Recognized outgoing mailbox calls remain tool calls, with their delivery state intact. */
export function outgoingAgentMessage(call: ToolCallView): AgentMessageData | null {
  const input = call.input;
  if (!input) return null;
  const name = call.name.split("__").at(-1)?.split(".").at(-1);
  let to: string | null;
  let body: string | null;
  if (name === "SendMessage") {
    to = text(input.to) ?? text(input.recipient);
    body = text(input.message) ?? text(input.content);
  } else if (name === "grove_send_workspace_message") {
    to = text(input.workspace_id);
    body = text(input.text);
  } else {
    return null;
  }
  // Control payloads (shutdown/approval), subscription-only calls, and malformed
  // messages retain their generic tool rendering rather than becoming blank mail.
  if (!body) return null;
  return {
    from: "This session",
    to,
    subject: text(input.summary) ?? text(input.subject),
    body,
    state: call.status === "running" ? SENDING : call.status === "error" ? "Delivery failed" : "Send call completed",
    // An OUTGOING send is agent-to-agent by construction — this session named a
    // recipient — so it renders as a handoff like an incoming peer delivery.
    // Unlike the incoming side there is no wire `kind` to consult and none is
    // needed: the tool NAME is already the protocol evidence, which is what
    // `outgoingAgentMessage` matched on to get here.
    handoff: true,
  };
}
