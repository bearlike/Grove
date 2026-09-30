import type { NativeOperationView } from "@/lib/grove/api";

/**
 * What the working loader says while a native session is doing something more
 * specific than generating — pure, so the wording is testable without a clock.
 *
 * A native harness announces two such steps on its own stream and nowhere else:
 * summarizing its context (`compacting`), and waiting to re-send a failed
 * request (`retrying`). Both look exactly like generating to every other
 * signal — the agent state stays `working` and the transcript is silent — so
 * the loader's plain "Working" was the only thing a person saw for a
 * compaction that measured 79 seconds end to end.
 *
 * Elapsed and countdown are computed against the caller's `now`, from absolute
 * instants the daemon sent once, so the label advances without a server tick.
 */
export function operationLabel(op: NativeOperationView, now: number): string {
  if (op.kind === "compacting") {
    return `Compacting context · ${seconds(now - Date.parse(op.started_at))}`;
  }
  const attempt =
    op.attempt != null ? (op.max_attempts != null ? ` ${op.attempt}/${op.max_attempts}` : ` ${op.attempt}`) : "";
  const wait = op.retry_at != null ? Date.parse(op.retry_at) - now : null;
  // A retry whose instant has passed is being re-sent right now; a countdown
  // stuck at "0s" would read as a frozen label.
  const when = wait == null ? "" : wait > 0 ? ` in ${seconds(wait)}` : " now";
  const why = op.detail ? ` · ${op.detail}` : "";
  return `Retrying${attempt}${when}${why}`;
}

/** Whole seconds, or minutes past one; never negative (browser/daemon skew). */
function seconds(ms: number): string {
  const total = Number.isFinite(ms) ? Math.max(0, Math.round(ms / 1000)) : 0;
  return total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${total % 60}s`;
}
