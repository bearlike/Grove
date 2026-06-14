"use client";

// Vendored from Vercel AI Elements (registry.ai-sdk.dev/tool), now house code —
// slimmed and retyped. Upstream types against the AI SDK's `ToolUIPart` (the
// `ai` package, which must never enter package.json) and renders streamed
// input/output JSON through Collapsible + CodeBlock. Grove's wire carries a
// tool call as one digest line (`DigestEntryView` role "tool", e.g.
// "Edit app/page.tsx") — post-hoc and always completed — so the state machine,
// the radix Collapsible dep, and the JSON panes are all dropped; disclosure is
// a local `open` state and the content is the mono detail line.

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { CheckCircleIcon, ChevronDownIcon, WrenchIcon } from "lucide-react";
import type { HTMLAttributes } from "react";
import { useState } from "react";
import type { ToolCall } from "@/lib/grove/chat-turns";

export type ToolProps = HTMLAttributes<HTMLDivElement> & {
  /** The tool's name — rendered font-mono per the house git/code-identifier rule. */
  name: string;
  /** The rest of the digest line (arguments / target); empty hides the body. */
  detail?: string;
  /** Completed tool blocks auto-open; pass false to collapse by default. */
  defaultOpen?: boolean;
};

export const Tool = ({ className, name, detail, defaultOpen = true, ...props }: ToolProps) => {
  const [open, setOpen] = useState(defaultOpen);
  const hasBody = Boolean(detail);

  return (
    <div className={cn("not-prose w-full rounded-md border border-border", className)} {...props}>
      <button
        type="button"
        aria-expanded={open}
        disabled={!hasBody}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center justify-between gap-4 p-2 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          hasBody && "transition-colors hover:bg-muted/40",
        )}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <WrenchIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate font-mono text-xs font-medium text-foreground">{name}</span>
          {/* The detail rides the row inline (truncated) so subagent spawns —
              "Agent(Explore): map the webapp" — are legible without expanding;
              the disclosure still holds the full untruncated line. */}
          {hasBody && (
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
              {detail}
            </span>
          )}
          <Badge className="shrink-0 gap-1 rounded-full text-[10px]" variant="secondary">
            <CheckCircleIcon aria-hidden className="size-3 text-[var(--ref-add)]" />
            Completed
          </Badge>
        </div>
        {hasBody && (
          <ChevronDownIcon
            aria-hidden
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
          />
        )}
      </button>
      {hasBody && open && (
        <div className="border-t border-border bg-muted/40 p-2">
          <p className="break-words font-mono text-xs text-muted-foreground">{detail}</p>
        </div>
      )}
    </div>
  );
};

export type ToolGroupProps = HTMLAttributes<HTMLDivElement> & {
  /** A consecutive run of tool calls (chat-turns' `tools` item), oldest first. */
  calls: ToolCall[];
};

/**
 * One collapsed "N tool calls" row for a consecutive run — long Read/Bash/Edit
 * sprees stay one line until the reader asks. Expanding reveals one `Tool`
 * block per call (collapsed themselves; the name row is the summary, the
 * detail line is a second click away). Same disclosure conventions as `Tool`:
 * local `open` state, chevron, `aria-expanded`, conditional mount.
 */
export const ToolGroup = ({ className, calls, ...props }: ToolGroupProps) => {
  const [open, setOpen] = useState(false);
  const label = calls.length === 1 ? "1 tool call" : `${calls.length} tool calls`;

  return (
    <div className={cn("not-prose w-full rounded-md border border-border", className)} {...props}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center justify-between gap-4 p-2 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          "transition-colors hover:bg-muted/40",
        )}
      >
        <div className="flex min-w-0 items-center gap-2">
          <WrenchIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate font-mono text-xs font-medium text-foreground">{label}</span>
        </div>
        <ChevronDownIcon
          aria-hidden
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-border bg-muted/40 p-2">
          {calls.map((call, i) => (
            // The chat panel is this component's only consumer, so the inner
            // blocks carry its established `chat-tool` seam directly.
            <Tool
              key={i}
              name={call.name}
              detail={call.detail}
              defaultOpen={false}
              data-testid="chat-tool"
            />
          ))}
        </div>
      )}
    </div>
  );
};
