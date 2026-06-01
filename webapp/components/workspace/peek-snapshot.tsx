"use client";
import { useEffect, useMemo, useRef } from "react";
import { cn } from "@/lib/utils";
import { stripAnsi } from "@/lib/grove/ansi";
import { ScrollArea } from "@/components/ui/scroll-area";

interface Props {
  snapshot: string | null;
  takenAt: string | null;
  className?: string;
}

export function PeekSnapshot({ snapshot, takenAt, className }: Props) {
  const ref = useRef<HTMLPreElement | null>(null);
  // The daemon emits `tmux capture-pane -e` output (SGR escapes intact).
  // The TUI renders them via Rich; the webapp strips them — color isn't
  // load-bearing for a glance dashboard, structure is.
  const text = useMemo(() => (snapshot ? stripAnsi(snapshot) : null), [snapshot]);
  // Auto-scroll to bottom only when user is already near the bottom (≤ 40px)
  // — never fight a deliberate scroll-up.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const distance = el.scrollHeight - (el.scrollTop + el.clientHeight);
    if (distance < 40) {
      el.scrollTop = el.scrollHeight;
    }
  }, [text]);

  if (!text) {
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
  // Default: fill the parent, with a generous minimum for short viewports
  // and mobile (where the panel stacks). The detail page wraps us in a
  // flex-fill column so on lg+ we grow into the remaining viewport.
  return (
    <ScrollArea className={cn("h-full min-h-[28rem] rounded-md border border-border bg-card", className)}>
      <pre
        ref={ref}
        data-testid="peek-snapshot"
        data-taken-at={takenAt ?? ""}
        className="min-h-40 p-3 text-[12px] leading-[1.45] md:text-[13px] font-mono whitespace-pre"
      >
        {text}
      </pre>
    </ScrollArea>
  );
}
