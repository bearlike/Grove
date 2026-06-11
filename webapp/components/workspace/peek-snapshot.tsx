"use client";

import { cn } from "@/lib/utils";
import { TerminalView } from "@/components/terminal/terminal-view";

interface Props {
  snapshot: string | null;
  takenAt: string | null;
  className?: string;
}

/**
 * The detail page's agent terminal preview. The daemon emits `tmux capture-pane
 * -e` (SGR intact); `TerminalView` renders it as a real colored terminal with an
 * accessible `<pre>` fallback (the unit/e2e text seam + screen-reader output).
 * Same engine the dashboard's `FocusedPane` uses — web and TUI stay siblings.
 */
export function PeekSnapshot({ snapshot, takenAt, className }: Props) {
  if (!snapshot) {
    return (
      <div
        data-testid="peek-snapshot-empty"
        className={cn(
          "flex h-full min-h-40 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground",
          className,
        )}
      >
        Waiting for agent output…
      </div>
    );
  }
  // Default: fill the parent, generous minimum for short viewports / mobile.
  // The detail page wraps us in a flex-fill column so on lg+ we grow into the
  // remaining viewport.
  return (
    <TerminalView
      ansi={snapshot}
      takenAt={takenAt}
      textTestId="peek-snapshot"
      ariaLabel="Agent terminal output"
      className={cn("h-full min-h-[28rem] rounded-md border border-border bg-card", className)}
    />
  );
}
