import { describe, expect, it } from "vitest";

import { paneHtml, tabOnViewChange, visiblePane } from "@/components/grove/workspace/selectors";

/**
 * The pane payload is `tmux capture-pane -e` — a fixed character grid whose SGR
 * escapes carry meaning an agent TUI relies on (a failing test IS red). The
 * regression these tests exist for is the previous renderer, which stripped
 * every escape and handed the terminal a wall of one-colour text.
 *
 * `ESC` is built from its code point so no literal control byte lands in a
 * source file.
 */
const ESC = String.fromCharCode(27);
const sgr = (code: string, text: string) => `${ESC}[${code}m${text}${ESC}[0m`;

/** The plain text a screen reader (and any DOM assertion) would see. */
const textOf = (html: string) => html.replace(/<[^>]*>/g, "");

describe("paneHtml", () => {
  it("keeps colour — an SGR run becomes a styled span, never plain text", () => {
    const html = paneHtml(`${sgr("32", "PASS")} ${sgr("31", "FAIL")}`);
    expect(html).not.toBeNull();
    expect(html).toContain("--ansi-green");
    expect(html).toContain("--ansi-red");
    expect(html?.match(/<span/g)).toHaveLength(2);
  });

  it("carries bold and 256-colour runs, not just the basic eight", () => {
    const html = paneHtml(`${sgr("1;33", "warn")} ${sgr("38;5;208", "orange")}`);
    expect(html).toContain("font-weight");
    expect(html).toContain("--ansi-yellow");
    expect(html).toContain("rgb(255,135,0)");
  });

  it("leaves the plain text intact under the markup — that is the a11y seam", () => {
    const html = paneHtml(`before ${sgr("36", "cyan")} after`);
    expect(textOf(html ?? "")).toBe("before cyan after");
  });

  it("escapes the payload, so pane content can never inject markup", () => {
    const html = paneHtml(`<script>alert(1)</script> & "quoted"`);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("drops cursor-motion and erase sequences rather than printing them", () => {
    // A capture is a settled grid; there is no motion left to replay, and
    // rendering the raw bytes would show `[?25l` as text.
    const html = paneHtml(`${ESC}[?25l${ESC}[2J${ESC}[3;1Hplain`);
    expect(textOf(html ?? "")).toBe("plain");
  });

  it("trims tmux's trailing blank rows so the reader does not scroll an empty screen", () => {
    expect(textOf(paneHtml("row one\nrow two\n\n\n   \n") ?? "")).toBe("row one\nrow two");
  });

  it("preserves interior blank rows — they are grid structure, not padding", () => {
    expect(textOf(paneHtml("head\n\nbody") ?? "")).toBe("head\n\nbody");
  });

  it("reports absence as null, so the caller can show its own empty copy", () => {
    expect(paneHtml(null)).toBeNull();
    expect(paneHtml(undefined)).toBeNull();
    expect(paneHtml("")).toBeNull();
    // Whitespace-only is a live but blank pane: still nothing to render.
    expect(paneHtml("   \n  ")).toBeNull();
  });
});

/**
 * The switcher and the content read the SAME value, so "some pane is always
 * selected" is a property of this function rather than of the JSX. The bug it
 * exists for: pick Split, narrow the window past the breakpoint, and `split`
 * leaves the switcher while the state keeps it — no tab reports `aria-selected`
 * and the work panel unmounts, so the chosen pane silently disappears.
 */
describe("visiblePane", () => {
  it("keeps split while the viewport still offers it", () => {
    expect(visiblePane("split", true)).toBe("split");
  });

  it("falls back to work when split stops being offered", () => {
    expect(visiblePane("split", false)).toBe("work");
  });

  it("falls back to the WORK half, never the transcript", () => {
    // Someone in split view is there for the work panel; sending them to the
    // transcript discards the half they opened split to see.
    expect(visiblePane("split", false)).not.toBe("transcript");
  });

  it("leaves a single-pane choice alone at any width", () => {
    for (const offered of [true, false]) {
      expect(visiblePane("transcript", offered)).toBe("transcript");
      expect(visiblePane("work", offered)).toBe("work");
    }
  });

  it("always resolves to a pane the switcher is offering", () => {
    const offeredWhen = (splitOffered: boolean): readonly string[] =>
      splitOffered ? ["transcript", "work", "split"] : ["transcript", "work"];
    for (const view of ["transcript", "work", "split"] as const) {
      for (const offered of [true, false]) {
        expect(offeredWhen(offered)).toContain(visiblePane(view, offered));
      }
    }
  });
});

/**
 * Both Work and Split open on Info, and only on the way IN. Neither pane
 * silently drops a user on a bare terminal with no context — that used to be
 * Work-alone's own default, overridden because it read as "taken to the
 * wrong place" exactly like the Split regression this test file already
 * pinned.
 */
describe("tabOnViewChange", () => {
  it("lands on info entering split from any other view", () => {
    expect(tabOnViewChange("transcript", "split")).toBe("info");
    expect(tabOnViewChange("work", "split")).toBe("info");
  });

  it("lands on info entering work from any other view", () => {
    expect(tabOnViewChange("transcript", "work")).toBe("info");
    expect(tabOnViewChange("split", "work")).toBe("info");
  });

  it("leaves a tab chosen INSIDE the same pane alone", () => {
    // The switcher re-firing the pane it is already on must not throw away a
    // deliberate move to Terminal — that is the difference between a default
    // and an override.
    expect(tabOnViewChange("split", "split")).toBeNull();
    expect(tabOnViewChange("work", "work")).toBeNull();
  });

  it("changes nothing moving to transcript, from anywhere", () => {
    // Transcript has no work-panel tab of its own to land on.
    for (const from of ["transcript", "work", "split"] as const) {
      expect(tabOnViewChange(from, "transcript")).toBeNull();
    }
  });
});
