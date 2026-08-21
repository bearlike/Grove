import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TicketRow } from "@/components/grove/workspace/ticket-refs";
import {
  ticketGlyph,
  ticketState,
  ticketStateColour,
  ticketStatusTone,
  titleRuns,
  type TicketState,
} from "@/components/grove/workspace/selectors";
import type { TicketRef } from "@/lib/grove/api";

/**
 * Ticket state: the normalization, the tables over it, and the title renderer.
 *
 * The seam being pinned is that `TicketRef.status` is `string | null` on the
 * wire — free provider text, with no enum to key a table on. Grove's own
 * `TicketState` union is what creates compile-time totality downstream, so the
 * tests split accordingly: the NORMALIZER is tested against real tracker
 * vocabulary (open-ended input, documented fallback), and the TABLES are tested
 * for totality over the union.
 */
const ALL_STATES: readonly TicketState[] = ["open", "closed", "merged", "draft", "unknown"];

function ref(overrides: Partial<TicketRef> = {}): TicketRef {
  return {
    provider: "gitea",
    id: "42",
    kind: "issue",
    title: "Widgets render twice on resize",
    url: "https://example.invalid/42",
    status: "open",
    draft: false,
    assignee: null,
    ambiguous: false,
    ...overrides,
  };
}

const row = (ticket: TicketRef) =>
  renderToStaticMarkup(<TicketRow ticket={ticket} resolving={false} />);

describe("normalizing a tracker's own word", () => {
  it("folds the vocabularies trackers actually use onto one state", () => {
    for (const word of ["open", "opened", "reopened", "OPEN", " Open "]) {
      expect(ticketState(word), word).toBe("open");
    }
    for (const word of ["closed", "done", "completed"]) {
      expect(ticketState(word), word).toBe("closed");
    }
    expect(ticketState("merged")).toBe("merged");
    expect(ticketState("draft")).toBe("draft");
    expect(ticketState("open", true)).toBe("draft");
  });

  // The fallback is the point of the open-ended side: a tracker Grove has never
  // seen must produce a neutral row, not a crash and not a wrong colour.
  it("calls anything it does not recognise `unknown`", () => {
    expect(ticketState("triaged")).toBe("unknown");
    expect(ticketState(null)).toBe("unknown");
    expect(ticketState(undefined)).toBe("unknown");
    expect(ticketState("")).toBe("unknown");
  });
});

describe("the tables over the union are total", () => {
  it("answers a glyph for every state, on both kinds", () => {
    for (const state of ALL_STATES) {
      expect(ticketGlyph("issue", state), `issue/${state}`).toBeTruthy();
      expect(ticketGlyph("pull_request", state), `pr/${state}`).toBeTruthy();
    }
  });

  it("answers a colour and a badge tone for every state", () => {
    for (const state of ALL_STATES) {
      expect(ticketStateColour(state), state).toMatch(/^text-/);
      expect(ticketStatusTone(state), state).toBeTruthy();
    }
  });

  // The forge convention, which is the whole reason a reader recognises this
  // card without being taught it.
  it("maps the states onto the colours every forge uses", () => {
    expect(ticketStateColour("open")).toBe("text-success");
    expect(ticketStateColour("merged")).toBe("text-merged");
    expect(ticketStateColour("closed")).toBe("text-destructive");
    expect(ticketStateColour("draft")).toBe("text-content-tertiary");
  });

  // §4.7: the hue may never be the only thing separating two states. Distinct
  // glyphs per state is what makes the row survive greyscale.
  it("gives a pull request a different mark in each state", () => {
    const marks = ALL_STATES.map((s) => ticketGlyph("pull_request", s));
    expect(new Set([marks[0], marks[1], marks[2], marks[3]]).size).toBe(4);
  });
});

describe("the row", () => {
  it("colours the glyph by state and records the state on it", () => {
    const html = row(ref({ kind: "pull_request", status: "merged" }));
    expect(html).toContain('data-state="merged"');
    expect(html).toContain("text-merged");
  });

  it("marks the id as a destination", () => {
    expect(row(ref())).toContain("decoration-dotted");
  });

  it("names the state to a screen reader, not only in colour", () => {
    expect(row(ref({ status: "merged", kind: "pull_request" }))).toContain("merged");
  });
});

/**
 * Titles are INLINE ONLY. The failure this must never have is a title emitting
 * a block element into a table row, so the assertions are about what does NOT
 * appear as much as what does.
 */
describe("ticket titles", () => {
  it("renders backticked runs as code and leaves the rest alone", () => {
    expect(titleRuns("fix `state.branch` when HEAD")).toEqual([
      { text: "fix ", code: false },
      { text: "state.branch", code: true },
      { text: " when HEAD", code: false },
    ]);
  });

  it("keeps emoji and every other character verbatim", () => {
    const title = "✨ feat(ui): don't strip *these* #chars or _those_";
    expect(titleRuns(title)).toEqual([{ text: title, code: false }]);
  });

  // A lone tick is far likelier to be prose than an unclosed span, and opening
  // a span that never closes would swallow the rest of the line.
  it("treats an unpaired backtick as literal text", () => {
    expect(titleRuns("it`s fine")).toEqual([{ text: "it`s fine", code: false }]);
  });

  it("handles a title that is entirely one code span", () => {
    expect(titleRuns("`grove tickets attach`")).toEqual([
      { text: "grove tickets attach", code: true },
    ]);
  });

  it("emits no block element for any markdown-looking title", () => {
    const html = row(ref({ title: "# Heading\n- item\n\n> quote `code`" }));
    for (const block of ["<h1", "<ul", "<li", "<blockquote", "<p>", "<pre"]) {
      expect(html, block).not.toContain(block);
    }
    expect(html).toContain("<code");
  });
});
