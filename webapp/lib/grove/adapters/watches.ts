import type { WatchView } from "@/lib/grove/api";

/**
 * What a watch is waiting on, in the words a reader scans a list by.
 *
 * Mirrors the subject line the daemon writes into the callback mail
 * (`WatchScheduler._subject`), shortened for a row: a commit reads as its
 * first seven characters, the way every other surface here prints one.
 */
export function watchSubject(predicate: WatchView["predicate"]): string {
  switch (predicate.kind) {
    case "ci":
      return `CI on ${predicate.owner}/${predicate.repo}@${predicate.head_sha.slice(0, 7)}`;
    case "command":
      return `Command: ${predicate.argv.join(" ")}`;
    case "timer":
      return "Timer";
    case "ticket":
      return `Changes to ${predicate.ticket_kind === "pull_request" ? "PR" : "issue"} #${predicate.ticket_id}`;
  }
}

/**
 * Running watches first, then settled ones — each group newest first.
 *
 * A running watch is the only kind a reader can still act on (cancel it, or
 * know the agent is parked on it); a settled one is history. The daemon lists
 * newest registration first, which would bury a long-running watch under a
 * dozen timers that fired minutes ago.
 */
export function orderWatches(watches: readonly WatchView[]): WatchView[] {
  const running = watches.filter((watch) => watch.state === "pending");
  const settled = watches.filter((watch) => watch.state !== "pending");
  return [...running, ...settled];
}
