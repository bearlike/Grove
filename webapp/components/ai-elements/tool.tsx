"use client";

// The transcript's tool-call surface, modern-chat-native. The
// upstream AI-Elements/assistant-ui template renders a tool call as a
// BORDERLESS expander — a ghost trigger ("Used tool: <b>name</b>") over an
// indented content well — not an outlined card. Grove adopts that grammar.
//
// Grove's divergence from the template's two-level shape (trigger → args/result
// <pre>): the wire carries a tool call as ONE post-hoc digest line
// (`DigestEntryView` role "tool", e.g. "Edit app/page.tsx" — always completed,
// no separate args/result payload). So the leaf row IS the digest, with nothing
// further to disclose; the single collapse level is the "Used N tools" group
// (a lone tool run must not dump detail into the scroll by default). If the
// wire ever gains a structured args/result payload,
// THAT is when a per-row second disclosure earns its chevron.
//
// Borderless throughout: no frame, no hairline, no bordered card — a transcript
// row separates from its neighbors by spacing and a soft muted fill, never a
// line (design-system.md "Don't draw borders on resting transcript blocks").

import { cn } from "@/lib/utils";
import { ChevronDownIcon, CircleCheckIcon, WrenchIcon } from "lucide-react";
import type { HTMLAttributes } from "react";
import { useState } from "react";
import type { ToolCall } from "@/lib/grove/chat-turns";

export type ToolGroupProps = HTMLAttributes<HTMLDivElement> & {
  /** A consecutive run of tool calls (chat-turns' `tools` item), oldest first. */
  calls: ToolCall[];
};

/**
 * One consecutive tool run as a single borderless expander — a "Used N tools"
 * ghost trigger (the template's grammar) collapsed by default, so a long
 * Read/Bash/Edit spree stays one calm line until the reader asks. Expanding
 * reveals the digest of each call in an indented column. Disclosure is plain
 * local state + a conditional mount (no radix Collapsible, no new dep).
 */
export const ToolGroup = ({ className, calls, ...props }: ToolGroupProps) => {
  const [open, setOpen] = useState(false);
  const label = `Used ${calls.length} ${calls.length === 1 ? "tool" : "tools"}`;

  return (
    <div className={cn("not-prose w-full", className)} {...props}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          // Ghost trigger: w-fit so it never reads as a full-width bar; the
          // count is prose (sans), the digests it reveals are code (mono).
          "flex w-fit items-center gap-2 rounded-md py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      >
        <WrenchIcon aria-hidden className="size-4 shrink-0" />
        <span>{label}</span>
        {/* ChevronDown points right (collapsed) → down (open) — the
            template's -rotate-90-when-closed convention. */}
        <ChevronDownIcon
          aria-hidden
          className={cn("size-4 shrink-0 transition-transform", !open && "-rotate-90")}
        />
      </button>
      {open && (
        <div className="flex flex-col gap-1 ps-6 pt-1 pb-1">
          {calls.map((call, i) => (
            <ToolDigestRow key={i} name={call.name} detail={call.detail} />
          ))}
        </div>
      )}
    </div>
  );
};

/**
 * One tool call's digest — the terminal leaf of the tool surface (see the file
 * header on why there is no second disclosure). A small completed-check mark
 * (Grove's wire only ever emits a call post-hoc, so every call reads as done),
 * then the digest as code: the tool name bold, its target/arguments muted. The
 * full line stays in the DOM and wraps rather than truncating — the digest text
 * is load-bearing (a subagent spawn's "Agent(Explore): map the webapp" must read
 * without a click). Carries the established `chat-tool` seam.
 */
function ToolDigestRow({ name, detail }: { name: string; detail: string }) {
  return (
    <div data-testid="chat-tool" className="flex items-start gap-2">
      <CircleCheckIcon
        aria-hidden
        className="mt-0.5 size-3.5 shrink-0 text-[var(--ref-add)]"
      />
      <span className="min-w-0 break-words font-mono text-[13px] leading-relaxed">
        <span className="font-medium text-foreground">{name}</span>
        {detail && <span className="text-muted-foreground"> {detail}</span>}
      </span>
    </div>
  );
}
