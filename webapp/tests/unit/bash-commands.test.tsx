import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageBashCommands } from "@/components/grove/usage/bash-commands";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { UsageBashInsightView } from "@/lib/grove/api";

/**
 * The card's whole job is to not ship a lie about what the number means.
 * These pin the three honesty rules from the census, not just the happy path:
 * a row is never ranked without showing what is wrong with its own
 * measurement, and the leading-command semantics are visible, not buried.
 */

/**
 * `error_reportable_calls` is deliberately NOT equal to `calls` on every row:
 * only some harnesses record a structural tool-error flag, so a real scope
 * mixes measurable and silent calls in a ratio that varies per command. `npm`
 * carries the shipped defect's exact shape (half its calls silent), and `sed`
 * is the wholly-unmeasurable row.
 */
const REAL: UsageBashInsightView = {
  commands: [
    { executable: "uv", calls: 6196, total_ms: 112_788_000, avg_ms: 18_205, censored_calls: 0, background_calls: 0, error_calls: 0, error_reportable_calls: 6196 },
    { executable: "timeout", calls: 1500, total_ms: 97_889_000, avg_ms: 65_259, censored_calls: 812, background_calls: 0, error_calls: 0, error_reportable_calls: 1500 },
    { executable: "sleep", calls: 900, total_ms: 87_430_000, avg_ms: 97_144, censored_calls: 0, background_calls: 340, error_calls: 0, error_reportable_calls: 900 },
    { executable: "npm", calls: 2000, total_ms: 8_000_000, avg_ms: 4000, censored_calls: 0, background_calls: 0, error_calls: 9, error_reportable_calls: 1000 },
    { executable: "grep", calls: 21_592, total_ms: 40_684_000, avg_ms: 1884, censored_calls: 0, background_calls: 0, error_calls: 1200, error_reportable_calls: 21_592 },
    { executable: "sed", calls: 1400, total_ms: 1_400_000, avg_ms: 1000, censored_calls: 0, background_calls: 0, error_calls: 0, error_reportable_calls: 0 },
  ],
  unattributed_calls: 9126,
  unattributed_ms: 3_600_000,
  total_calls: 111_578,
  total_ms: 400_000_000,
};

const EMPTY: UsageBashInsightView = {
  commands: [],
  unattributed_calls: 0,
  unattributed_ms: 0,
  total_calls: 0,
  total_ms: 0,
};

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
}

/** The full `<tr>…</tr>` for one command's row, so an assertion can be scoped
 * to that row instead of matching anywhere on the page. */
function rowFor(html: string, executable: string): string {
  const match = html.match(new RegExp(`<tr[^>]*>(?:(?!</tr>)[\\s\\S])*?>${executable}<[\\s\\S]*?</tr>`));
  if (!match) throw new Error(`no row for "${executable}"`);
  return match[0];
}

