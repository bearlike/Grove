import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SessionOverview } from "@/components/grove/fleet/session-overview";
import { workspace } from "@/tests/fixtures/fleet";

const tree = readFileSync(
  new URL("../../components/grove/fleet/fleet-tree.tsx", import.meta.url),
  "utf8",
);

const css = readFileSync(
  new URL("../../app/globals.css", import.meta.url),
  "utf8",
);

describe("sidebar overview integration", () => {
  it("does not present HEAD as a base branch or a meaningful divergence", () => {
    const ws = workspace({ id: "root", state: "working" });
    ws.state.base_branch = "HEAD";
    ws.base_ahead = 42;
    ws.base_behind = 17;
    const html = renderToStaticMarkup(
      createElement(SessionOverview, { workspace: ws }),
    );
    expect(html).toContain("No separate base");
    expect(html).not.toContain("<code>HEAD</code>");
    expect(html).not.toContain("42 ahead");
    expect(html).not.toContain("17 behind");
  });
  it("stays a glance: the dense type step and a narrow measure", () => {
    const tooltip = css.slice(css.indexOf(".session-overview-tooltip {"));
    expect(tooltip.slice(0, tooltip.indexOf("}"))).toContain("width: min(24rem");
    expect(css).toMatch(/\.session-overview \{ font-size: var\(--text-xs\)/);
  });
  it("inverts the panel against the page, swapping the whole palette not just the fill", () => {
    const rule = (selector: string): string => {
      const start = css.indexOf(`${selector} {`);
      expect(start, `${selector} must have a rule`).toBeGreaterThan(-1);
      return css.slice(start, css.indexOf("}", start));
    };
    // Dark page, white panel; light page, dark panel.
    expect(rule(".session-overview-tooltip")).toContain("--overview-fill: oklch(0.2");
    expect(rule(".dark .session-overview-tooltip")).toContain("--overview-fill: oklch(1 0 0)");
    // A fill swap alone leaves each theme's state hues tuned for the OTHER
    // background: pale success-green on a white panel. Every token the panel
    // reads is re-declared from the opposite theme.
    for (const selector of [".session-overview-tooltip", ".dark .session-overview-tooltip"]) {
      for (const token of ["--content-primary", "--content-tertiary", "--success", "--warning", "--destructive", "--info", "--primary", "--border-source"]) {
        expect(rule(selector), `${selector} must re-declare ${token}`).toContain(`${token}:`);
      }
    }
  });
  it("loosens the reading line height above the dense step's own", () => {
    expect(css).toMatch(/\.session-overview \{ font-size: var\(--text-xs\); line-height: 1\.\d+;/);
  });
  it("keeps metric gutters independent of conditional full-width rows", () => {
    const overview = css.slice(
      css.indexOf(".session-overview-fields {"),
      css.indexOf("/* A rail row's fill"),
    );
    expect(overview).not.toContain("nth-child");
    expect(overview).toContain("column-gap: 0.75rem");
  });
  it("replaces the raw row tooltip with the session overview", () => {
    expect(tree).toContain(
      "<SessionOverview workspace={row.workspace} unread={unread}",
    );
    const tooltip = tree.slice(
      tree.indexOf('<TooltipContent side="left"'),
      tree.indexOf(
        "</TooltipContent>",
        tree.indexOf('<TooltipContent side="left"'),
      ),
    );
    expect(tooltip).not.toContain("SessionMetricDetails");
    expect(tooltip).not.toContain("Created");
    expect(tooltip).not.toContain("Last update");
    expect(tooltip).not.toContain("state.title");
  });
});
