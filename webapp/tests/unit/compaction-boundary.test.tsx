import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CompactionBoundary } from "@/components/grove/workspace/compaction-boundary";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GROVE_DATA_PART, messagesFromTurns } from "@/lib/grove/adapters";
import type { SessionTurnView } from "@/lib/grove/api";

/**
 * `AbbreviatedNumber` mounts a Radix `Tooltip` without self-providing (unlike
 * `Explain`, which does) — the app supplies one `TooltipProvider` at the root
 * (`providers.tsx`), and every other test exercising it wraps the same way
 * (`usage-page.test.tsx`, `usage-quota.test.tsx`).
 */
function render(node: ReactNode): string {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
}

describe("CompactionBoundary", () => {
  it("marks the cut with a scissors icon on a dotted line — the universal cut-here mark", () => {
    const markup = render(
      <CompactionBoundary
        data={{ trigger: "manual", at: "2026-08-10T12:00:00Z", droppedTokens: 4200, summary: "" }}
      />,
    );
    expect(markup).toContain("lucide-scissors");
    expect(markup).toContain("border-dashed");
  });

  it("never fabricates a trigger the harness never recorded", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={{ trigger: null, at: null, droppedTokens: null, summary: "" }} />,
    );
    expect(markup).toContain("trigger not recorded");
    expect(markup).not.toContain("Compacted automatically");
    expect(markup).not.toContain("Compacted manually");
  });

  it("names a manual and an automatic compaction distinctly", () => {
    const manual = renderToStaticMarkup(
      <CompactionBoundary data={{ trigger: "manual", at: null, droppedTokens: null, summary: "" }} />,
    );
    expect(manual).toContain("Compacted manually");

    const auto = renderToStaticMarkup(
      <CompactionBoundary data={{ trigger: "auto", at: null, droppedTokens: null, summary: "" }} />,
    );
    expect(auto).toContain("Compacted automatically");
  });

  it("renders an unknown instant rather than inventing one — RelativeTime's own contract", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={{ trigger: "auto", at: null, droppedTokens: null, summary: "" }} />,
    );
    expect(markup).toContain(">unknown<");
  });

  it("omits the dropped-tokens clause entirely when unmeasured, never as 0", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={{ trigger: "auto", at: null, droppedTokens: null, summary: "" }} />,
    );
    expect(markup).not.toContain("tokens dropped");
    expect(markup).not.toContain(">0<");
  });

  it("shows the dropped-token count, abbreviated, when the harness measured one", () => {
    const markup = render(
      <CompactionBoundary data={{ trigger: "auto", at: null, droppedTokens: 42000, summary: "" }} />,
    );
    expect(markup).toContain("42.0K");
    expect(markup).toContain("tokens dropped");
  });

  it("renders no disclosure at all when the harness carries no summary", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={{ trigger: "auto", at: null, droppedTokens: null, summary: "" }} />,
    );
    expect(markup).not.toContain("compaction-summary");
    expect(markup).not.toContain("Post-compaction summary");
  });

  it("collapses the summary by default and states its line count, never the text, while closed", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary
        data={{
          trigger: "auto",
          at: null,
          droppedTokens: null,
          summary: "Line one\nLine two\nLine three",
        }}
      />,
    );
    expect(markup).toContain('data-collapsed="true"');
    expect(markup).toContain("Post-compaction summary");
    expect(markup).toContain("3 lines");
    // Radix unmounts closed content — the same contract `FileEditPart` relies on.
    expect(markup).not.toContain("Line one");
  });

  it("explains the term rather than leaving a reader to infer it", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={{ trigger: "manual", at: null, droppedTokens: null, summary: "" }} />,
    );
    expect(markup).toContain('data-testid="explain-compaction"');
  });
});

/**
 * `DigestEntryView` (types.gen.ts) does not yet declare `role: "compaction"` —
 * the daemon-side producer lands separately, in parallel with this UI — so
 * these fixtures go through `as unknown as SessionTurnView` rather than the
 * `SessionTurnView[]` literal typing `tests/fixtures/turns.ts` uses. That cast
 * is intentional and mirrors `compactionPartData`'s own narrowing seam in
 * `lib/grove/adapters/transcript.ts`; both need to drop it together once the
 * wire type catches up.
 */
describe("messagesFromTurns — the compaction role", () => {
  it("narrows the wire payload into CompactionPartData", () => {
    const turn = {
      user_text: "keep going",
      started_at: null,
      entries: [
        {
          role: "compaction",
          text: "",
          compaction: {
            trigger: "manual",
            at: "2026-08-09T10:00:00Z",
            dropped_tokens: 15000,
            summary: "Earlier turns summarized.",
          },
        },
      ],
    } as unknown as SessionTurnView;

    const messages = messagesFromTurns([turn]);
    const boundary = messages.find(
      (m) => Array.isArray(m.content) && m.content[0]?.type === GROVE_DATA_PART.compaction,
    );
    expect(boundary?.content[0]).toMatchObject({
      data: {
        trigger: "manual",
        at: "2026-08-09T10:00:00Z",
        droppedTokens: 15000,
        summary: "Earlier turns summarized.",
      },
    });
  });

  it("renders even with an empty digest line, unlike every other role", () => {
    const turn = {
      user_text: "keep going",
      started_at: null,
      entries: [
        {
          role: "compaction",
          text: "",
          compaction: { trigger: null, at: null, dropped_tokens: null, summary: "" },
        },
      ],
    } as unknown as SessionTurnView;

    // Only the user head plus the compaction boundary — an empty `text` on any
    // other role is dropped, and a compaction boundary must not be.
    expect(messagesFromTurns([turn])).toHaveLength(2);
  });

  it("defaults every field honestly when the wire payload is missing entirely", () => {
    const turn = {
      user_text: "keep going",
      started_at: null,
      entries: [{ role: "compaction", text: "" }],
    } as unknown as SessionTurnView;

    const messages = messagesFromTurns([turn]);
    const boundary = messages.find(
      (m) => Array.isArray(m.content) && m.content[0]?.type === GROVE_DATA_PART.compaction,
    );
    expect(boundary?.content[0]).toMatchObject({
      data: { trigger: null, at: null, droppedTokens: null, summary: "" },
    });
  });

  it("supports several boundaries in one turn — Codex compacts up to ~20 times in a session", () => {
    const turn = {
      user_text: null,
      started_at: null,
      entries: Array.from({ length: 3 }, () => ({
        role: "compaction",
        text: "",
        compaction: { trigger: "auto", at: null, dropped_tokens: null, summary: "" },
      })),
    } as unknown as SessionTurnView;

    const boundaries = messagesFromTurns([turn]).filter(
      (m) => Array.isArray(m.content) && m.content[0]?.type === GROVE_DATA_PART.compaction,
    );
    expect(boundaries).toHaveLength(3);
  });
});
