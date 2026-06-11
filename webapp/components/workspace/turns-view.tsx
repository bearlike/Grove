"use client";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionTurns } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import type { SessionTurnView } from "@/lib/grove/types";

/**
 * The conversation digest for one expanded session row — fetched on mount via
 * `useSessionTurns` (the on-expand tier: zero requests until a row expands).
 * Turns render oldest-first, exactly as the wire delivers them: the user
 * prompt leads (❯-prefixed, terminal-style), then the digest entries as
 * compact rows. A turn whose `user_text` is empty is a resumed/compacted
 * session's head — rendered as a quiet "continued session" marker, not an
 * empty prompt.
 *
 * The `max-h-96` cap is deliberately ON the leaf here, unlike PeekSnapshot /
 * CommitList: this is an inline expansion inside a list, not a viewport-fill
 * panel — unbounded height would shove every later session row off-screen.
 *
 * Test seam: `data-testid="turns-view"`, `"turn-row"`, and per-entry
 * `"turn-entry"` + `data-role`.
 */
export function TurnsView({
  workspaceId,
  sessionId,
}: {
  workspaceId: string;
  sessionId: string;
}) {
  const { data, isLoading, isError } = useSessionTurns(workspaceId, sessionId);

  if (isError) {
    return (
      <p className="py-2 text-sm text-muted-foreground" data-testid="turns-view">
        couldn&apos;t load turns
      </p>
    );
  }
  if (isLoading || !data) {
    return (
      <div className="flex flex-col gap-2 py-2" data-testid="turns-view">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    );
  }
  if (data.turns.length === 0) {
    return (
      <p className="py-2 text-sm italic text-muted-foreground" data-testid="turns-view">
        no turns recorded
      </p>
    );
  }

  return (
    <ScrollArea className="min-h-0 max-h-96 pr-3" data-testid="turns-view">
      <ol className="flex flex-col gap-3 py-2">
        {data.turns.map((turn, i) => (
          <TurnRow key={`${turn.started_at ?? "t"}-${i}`} turn={turn} />
        ))}
      </ol>
    </ScrollArea>
  );
}

function TurnRow({ turn }: { turn: SessionTurnView }) {
  return (
    <li className="flex flex-col gap-1" data-testid="turn-row">
      {turn.user_text ? (
        <p className="break-words font-mono text-sm text-foreground">
          <span aria-hidden className="select-none text-muted-foreground">
            ❯{" "}
          </span>
          {turn.user_text}
        </p>
      ) : (
        // A resumed/compacted session's head — there was no fresh prompt.
        <p className="text-xs italic text-muted-foreground">continued session</p>
      )}
      {turn.entries.map((e, j) => (
        <p
          key={j}
          data-testid="turn-entry"
          data-role={e.role}
          className={cn(
            "break-words pl-4",
            e.role === "assistant" && "text-sm text-foreground/90",
            e.role === "user" && "font-mono text-sm text-foreground",
            e.role === "tool" && "font-mono text-xs text-muted-foreground",
            (e.role === "summary" || e.role === "status") &&
              "text-xs italic text-muted-foreground",
          )}
        >
          {e.role === "tool" && (
            <span aria-hidden className="select-none">
              ⚒{" "}
            </span>
          )}
          {e.text}
        </p>
      ))}
    </li>
  );
}
