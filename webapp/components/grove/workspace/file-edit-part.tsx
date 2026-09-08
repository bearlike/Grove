"use client";

import { useMemo, useState } from "react";

import {
  DiffViewerContent,
  DiffViewerLine,
  DiffViewerSplitLine,
  computeDiff,
  type ParsedLine,
  type SplitLinePair,
} from "@/components/assistant-ui/diff-viewer";
import { CardDisclosure, CardShell } from "@/components/grove/card";
import type { FileEditPartData } from "@/lib/grove/adapters";
import { FileRowSummary } from "./file-row";
import { ToolInvocationMeta } from "./tool-call-part";

// The vendor exports SplitLine but keeps its pairing helper private. Pair
// contiguous replacement runs here so the existing single-header disclosure stays intact.
function splitPairs(lines: ParsedLine[]): SplitLinePair[] {
  const pairs: SplitLinePair[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index]!;
    if (line.type === "normal") {
      pairs.push({ left: line, right: line });
      index++;
      continue;
    }
    if (line.type === "add") {
      pairs.push({ left: null, right: line });
      index++;
      continue;
    }

    const deletions: ParsedLine[] = [];
    while (index < lines.length && lines[index]!.type === "del") {
      deletions.push(lines[index]!);
      index++;
    }
    const additions: ParsedLine[] = [];
    while (index < lines.length && lines[index]!.type === "add") {
      additions.push(lines[index]!);
      index++;
    }
    for (let pairIndex = 0; pairIndex < Math.max(deletions.length, additions.length); pairIndex++) {
      pairs.push({
        left: deletions[pairIndex] ?? null,
        right: additions[pairIndex] ?? null,
      });
    }
  }

  return pairs;
}

/**
 * A WRITE HAS NO LEFT-HAND SIDE, so it is drawn unified, not split.
 *
 * Split view exists to put the old text beside the new one. When the file did
 * not exist — or held nothing — that left column is empty for the whole body:
 * a creation of 200 lines renders 200 blank cells next to 200 real ones, which
 * spends half the pane's width saying "there was nothing here" 200 times and
 * halves the measure available to the content anybody is reading. The vendor
 * already ships both renderers, so this is a choice between two of its own
 * rows rather than a second viewer.
 *
 * The test is the OLD text, never the tool's name. `Write` routinely overwrites
 * a file that has plenty of content, and that is a real two-sided diff; an
 * `Edit` whose payload reports an empty original is a creation whatever the
 * provider called it. Whitespace-only counts as empty — a file holding one
 * newline has no left side worth a column either.
 */
function isCreation(data: FileEditPartData): boolean {
  return data.oldText.trim() === "";
}

/**
 * The diff body, in whichever of the vendor's two row shapes the edit calls for.
 *
 * One component rather than two call sites choosing, because the standalone
 * card and the timeline step must never disagree about how a given edit reads.
 */
function DiffBody({ data, lines }: { data: FileEditPartData; lines: ParsedLine[] }) {
  const unified = isCreation(data);
  const pairs = useMemo(() => (unified ? [] : splitPairs(lines)), [unified, lines]);

  return (
    <DiffViewerContent
      role="region"
      aria-label={`Changes to ${data.displayPath}`}
      tabIndex={0}
      className="bg-surface-sunken font-mono text-xs"
      data-testid="file-edit-diff"
      data-diff-view={unified ? "unified" : "split"}
    >
      <div className="min-w-full w-max py-2">
        {unified
          ? lines.map((line, index) => <DiffViewerLine key={index} line={line} className="pr-3" />)
          : pairs.map((pair, index) => (
              <DiffViewerSplitLine key={index} pair={pair} className="pr-3" />
            ))}
      </div>
    </DiffViewerContent>
  );
}

/** The step owns disclosure; its card restores the path and counts without a second toggle. */
export function FileEditDiff({ data }: { data: FileEditPartData }) {
  const { lines, additions, deletions } = useMemo(
    () => computeDiff(data.oldText, data.newText),
    [data.oldText, data.newText],
  );

  return (
    <CardShell className="mt-1.5" data-testid="file-edit-expanded" title={data.path}>
      <div
        className="surface-header flex min-w-0 items-center gap-2 border-b border-border px-3 py-1.5"
        data-testid="file-edit-header"
      >
        <FileRowSummary path={data.displayPath} additions={additions} deletions={deletions} />
      </div>
      <DiffBody data={data} lines={lines} />
    </CardShell>
  );
}

/** The add/remove tally for one edit, shared by the row and the timeline chips. */
export function fileEditCounts(data: FileEditPartData): { added: number; removed: number } {
  const { additions, deletions } = computeDiff(data.oldText, data.newText);
  return { added: additions, removed: deletions };
}

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
  const { additions, deletions, lines } = useMemo(
    () => computeDiff(data.oldText, data.newText),
    [data.oldText, data.newText],
  );

  return (
    <CardShell data-testid="file-edit-card" data-collapsed={!open} title={data.path}>
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        data-testid="file-edit-toggle"
        header
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
        {/* Compose the native lines, not a second complete viewer. The file
            summary above is this diff's only header and disclosure trigger. */}
        {open && <DiffBody data={data} lines={lines} />}
      </CardDisclosure>
    </CardShell>
  );
}
