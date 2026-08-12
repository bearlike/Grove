"use client";

import { useState } from "react";
import { ScissorsIcon } from "lucide-react";

import { CardDisclosure, CardShell } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";
import { RelativeTime } from "@/components/grove/relative-time";
import { AbbreviatedNumber } from "@/components/grove/usage/abbreviated-number";
import type { CompactionPartData } from "@/lib/grove/adapters";
import { CodeBlock } from "./code-block";

/**
 * The point a session's context was dropped and replaced by a summary — drawn
 * as the "cut here" mark every scissors icon already means, rather than
 * inventing a new glyph for it.
 *
 * ONE ENTRY IS ONE BOUNDARY, and a session commonly compacts several times
 * (Claude up to ~5, Codex up to ~20 in a long session) — this component makes
 * no assumption of singularity. It renders once per `data-compaction` part
 * `messagesFromTurns` emits, in the exact stream position the wire entry held,
 * so N compactions render as N marks with no coordination between them.
 *
 * HONESTY OVER TIDINESS, because a fabricated fact here is worse than a gap:
 * - `trigger: null` renders as a stated "trigger not recorded" — never
 *   defaulted to "automatic". Codex genuinely carries no trigger field, and
 *   guessing one would be inventing a fact the harness never reported.
 * - `at: null` reaches `RelativeTime`, which already renders "unknown" for a
 *   missing instant — this component adds no second null-check on top of it.
 * - `droppedTokens: null` OMITS the tokens clause entirely, the same way
 *   `ToolInvocationMeta` (`tool-call-part.tsx`) omits its duration span when a
 *   call carries none: an absent fact is left out of the line, never printed
 *   as "0 tokens dropped" or "not measured" — there is nothing wrong with the
 *   session that would make "not measured" the honest word here, the harness
 *   simply logs no delta for this event.
 * - `summary: ""` renders NO disclosure at all. An empty `CardDisclosure` that
 *   opens onto nothing is worse than no disclosure — it invites a click for
 *   zero content.
 *
 * THE DISCLOSURE REUSES `NotificationPart`'s exact anatomy (`data-parts.tsx`):
 * a `CardShell` + `CardDisclosure` collapsed by default, markdown rendered
 * through `CodeBlock` because assistant-ui's own markdown primitive reads its
 * text from message context and cannot render a string pulled out of a data
 * part's payload (see that component's docstring for the longer version). A
 * third bespoke collapsible here would be a third way to ask "show more" in
 * one transcript.
 */
export function CompactionBoundary({ data }: { data: CompactionPartData }) {
  const [open, setOpen] = useState(false);
  const summaryLines = data.summary ? data.summary.split("\n") : [];

  return (
    <div className="flex flex-col gap-2 py-1" data-testid="compaction-boundary">
      <div
        className="flex items-center gap-3"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Context compacted"
      >
        <span aria-hidden className="h-0 flex-1 border-t border-dashed border-border" />
        <ScissorsIcon aria-hidden className="size-3.5 shrink-0 text-content-tertiary" />
        <span aria-hidden className="h-0 flex-1 border-t border-dashed border-border" />
      </div>
      <div className="flex flex-wrap items-center justify-center gap-x-1.5 gap-y-1 text-xs text-content-tertiary">
        <Explain term="compaction">{triggerLabel(data.trigger)}</Explain>
        <span aria-hidden>·</span>
        <RelativeTime iso={data.at} />
        {data.droppedTokens !== null && (
          <>
            <span aria-hidden>·</span>
            <span className="tabular-nums" data-testid="compaction-dropped-tokens">
              <AbbreviatedNumber value={data.droppedTokens} /> tokens dropped
            </span>
          </>
        )}
      </div>
      {summaryLines.length > 0 && (
        <CardShell
          className="self-center w-full max-w-md"
          data-testid="compaction-summary"
          data-collapsed={!open}
        >
          <CardDisclosure
            open={open}
            onOpenChange={setOpen}
            data-testid="compaction-summary-toggle"
            contentClassName="px-3 pb-3"
            summary={
              <>
                <span className="min-w-0 flex-1 truncate text-xs text-content-primary">
                  Post-compaction summary
                </span>
                <span className="shrink-0 text-xs text-content-tertiary tabular-nums">
                  {summaryLines.length} {summaryLines.length === 1 ? "line" : "lines"}
                </span>
              </>
            }
          >
            <CodeBlock code={data.summary} language="markdown" />
          </CardDisclosure>
        </CardShell>
      )}
    </div>
  );
}

/** Three states, and the third is a real one — see the module docstring. */
function triggerLabel(trigger: CompactionPartData["trigger"]): string {
  switch (trigger) {
    case "manual":
      return "Compacted manually";
    case "auto":
      return "Compacted automatically";
    default:
      return "Compacted — trigger not recorded";
  }
}
