"use client";

import { X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { TerminalView } from "@/components/terminal/terminal-view";
import { useWorkspacePane } from "@/lib/grove/hooks";

/**
 * The dashboard's single live focus pane (#19).
 *
 * Streams one workspace's agent pane over SSE (`useWorkspacePane` opens an
 * `EventSource` to `GET /workspaces/{id}/pane/stream`, diff-guarded server-side)
 * and renders it via `<TerminalView>` (ANSI→HTML into a real `<pre>` through
 * `fancy-ansi`, color intact — NOT a terminal emulator). Exactly one is mounted
 * at a time (the page tracks a single `liveId`), disposed on close/blur, so the
 * wall never pays for N live streams. The `<pre>` keeps the output in the DOM
 * for screen readers + the e2e seam.
 */
export function FocusedPane({
  workspaceId,
  title,
  onClose,
}: {
  workspaceId: string;
  title: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useWorkspacePane(workspaceId, true);
  const emptyLabel = isLoading ? "connecting…" : "(no output — the agent pane is idle)";

  return (
    <Card className="flex flex-col gap-2 p-3" data-testid="focused-pane">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          Live pane
        </span>
        <span className="truncate text-sm font-medium" title={title}>
          {title}
        </span>
        <span
          className="size-2 shrink-0 rounded-full bg-[var(--agent-working)] motion-safe:animate-pulse"
          aria-hidden
        />
        <Button
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          aria-label="Close live pane"
          onClick={onClose}
        >
          <X />
        </Button>
      </div>
      <TerminalView
        ansi={data?.ansi ?? null}
        ariaLabel={`Live terminal for ${title}`}
        emptyLabel={emptyLabel}
        className="h-64 rounded-md border border-border bg-muted/40"
      />
    </Card>
  );
}