describe("UsageBashCommands", () => {
  const html = render(<UsageBashCommands insight={REAL} failed={false} />);

  it("labels the number as time in calls LED BY a command, not time spent in it", () => {
    expect(html).toContain("led");
    expect(html).not.toContain("spent");
  });

  it("shows a censored row's cap inline, never only behind a tooltip", () => {
    // "timeout" carries 812 censored calls — the ranking is unearned if this
    // is not visible in the row itself, not just in a title attribute.
    expect(rowFor(html, "timeout")).toContain(">812 capped<");
  });

  it("shows a backgrounded row's blind spot inline", () => {
    // "sleep" carries 340 background calls — its true duration is invisible
    // to Grove, and the row has to say so rather than presenting it as fact.
    expect(rowFor(html, "sleep")).toContain(">340 background<");
  });

  it("leaves an uncensored, non-backgrounded row's measurement column unmarked", () => {
    expect(rowFor(html, "uv")).toContain(">—<");
  });

  it("shows the error rate as a percentage of that command's own calls", () => {
    // 1200 of grep's 21,592 calls errored — ranked by cost alone this row
    // looks clean, so the failure rate has to be its own visible column.
    expect(rowFor(html, "grep")).toContain(">5.6%<");
  });

  it("divides errors by the calls that COULD report one, not by every call", () => {
    // The shipped defect, in one row: 9 failures over npm's 1000 measurable
    // calls is 0.9%, but against all 2000 it published 0.5% — deflated by
    // exactly the row's silent share, confidently and with nothing on screen
    // to say so. Half of npm's calls ran under a harness that records no
    // structural tool-error flag, so they are not evidence of success.
    expect(rowFor(html, "npm")).toContain(">0.9%<");
    expect(rowFor(html, "npm")).not.toContain(">0.5%<");
  });

  it("states the real denominator on the row rather than leaving it implied", () => {
    // A rate whose denominator is smaller than the visible `Calls` column is
    // not self-explanatory — the row has to be able to say what it divided by.
    expect(rowFor(html, "npm")).toContain("9 of 1,000 calls that can report an error");
    expect(rowFor(html, "npm")).toContain("2,000 calls in total");
  });

  it("renders a MEASURED zero as 0%, never as an absent-value glyph", () => {
    // uv's 6,196 calls could all have reported an error and none did. That is
    // this column's most reassuring answer and it must not wear the same mark
    // as "no data" — the mirror of the unmeasured-is-never-zero rule.
    expect(rowFor(html, "uv")).toContain(">0%<");
    expect(rowFor(html, "uv")).not.toContain("not measured");
  });

  it("renders an UNMEASURABLE row as not measured, never as a confident 0%", () => {
    // Every one of sed's calls ran under a harness with no structural error
    // flag, so its zero errors mean "nobody could tell us" rather than
    // "nothing failed" — the one case where the dash-shaped answer is honest.
    const row = rowFor(html, "sed");
    expect(row).toContain("not measured");
    expect(row).not.toContain("0%");
    expect(row).toContain("ran under a harness that records tool errors");
  });

  it("leaves the measurement note blank for a clean row, independent of errors", () => {
    // The note column carries DURATION honesty (capped / background) only; a
    // row can be error-free and still have something to say here, and vice
    // versa. Pinned so the two axes do not get merged into one column.
    const cells = rowFor(html, "uv").match(/<td[^>]*>[\s\S]*?<\/td>/g) ?? [];
    expect(cells.some((cell) => cell.includes(">—<"))).toBe(true);
  });

  it("renders the executable as an identifier (mono, truncating), not a quantity", () => {
    const row = rowFor(html, "grep");
    expect(row).toContain("font-mono");
    expect(row).toContain("truncate");
  });

  it("states the unattributed share rather than silently dropping it", () => {
    expect(html).toContain("not ranked above");
    expect(html).toContain("9.1K");
  });

  it("orders rows by AVERAGE duration, not by the daemon's own total-cost ranking", () => {
    // The daemon returns uv/timeout/sleep/grep, ranked by total_ms. The card
    // must reorder to sleep/timeout/uv/grep (avg_ms descending) without
    // dropping or re-selecting any row.
    const order = ["sleep", "timeout", "uv", "grep"].map((name) => html.indexOf(`>${name}<`));
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it("formats calls through the shared abbreviation vocabulary", () => {
    expect(rowFor(html, "uv")).toContain("6.2K");
    expect(rowFor(html, "grep")).toContain("21.6K");
  });

  it("says nothing bold when no Bash calls were measured", () => {
    const emptyHtml = render(<UsageBashCommands insight={EMPTY} failed={false} />);
    expect(emptyHtml).toContain("Not measured: no Bash calls recorded");
    expect(emptyHtml).not.toContain("font-semibold");
  });

  it("shows a loading skeleton rather than the empty copy while the query is in flight", () => {
    const loadingHtml = render(<UsageBashCommands insight={undefined} failed={false} />);
    expect(loadingHtml).not.toContain("Not measured: no Bash calls recorded");
  });
});
