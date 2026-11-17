import type { LivePulseView } from "@/lib/grove/api";
import { formatTokenCount } from "./context";

/**
 * What the working loader says about a native session's live step — pure, so
 * the wording is testable without a stream or a clock.
 *
 * The daemon only relays what the owned stream stated; this decides nothing
 * about the session, it only spells the step out:
 *
 * - `thinking`: "Thinking · 1.2K tokens · 14s" — Claude's own running estimate.
 * - `generating`: "Writing · 3.4K tokens".
 * - `tool`: "Running Bash · make lint · 8s" — the hint is the call's first
 *   naming argument, already cut to one line by the daemon.
 * - `subagent`: "2 sub-agents · Inventory file fields · Bash".
 *
 * `null` means the pulse names no step, and the loader keeps its plain word.
 */
export function liveLabel(pulse: LivePulseView, now: number): string | null {
  const elapsed = pulse.since ? since(now - Date.parse(pulse.since)) : null;
  const parts: (string | null | undefined)[] = [];
  switch (pulse.step) {
    case "thinking":
      parts.push("Thinking", tokens(pulse.thinking_tokens), elapsed);
      break;
    case "generating":
      parts.push("Writing", tokens(pulse.output_tokens), elapsed);
      break;
    case "tool":
      parts.push(pulse.tool ? `Running ${pulse.tool}` : "Running a tool", pulse.tool_detail, elapsed);
      break;
    case "subagent": {
      const count = pulse.subagents;
      parts.push(
        count > 1 ? `${count} sub-agents` : "Sub-agent",
        pulse.subagent,
        pulse.subagent_tool,
      );
      break;
    }
    default:
      return null;
  }
  return parts.filter((part): part is string => Boolean(part)).join(" · ");
}

/**
 * The short accessible name for a step — a screen reader hears what the
 * session is doing, never a number that changes every second.
 */
export function liveStepName(pulse: LivePulseView): string | null {
  switch (pulse.step) {
    case "thinking":
      return "Thinking";
    case "generating":
      return "Writing a reply";
    case "tool":
      return pulse.tool ? `Running ${pulse.tool}` : "Running a tool";
    case "subagent":
      return "Sub-agents working";
    default:
      return null;
  }
}

function tokens(count: number | null | undefined): string | null {
  return count != null && count > 0 ? `${formatTokenCount(count)} tokens` : null;
}

/** Whole seconds, or minutes past one; hidden for the first second (noise). */
function since(ms: number): string | null {
  const total = Number.isFinite(ms) ? Math.max(0, Math.round(ms / 1000)) : 0;
  if (total < 1) return null;
  return total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${total % 60}s`;
}
