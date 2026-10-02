"use client";

/**
 * GROVE DELTA 18 — what a finished turn changed, above its action bar.
 *
 * One collapsed `Files (N)` row per turn, opening onto one line per file: its
 * name, its worktree-relative path muted, and `+n −n`. It mounts inside
 * `TurnActions`, so it inherits that bar's "once per turn, on the answer" rule
 * rather than restating it, and it reads the edits the transcript adapter
 * stamped on that same message (`GROVE_TURN_FILES`).
 *
 * THE COUNTS ARE THE TURN'S OWN EDITS, NOT GIT. Each is the vendored
 * `computeDiff` over the two strings an edit tool reported — the same source
 * and the same differ as the edit cards and the timeline's file chips, so the
 * three can never disagree — summed per path across the turn. Two edits that
 * touch the same lines count twice, and a change made outside an edit tool (a
 * `sed`, a formatter) is not seen at all. The Files tab is the git-truth view;
 * this answers "what did THIS turn touch", which `git diff` cannot, because the
 * working tree carries every earlier turn's changes too.
 *
 * HIDDEN WHILE ITS TURN IS STILL LIVE. The answer marker lands on the newest
 * turn's latest text as soon as one exists, so mid-turn the panel would sit
 * between two tool runs with a count that is still growing. `LiveTurnContext`
 * names the turn the agent is working on; a surface that cannot know (the
 * read-only catalog) provides nothing, and every turn there is settled.
 */

import { createContext, useContext, useMemo, useState, type FC } from "react";
import { useAuiState } from "@assistant-ui/react";
import { ChevronRightIcon } from "lucide-react";

import { computeDiff } from "@/components/assistant-ui/diff-viewer";
import { CardShell } from "@/components/grove/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import { GROVE_TURN_FILES, type FileEditPartData } from "@/lib/grove/adapters";
import { cn } from "@/lib/utils";
import { FileTypeIcon } from "./file-type-icon";

/** The `started_at` of the turn the agent is working on, or `null` when idle. */
export const LiveTurnContext = createContext<string | null>(null);

export type TurnFileStat = {
  path: string;
  displayPath: string;
  additions: number;
  deletions: number;
};

/** One row per path, in first-touched order, each edit's tally summed. */
export function turnFileStats(edits: readonly FileEditPartData[]): TurnFileStat[] {
  const byPath = new Map<string, TurnFileStat>();
  for (const edit of edits) {
    const { additions, deletions } = computeDiff(edit.oldText, edit.newText);
    const row = byPath.get(edit.path);
    if (row) {
      row.additions += additions;
      row.deletions += deletions;
    } else {
      byPath.set(edit.path, { path: edit.path, displayPath: edit.displayPath, additions, deletions });
    }
  }
  return [...byPath.values()];
}

export const TurnFiles: FC<{ startedAt: string }> = ({ startedAt }) => {
  const edits = useAuiState(
    (s) => s.message.metadata.custom[GROVE_TURN_FILES] as FileEditPartData[] | undefined,
  );
  const live = useContext(LiveTurnContext) === startedAt;
  // Memoized on the stamped array, which the adapter's per-turn cache keeps
  // referentially stable across polls, so a tick re-diffs nothing.
  const files = useMemo(() => (edits ? turnFileStats(edits) : []), [edits]);
  const [open, setOpen] = useState(false);
  if (live || files.length === 0) return null;
  return <TurnFilesCard files={files} open={open} onOpenChange={setOpen} />;
};

/** The card itself, controlled, so the expanded state renders without a click. */
export const TurnFilesCard: FC<{
  files: readonly TurnFileStat[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}> = ({ files, open, onOpenChange }) => {
  return (
    <CardShell data-testid="turn-files" data-collapsed={!open} className="ms-2">
      <Collapsible open={open} onOpenChange={onOpenChange}>
        <CollapsibleTrigger
          data-testid="turn-files-toggle"
          className="flex w-full items-center justify-between gap-2 px-4 py-2.5 text-start text-sm font-medium transition-colors outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span>Files ({files.length})</span>
          <ChevronRightIcon
            aria-hidden
            className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
          />
        </CollapsibleTrigger>
        <CollapsibleContent>
          {/* The rule spans the card's full width, so header and list read as
              two parts of one card, not as one undivided block. */}
          {open && <Separator data-testid="turn-files-separator" />}
          {open && (
            <ul className="divide-y divide-border px-4 pb-1">
              {files.map((file) => (
                <TurnFileRow key={file.path} file={file} />
              ))}
            </ul>
          )}
        </CollapsibleContent>
      </Collapsible>
    </CardShell>
  );
};

const TurnFileRow: FC<{ file: TurnFileStat }> = ({ file }) => {
  const name = file.displayPath.slice(file.displayPath.lastIndexOf("/") + 1);
  return (
    <li data-testid="turn-file" className="flex min-w-0 items-center gap-2 py-2 text-sm" title={file.path}>
      <FileTypeIcon path={file.displayPath} />
      <span className="shrink-0 font-medium">{name}</span>
      <span className="min-w-0 flex-1 truncate text-muted-foreground">{file.displayPath}</span>
      <span className="shrink-0 tabular-nums" data-testid="turn-file-stats">
        <span className="text-success">+{file.additions}</span>{" "}
        <span className="text-destructive">−{file.deletions}</span>
      </span>
    </li>
  );
};
