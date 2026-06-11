"use client";

import { useMemo } from "react";
import { FancyAnsi } from "fancy-ansi";
import { cn } from "@/lib/utils";

/**
 * A colored terminal-snapshot view.
 *
 * The daemon emits `tmux capture-pane -e` — a *fixed character grid* with SGR
 * color escapes, no cursor motion. The faithful way to render that is an
 * ANSI→HTML pass into a real `<pre>`, NOT a terminal emulator: an emulator
 * (xterm.js) reflows at its column count (so wide captures word-wrap) and, with
 * its DOM renderer, draws box-drawing glyphs disconnected ("stitched"). A `<pre>`
 * keeps the page's own monospace font (so `─│┼` join into clean lines), never
 * wraps (`whitespace-pre`), and scrolls horizontally instead — exactly how this
 * looked before, now with color via `fancy-ansi`.
 *
 * `fancy-ansi` HTML-escapes the text and only injects `<span style>` color runs,
 * so the `<pre>`'s `textContent` is still the plain stripped output — the
 * a11y/screen-reader text and the unit/e2e seam. (When a true live PTY lands —
 * the deferred websocket work — xterm.js is the right tool *there*; for a polled
 * snapshot it is not.)
 */
const fancy = new FancyAnsi();

export function TerminalView({
  ansi,
  className,
  ariaLabel = "Live terminal output",
  textTestId,
  takenAt,
  emptyLabel,
}: {
  ansi: string | null;
  className?: string;
  ariaLabel?: string;
  /** testid for the `<pre>` (the unit/e2e seam). */
  textTestId?: string;
  /** stamped as `data-taken-at` (peek-snapshot freshness seam). */
  takenAt?: string | null;
  /** shown when there is no output yet. */
  emptyLabel?: string;
}) {
  const html = useMemo(() => (ansi ? fancy.toHtml(ansi) : null), [ansi]);

  return (
    // Native overflow so the grid scrolls on BOTH axes (a wide capture scrolls
    // horizontally instead of wrapping); `whitespace-pre` is the no-wrap.
    <div className={cn("overflow-auto", className)}>
      <pre
        data-testid={textTestId}
        data-taken-at={takenAt ?? ""}
        aria-label={ariaLabel}
        className="w-max min-w-full p-3 font-mono text-[12px] leading-[1.45] whitespace-pre text-foreground md:text-[13px]"
        {...(html ? { dangerouslySetInnerHTML: { __html: html } } : {})}
      >
        {html ? undefined : emptyLabel}
      </pre>
    </div>
  );
}
