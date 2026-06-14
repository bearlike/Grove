"use client";
import { useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { RoleLabel } from "@/components/shared/role-label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionTurns } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import type { DigestEntryView, SessionTurnView } from "@/lib/grove/types";

/**
 * The conversation digest for one expanded session row — fetched on mount via
 * `useSessionTurns` (the on-expand tier: zero requests until a row expands).
 * Turns render oldest-first, exactly as the wire delivers them: the user
 * prompt leads (❯-prefixed, terminal-style), then the digest entries as
 * compact rows. A turn whose `user_text` is empty is a resumed/compacted
 * session's head — rendered as a quiet "continued session" marker, not an
 * empty prompt. Consecutive tool entries collapse into one "N tool calls"
 * disclosure row (collapsed by default) so a long Read/Bash/Edit run doesn't
 * spam the digest; expanding reveals the individual ⚒ rows.
 *
 * The `max-h-96` cap is deliberately ON the leaf here, unlike PeekSnapshot /
 * CommitList: this is an inline expansion inside a list, not a viewport-fill
 * panel — unbounded height would shove every later session row off-screen.
 *
 * Test seam: `data-testid="turns-view"`, `"turn-row"`, per-entry
 * `"turn-entry"` + `data-role`, `"tool-group"` for a collapsed tool run, and
 * `"role-label"` + `data-role-label` on the IRC-style speaker labels.
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

/**
 * Group a turn's entries so consecutive tool calls render as one disclosure
 * row. Same "consecutive within a turn" rule as `chatItemsFromTurns`, kept
 * local because the render shape differs (raw digest entries, not split
 * name/detail pairs).
 */
type EntrySegment =
  | { kind: "entry"; entry: DigestEntryView }
  | { kind: "tools"; entries: DigestEntryView[] };

function segmentEntries(entries: DigestEntryView[]): EntrySegment[] {
  const segments: EntrySegment[] = [];
  for (const entry of entries) {
    const prev = segments[segments.length - 1];
    if (entry.role === "tool" && prev?.kind === "tools") {
      prev.entries.push(entry);
    } else if (entry.role === "tool") {
      segments.push({ kind: "tools", entries: [entry] });
    } else {
      segments.push({ kind: "entry", entry });
    }
  }
  return segments;
}

function TurnRow({ turn }: { turn: SessionTurnView }) {
  return (
    <li className="flex flex-col gap-1" data-testid="turn-row">
      {turn.user_text ? (
        <p className="break-words font-mono text-sm text-foreground">
          <RoleLabel role="you" className="mr-1.5" />
          <span aria-hidden className="select-none text-muted-foreground">
            ❯{" "}
          </span>
          {turn.user_text}
        </p>
      ) : (
        // A resumed/compacted session's head — there was no fresh prompt.
        <p className="text-xs italic text-muted-foreground">continued session</p>
      )}
      {segmentEntries(turn.entries).map((segment, j) =>
        segment.kind === "tools" ? (
          <ToolGroupRow key={j} entries={segment.entries} />
        ) : (
          <EntryRow key={j} entry={segment.entry} />
        ),
      )}
    </li>
  );
}

/** Non-tool digest rows — tool entries always route through `ToolGroupRow`.
 * Conversational rows lead with the IRC-style speaker label (`agent` blue /
 * `you` clay — the convention shared with the TUI); summary/status rows are
 * commentary, not speech, so they stay unlabeled. */
function EntryRow({ entry }: { entry: DigestEntryView }) {
  return (
    <p
      data-testid="turn-entry"
      data-role={entry.role}
      className={cn(
        "break-words pl-4",
        entry.role === "assistant" && "text-sm text-foreground/90",
        entry.role === "user" && "font-mono text-sm text-foreground",
        (entry.role === "summary" || entry.role === "status" || entry.role === "notification") &&
          "text-xs italic text-muted-foreground",
      )}
    >
      {entry.role === "assistant" && <RoleLabel role="agent" className="mr-1.5" />}
      {entry.role === "user" && <RoleLabel role="you" className="mr-1.5" />}
      {/* A notification packs a summary line + the subagent's full result;
          the digest is a glance surface, so only the summary line renders
          here (the chat panel carries the expandable full row). */}
      {entry.role === "notification" ? entry.text.split("\n", 1)[0] : entry.text}
    </p>
  );
}

/**
 * One collapsed "N tool calls" row for a consecutive run — same disclosure
 * conventions as `components/ai-elements/tool.tsx` (local `open`, chevron,
 * `aria-expanded`, conditional mount), in the digest's quiet ⚒ mono style.
 */
function ToolGroupRow({ entries }: { entries: DigestEntryView[] }) {
  const [open, setOpen] = useState(false);
  const label = entries.length === 1 ? "1 tool call" : `${entries.length} tool calls`;

  return (
    <div className="flex flex-col gap-1 pl-4" data-testid="tool-group">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-fit items-center gap-1 rounded-sm text-left font-mono text-xs text-muted-foreground",
          "transition-colors hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      >
        <ChevronDownIcon
          aria-hidden
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-180")}
        />
        <span aria-hidden className="select-none">
          ⚒{" "}
        </span>
        {label}
      </button>
      {open &&
        entries.map((entry, j) => (
          <p
            key={j}
            data-testid="turn-entry"
            data-role="tool"
            className="break-words pl-4 font-mono text-xs text-muted-foreground"
          >
            <span aria-hidden className="select-none">
              ⚒{" "}
            </span>
            {entry.text}
          </p>
        ))}
    </div>
  );
}
