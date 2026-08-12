"use client";

import { useEffect, useMemo, useRef } from "react";

import { JetBrainsMonoNerd } from "@/app/fonts";
import type { WorkspacePeekView } from "@/lib/grove/api";
import { useWorkspacePane } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import { paneHtml } from "./selectors";

/** How close to the bottom still counts as "following the tail", in pixels. */
const PIN_SLACK = 40;

/**
 * The real tmux pane — Grove's differentiator over an activity log.
 *
 * The pane is pushed over its own SSE stream while this tab is mounted, and the
 * polled peek's capture is the fallback for the gap before the first frame (and
 * for a browser without `EventSource`). `undefined` from the stream means "no
 * frame yet", `null` means the daemon reported an idle or dead pane — only the
 * former should fall back.
 *
 * WHY a `<pre>` and not the vendored `TerminalBlock`: that component takes
 * `lines: string[]`, so colour cannot reach it at all — a plain string has
 * nowhere to carry an SGR run. It is also a *demo* of a finished command (a
 * fixed `max-w-md`, an "exit 0" badge, a fade-in per line that would replay the
 * whole grid on every frame), where this is a live character grid. A `<pre>`
 * with `whitespace-pre` is the faithful surface for a `tmux capture-pane -e`
 * payload: box-drawing glyphs join into clean lines in one real monospace face,
 * nothing reflows, and an over-wide capture scrolls horizontally instead of
 * wrapping. A terminal emulator is the right tool only for a true live PTY.
 */
export function TerminalTab({ peek, active }: { peek: WorkspacePeekView; active: boolean }) {
  const streamed = useWorkspacePane(peek.state.id, active);
  const live = streamed !== undefined;
  const ansi = live ? streamed.ansi : peek.agent_snapshot;
  const html = useMemo(() => paneHtml(ansi), [ansi]);

  const scrollRef = useRef<HTMLDivElement>(null);
  // Follow the tail as frames arrive, but yield the moment the reader scrolls
  // up to read history — an auto-scroll that fights a deliberate scroll is
  // worse than none.
  const pinned = useRef(true);
  useEffect(() => {
    const pane = scrollRef.current;
    if (pane && pinned.current) pane.scrollTop = pane.scrollHeight;
  }, [html]);

  return (
    <div
      // The Nerd Font rides THIS subtree only, never <html>: its powerline and
      // private-use ranges are what agent TUIs print, and the un-subset face is
      // ~1 MB that app chrome must not pay for.
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col bg-background",
        JetBrainsMonoNerd.variable,
      )}
      data-testid="terminal-tab"
    >
      <div className="flex shrink-0 items-center gap-2 bg-muted/40 px-3 py-1.5">
        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground"
          title={peek.state.tmux_session}
        >
          {peek.state.tmux_session}
        </span>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 font-mono text-xs uppercase",
            live ? "text-success" : "text-muted-foreground",
          )}
          data-testid="terminal-capture-source"
        >
          <span
            aria-hidden
            className={cn(
              "size-1.5",
              live ? "bg-success motion-safe:animate-pulse" : "bg-muted-foreground",
            )}
          />
          {live ? "live" : "capture"}
        </span>
      </div>
      <div
        ref={scrollRef}
        onScroll={(event) => {
          const pane = event.currentTarget;
          pinned.current = pane.scrollHeight - pane.scrollTop - pane.clientHeight <= PIN_SLACK;
        }}
        className="min-h-0 flex-1 overflow-auto"
      >
        <pre
          aria-label="Live terminal output"
          data-testid="terminal-output"
          className="w-max min-w-full p-3 font-terminal text-xs leading-relaxed whitespace-pre text-foreground"
          {...(html ? { dangerouslySetInnerHTML: { __html: html } } : {})}
        >
          {html ? undefined : "No terminal output captured yet."}
        </pre>
      </div>
    </div>
  );
}
