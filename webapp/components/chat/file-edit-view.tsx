"use client";

import { type CSSProperties, useMemo } from "react";
import { DiffViewer, computeDiff } from "@/components/assistant-ui/diff-viewer";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { FileEditPartData } from "@/lib/grove/chat-turns";

/**
 * One file mutation a tool call performed, drawn as an ALWAYS-visible inline
 * diff. Unlike the "Used N tools" accordion (`ai-elements/tool.tsx`), a file
 * edit is the thing the reader came to see, so it never collapses — no
 * disclosure, no default-closed state, one card per edit.
 *
 * The card wears its OWN header (the vendored `DiffViewer` header is hidden)
 * so it can carry three things the reader wants at a glance:
 *   - the worktree-RELATIVE path (`displayPath`), truncating, with the full
 *     path in a tooltip (the wire sends both — see `FileEditView`);
 *   - a `+adds −dels` count in Grove's git-ref palette (the same `+`/`−`
 *     grammar the Work panel's Diff tab uses), computed once via the viewer's
 *     own `computeDiff` so the number can't drift from the rendered body.
 *
 * The body is deliberately NOT syntax-highlighted (that would mean pulling a
 * heavyweight highlighter Grove doesn't otherwise ship). Readability comes from
 * the GitHub/editor convention instead: the CODE text stays the normal mono
 * foreground (legible), while add/remove is carried by a soft row-background
 * tint plus a colored `+`/`−` gutter — three-coded (position · symbol · color)
 * so it survives colour-blindness. Line numbers ride the left gutter.
 *
 * `DiffViewer` reads its add/del hues from `--diff-*` CSS vars; we map those
 * onto Grove's `--ref-add`/`--ref-remove` (already theme-flipping, so one value
 * covers light and dark) and force the line CONTENT back to `foreground` so
 * only the gutter + tint are coloured, never the code text.
 *
 * Test seam: `data-testid="file-edit-card"`.
 */
const DIFF_TONE = {
  // The +/- gutter indicator takes these; the row tint takes the *-bg pair.
  "--diff-add-text": "var(--ref-add)",
  "--diff-add-text-dark": "var(--ref-add)",
  "--diff-del-text": "var(--ref-remove)",
  "--diff-del-text-dark": "var(--ref-remove)",
  "--diff-add-bg": "color-mix(in oklab, var(--ref-add) 12%, transparent)",
  "--diff-del-bg": "color-mix(in oklab, var(--ref-remove) 12%, transparent)",
} as CSSProperties;

export function FileEditCard({ path, displayPath, oldText, newText }: FileEditPartData) {
  // Reuse the viewer's own diff so the header count matches the body exactly;
  // the edits are small, so a second pass over the text is negligible.
  const { additions, deletions } = useMemo(
    () => computeDiff(oldText, newText),
    [oldText, newText],
  );

  return (
    <div data-testid="file-edit-card" className="not-prose w-full min-w-0" style={DIFF_TONE}>
      {/* House header: relative path (tooltip = full path) + ± count. */}
      <div
        data-testid="file-edit-header"
        className="flex items-center gap-3 rounded-t-xl border border-border/50 border-b-0 bg-muted/50 px-3 py-2"
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="min-w-0 truncate font-mono text-[13px] text-muted-foreground">
              {displayPath}
            </span>
          </TooltipTrigger>
          <TooltipContent className="font-mono text-xs">{path}</TooltipContent>
        </Tooltip>
        {(additions > 0 || deletions > 0) && (
          <span className="ml-auto flex shrink-0 items-center gap-2 font-mono text-xs tabular-nums">
            {additions > 0 && <span className="text-[var(--ref-add)]">+{additions}</span>}
            {deletions > 0 && <span className="text-[var(--ref-remove)]">−{deletions}</span>}
          </span>
        )}
      </div>
      <DiffViewer
        oldFile={{ content: oldText, name: displayPath }}
        newFile={{ content: newText, name: displayPath }}
        viewMode="unified"
        showLineNumbers
        showIcon={false}
        showStats={false}
        className={cn(
          // The single well: Grove's hairline + code-well radius + 13px mono,
          // fused to the house header above (no top rounding, shares its border).
          "rounded-t-none rounded-b-xl border-border/50 text-[13px]",
          // Hide the vendored header — the house header above replaces it.
          "[&_[data-slot=diff-viewer-header]]:hidden",
          // Line-number gutter: tabular figures so digits align.
          "[&_[data-slot=diff-viewer-line-number]]:tabular-nums",
          // Neutral code: force the line CONTENT to foreground so add/remove is
          // carried by the row tint + coloured gutter, not hard-to-read text.
          "[&_[data-slot=diff-viewer-content]]:text-foreground",
        )}
      />
    </div>
  );
}
