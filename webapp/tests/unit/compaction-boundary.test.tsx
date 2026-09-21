import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CompactionBoundary } from "@/components/grove/workspace/compaction-boundary";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GROVE_DATA_PART, messagesFromTurns } from "@/lib/grove/adapters";
import type { CompactionPartData } from "@/lib/grove/adapters";
import type { SessionTurnView } from "@/lib/grove/api";

/**
 * `AbbreviatedNumber` mounts a Radix `Tooltip` without self-providing (unlike
 * `Explain`, which does) — the app supplies one `TooltipProvider` at the root
 * (`providers.tsx`), and every other test exercising it wraps the same way
 * (`usage-page.test.tsx`, `usage-quota.test.tsx`).
 */
/**
 * A boundary with every field absent, so each test states ONLY the fields it is
 * about. Spelling the whole object at each call site meant a new honest-absence
 * field touched eleven unrelated tests and, worse, let one of them silently
 * assert against a default it never meant to choose.
 */
function boundary(over: Partial<CompactionPartData> = {}): CompactionPartData {
  return {
    trigger: null,
    at: null,
    droppedTokens: null,
    summary: "",
    durationMs: null,
    model: null,
    ...over,
  };
}

function render(node: ReactNode): string {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
}

describe("CompactionBoundary", () => {
  it("marks the cut with a scissors icon on a dotted line — the universal cut-here mark", () => {
    const markup = render(
      <CompactionBoundary
        data={boundary({ trigger: "manual", at: "2026-08-10T12:00:00Z", droppedTokens: 4200 })}
      />,
    );
    expect(markup).toContain("lucide-scissors");
    expect(markup).toContain("border-dashed");
  });

  it("never fabricates a trigger the harness never recorded", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary()} />,
    );
    expect(markup).toContain("trigger not recorded");
    expect(markup).not.toContain("Compacted automatically");
    expect(markup).not.toContain("Compacted manually");
  });

  it("names a manual and an automatic compaction distinctly", () => {
    const manual = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "manual" })} />,
    );
    expect(manual).toContain("Compacted manually");

    const auto = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "auto" })} />,
    );
    expect(auto).toContain("Compacted automatically");
  });

  it("renders an unknown instant rather than inventing one — RelativeTime's own contract", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "auto" })} />,
    );
    expect(markup).toContain(">unknown<");
  });

  it("omits the dropped-tokens clause entirely when unmeasured, never as 0", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "auto" })} />,
    );
    expect(markup).not.toContain("tokens dropped");
    expect(markup).not.toContain(">0<");
  });

  it("shows the dropped-token count, abbreviated, when the harness measured one", () => {
    const markup = render(
      <CompactionBoundary data={boundary({ trigger: "auto", droppedTokens: 42000 })} />,
    );
    expect(markup).toContain("42.0K");
    expect(markup).toContain("tokens dropped");
  });

  it("renders no disclosure at all when the harness carries no summary", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "auto" })} />,
    );
    expect(markup).not.toContain("compaction-summary");
    expect(markup).not.toContain("Post-compaction summary");
  });

  it("collapses the summary by default and states its line count, never the text, while closed", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary
        data={boundary({ trigger: "auto", summary: "Line one\nLine two\nLine three" })}
      />,
    );
    expect(markup).toContain('data-collapsed="true"');
    expect(markup).toContain("Post-compaction summary");
    expect(markup).toContain("3 lines");
    // Radix unmounts closed content — the same contract `FileEditPart` relies on.
    expect(markup).not.toContain("Line one");
  });

  it("gives the summary the full column — no width cap of its own", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary
        data={boundary({ trigger: "auto", summary: "Line one\nLine two" })}
      />,
    );
    const card = markup.slice(markup.indexOf('data-testid="compaction-summary"') - 400);
    expect(card).not.toMatch(/max-w-/);
    expect(card).not.toContain("self-center");
  });

  it("omits the duration and the model when the harness recorded neither", () => {
    // The whole meta row, so this cannot pass by the clauses merely being
    // absent from some other part of the markup.
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "auto" })} />,
    );
    expect(markup).not.toContain("compaction-duration");
    expect(markup).not.toContain("compaction-model");
    expect(markup).not.toContain("took ");
  });

  it("states how long the compaction took, in the same words a tool call uses", () => {
    // 263534ms is the real on-host figure: minutes, not a rounding detail.
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "manual", durationMs: 263_534 })} />,
    );
    expect(markup).toContain("4m 23s");
  });

  it("never renders a zero duration as an instantaneous compaction", () => {
    // A reported 0 IS a measurement and renders; the absence above does not.
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "auto", durationMs: 0 })} />,
    );
    expect(markup).toContain("compaction-duration");
    expect(markup).toContain("&lt;1s");
  });

  it("names the model that compacted, exactly as the provider reported it", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "auto", model: "claude-opus-5" })} />,
    );
    expect(markup).toContain("claude-opus-5");
  });

  it("explains the term rather than leaving a reader to infer it", () => {
    const markup = renderToStaticMarkup(
      <CompactionBoundary data={boundary({ trigger: "manual" })} />,
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
            duration_ms: 263_534,
            model: "claude-opus-5",
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
        durationMs: 263_534,
        model: "claude-opus-5",
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
      data: {
        trigger: null,
        at: null,
        droppedTokens: null,
        summary: "",
        // An older daemon omits these two entirely; neither may become a 0 or a
        // guessed model on the way through.
        durationMs: null,
        model: null,
      },
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
