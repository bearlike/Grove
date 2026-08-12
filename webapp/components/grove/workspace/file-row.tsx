import { FileTypeIcon } from "./file-type-icon";

/**
 * The summary line for one file that changed: icon, path, tally.
 *
 * WHY THIS IS SHARED. Grove shows a changed file in two places — as a mutation
 * in the transcript (`file-edit-part.tsx`) and as an entry in the Files tab
 * (`files-tab.tsx`) — and they had drifted: the transcript led with the coloured
 * type icon and suppressed the vendored `DiffViewer` header's own monochrome
 * chip, while the Files tab had no leading icon at all and let that chip
 * through. Same file, two vocabularies, depending on which pane you were
 * looking at.
 *
 * Keeping the row in one component is what makes that divergence impossible
 * rather than merely fixed. The two callers differ ONLY in where the numbers
 * come from — a `computeDiff` over the two strings the agent reported, versus
 * git's own patch header — which is a data difference, not a presentation one.
 *
 * BOTH CALLERS MUST PASS `showIcon={false}` to `DiffViewer`. The vendored
 * header draws the monochrome text chip this component's icon replaces, and
 * leaving it on puts two different icons on one file.
 */
export function FileRowSummary({
  path,
  additions,
  deletions,
}: {
  path: string;
  additions: number;
  deletions: number;
}): React.ReactNode {
  // The directory takes the truncation and the file name never does: a path
  // clipped from the right in a docked pane loses precisely the part that
  // identifies the file.
  const cut = path.lastIndexOf("/") + 1;

  return (
    <>
      <FileTypeIcon path={path} />
      <span className="flex min-w-0 flex-1 font-mono text-xs" title={path}>
        <span className="truncate text-muted-foreground">{path.slice(0, cut)}</span>
        <span className="shrink-0">{path.slice(cut)}</span>
      </span>
      <span className="shrink-0 font-mono text-xs tabular-nums" data-testid="file-row-stats">
        <span className="text-success">+{additions}</span>{" "}
        <span className="text-destructive">−{deletions}</span>
      </span>
    </>
  );
}
