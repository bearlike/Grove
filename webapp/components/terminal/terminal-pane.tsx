"use client";

import { PeekSnapshot } from "@/components/workspace/peek-snapshot";
import { RelativeTime } from "@/components/shared/relative-time";
import { JetBrainsMonoNerd } from "@/app/fonts";
import { cn } from "@/lib/utils";

/**
 * The terminal surface: a framed pane with a title bar (the tmux target) and a
 * live "capturing" badge over the `PeekSnapshot` grid. The badge is honest —
 * it reflects the peek poll that feeds `snapshot` (every 2 s), so a viewer can
 * trust the freshness without us standing up a second SSE transport the page
 * doesn't need (the polled peek already carries the grid).
 *
 * Renders `PeekSnapshot` with its own border/rounding stripped so the frame is
 * this pane's; the snapshot keeps its `peek-snapshot` / `peek-snapshot-empty`
 * seams and its `min-h` floor for short viewports.
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
        // `overflow-clip`, never `overflow-hidden`: clip keeps the rounded
        // corners crisp WITHOUT becoming a scroll container, so Playwright's
        // scrollIntoView can't get trapped here (the scroll-trap lesson). The
        // PeekSnapshot inside owns the real scroll viewport.
        //
        // This is the one intentionally-DEEPER surface (#92): `bg-background`
        // is the canvas tier, which reads as an inset well against the
        // detail-panel's `bg-card` around it — a terminal-emulator feel,
        // distinct from the transcript's `bg-card` panel. The Nerd Font
        // (`JetBrainsMonoNerd.variable`) is scoped to THIS subtree so
        // `font-terminal` on the inner `<pre>` resolves the powerline/icon
        // glyphs without dragging the face into app chrome (never on <html>).
        "flex min-h-0 min-w-0 flex-1 flex-col overflow-clip rounded-md border border-border bg-background",
        JetBrainsMonoNerd.variable,
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/40 px-3 py-1.5">
        <span aria-hidden className="flex shrink-0 gap-1.5">
          <span className="size-2.5 rounded-full bg-[var(--status-error)]/70" />
          <span className="size-2.5 rounded-full bg-[var(--status-orphaned)]/70" />
          <span className="size-2.5 rounded-full bg-[var(--status-active)]/70" />
        </span>
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
