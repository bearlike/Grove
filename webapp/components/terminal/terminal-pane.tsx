"use client";

import { PeekSnapshot } from "@/components/workspace/peek-snapshot";
import { RelativeTime } from "@/components/shared/relative-time";
import { JetBrainsMonoNerd } from "@/app/fonts";
import { cn } from "@/lib/utils";

/**
 * The terminal surface: a title bar (the tmux target) and a live "capturing"
 * badge over the `PeekSnapshot` grid. The badge is honest — it reflects the
 * peek poll that feeds `snapshot` (every 2 s), so a viewer can trust the
 * freshness without us standing up a second SSE transport the page doesn't
 * need (the polled peek already carries the grid).
 *
 * Edge-to-edge, no frame (#124 density pass, tightened in the de-border pass
 * 2026-07-05): the pane's own rounded border (#92) is gone — its neighbor is
 * now the tabs strip's `border-b` (tabs mode) or the `ResizableHandle` (split
 * mode), never a border of its own, so no `overflow-clip` is needed either
 * (that was only clipping this pane's own rounded corners). The title bar
 * separates from the terminal grid below it by tone alone (`bg-muted/40` vs
 * the canvas's `bg-background`), not a hairline — same philosophy as the app
 * header. `bg-background` stays as the canvas tier so the well still reads
 * distinct from the transcript's `bg-card`. Renders `PeekSnapshot` with its
 * own border/rounding stripped so the surface is unbroken; the snapshot
 * keeps its `peek-snapshot` / `peek-snapshot-empty` seams and its `min-h`
 * floor for short viewports.
 */
export function TerminalPane({
  snapshot,
  takenAt,
  target,
  className,
}: {
  snapshot: string | null;
  takenAt: string | null;
  /** The tmux session the pane mirrors — shown in the title bar. */
  target: string;
  className?: string;
}) {
  return (
    <div
      data-testid="terminal-pane"
      className={cn(
        // The Nerd Font (`JetBrainsMonoNerd.variable`) is scoped to THIS
        // subtree so `font-terminal` on the inner `<pre>` resolves the
        // powerline/icon glyphs without dragging the face into app chrome
        // (never on <html>).
        "flex min-h-0 min-w-0 flex-1 flex-col bg-background",
        JetBrainsMonoNerd.variable,
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-2 bg-muted/40 px-2 py-1">
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground" title={target}>
          {target}
        </span>
        <span
          data-testid="terminal-capture-badge"
          className="inline-flex shrink-0 items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground"
        >
          <span
            aria-hidden
            className="size-1.5 rounded-full bg-[var(--status-active)] motion-safe:animate-pulse"
          />
          {takenAt ? <RelativeTime iso={takenAt} /> : "capturing"}
        </span>
      </div>
      <PeekSnapshot
        snapshot={snapshot}
        takenAt={takenAt}
        className="flex-1 rounded-none border-0 bg-background"
      />
    </div>
  );
}
