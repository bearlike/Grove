import { GroveProtocolError } from "@/lib/grove/api";

/** What the user was trying to do when the daemon refused. */
export type SteeringAction = "send" | "interrupt" | "answer";

const REFUSAL: Record<SteeringAction, string> = {
  send: "Couldn't deliver that message",
  interrupt: "Couldn't interrupt",
  answer: "Couldn't submit that answer",
};

/**
 * One steering refusal, as a sentence.
 *
 * The daemon's refusals are meaningful and specific — a 409 usually means the
 * terminal got there first, a 501 that this runtime has no steering channel at
 * all — so its message is preferred over anything invented here. Lives in one
 * place because a second copy of this wording is how two surfaces come to
 * describe the same 409 differently.
 */
export function refusalNotice(error: unknown, action: SteeringAction): string {
  const prefix = REFUSAL[action];
  if (error instanceof GroveProtocolError) return `${prefix}: ${error.message}`;
  if (error instanceof Error && error.message) return `${prefix}: ${error.message}`;
  return `${prefix}.`;
}
