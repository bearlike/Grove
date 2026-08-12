"use client";

import { useMemo, useState } from "react";

import { DiffViewer, computeDiff } from "@/components/assistant-ui/diff-viewer";
import { CardDisclosure, CardShell } from "@/components/grove/card";
import type { FileEditPartData } from "@/lib/grove/adapters";
import { FileRowSummary } from "./file-row";
import { NestLevel } from "./nesting";
import { ToolInvocationMeta } from "./tool-call-part";

/**
 * One file mutation in the transcript: a header always, the diff only while open.
 *
 * WHY COLLAPSED BY DEFAULT. This used to render every diff expanded, and a
 * transcript carries one of these per Edit/Write the agent ever made — the same
 * shape that froze the Files tab, except the transcript has no list to cap and
 * no tab to leave. A session with a hundred edits paid a hundred full unified
 * diffs of DOM before it could paint a single reply.
 *
 * Collapse hides the DETAIL, never the SIGNAL: the row states which file and
 * how much of it moved, so scanning a run still answers "what did it touch"
 * without a click. The `open &&` guard is deliberate belt-and-braces — Radix
 * already unmounts a closed `CollapsibleContent`, but that is an invisible
 * property of a vendored component, and a later `forceMount` for an exit
 * animation would silently restore every diff on mount.
 */
export function FileEditPart({ data }: { data: FileEditPartData }) {
  const [open, setOpen] = useState(false);

  // The tally is the one thing a collapsed row cannot fake, so it is the floor
  // this component pays at mount: one `diffLines` per edit. It is the vendored
  // `computeDiff` and not a private line count so a row can never disagree with
  // the diff it expands into. The Files tab answers a different question — it
  // renders git's own patch, so it needs no differ at all; here the only source
  // is the two strings the agent reported, so a diff is the honest way to count
  // them. Memoized on the two strings, so an SSE tick that rebuilds the message
  // list costs nothing. Measured on this host: ~0.2 ms for an Edit-sized
  // snippet, ~4 ms for a from-scratch write of 2500 lines.
  const { additions, deletions } = useMemo(
    () => computeDiff(data.oldText, data.newText),
    [data.oldText, data.newText],
  );

  return (
    <CardShell data-testid="file-edit-card" data-collapsed={!open} title={data.path}>
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        data-testid="file-edit-toggle"
        contentClassName="px-3 pb-3"
        summary={
          <>
            <FileRowSummary
              path={data.displayPath}
              additions={additions}
              deletions={deletions}
            />
            {/* An edit IS a tool invocation, and the wire says so on the same
                entry. The card already expands into its diff, so the invocation
                gets a header line rather than a second disclosure. */}
            <ToolInvocationMeta tool={data.tool} />
          </>
        }
      >
        {/* The diff is one level deeper than the row that discloses it — same
            mechanism the transcript's tool calls use (`./nesting`), so a file
            edit reads as the same kind of nesting rather than a fourth one. */}
        <NestLevel>
          {/* `showIcon`/`showStats` off: the row above already carries both,
              and the vendored header's own badge is the monochrome text chip
              `FileTypeIcon` replaces. */}
          <DiffViewer
            oldFile={{ content: data.oldText, name: data.displayPath }}
            newFile={{ content: data.newText, name: data.displayPath }}
            viewMode="unified"
            size="sm"
            showIcon={false}
            showStats={false}
          />
        </NestLevel>
      </CardDisclosure>
    </CardShell>
  );
}
