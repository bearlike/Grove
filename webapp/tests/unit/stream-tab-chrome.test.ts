import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { STREAM_TAB_CHROME, tabChrome } from "@/components/grove/workspace/work-panel";
import { PANEL_TAB_VALUES } from "@/components/grove/workspace/selectors";

/**
 * The pane tab is named for what it SHOWS. A native workspace's pane is the
 * worker's protocol log, so "Terminal" would promise a prompt that is not
 * there; every other tab means the same thing in either mode. The tab VALUE
 * stays `terminal` in both, which is what keeps a bookmarked tab, the e2e
 * census and the `TabsContent` wiring untouched by the relabel.
 */
describe("the pane tab is Stream for a native workspace", () => {
  it("relabels only the pane tab, and only when the workspace is native", () => {
    expect(tabChrome("terminal", true)).toBe(STREAM_TAB_CHROME);
    expect(tabChrome("terminal", false).label).toBe("Terminal");
    for (const value of PANEL_TAB_VALUES.filter((v) => v !== "terminal")) {
      expect(tabChrome(value, true)).toBe(tabChrome(value, false));
    }
  });

  it("keeps the tab value the same in both modes", () => {
    const source = readFileSync(
      new URL("../../components/grove/workspace/work-panel.tsx", import.meta.url),
      "utf8",
    );
    // The strip maps the OFFERED census values and renders the chrome for each
    // by lookup, so no branch can mint a second value for the same surface.
    expect(source).toContain('tabChrome(value, privileged?.peek.state.native ?? false)');
    expect(source).not.toContain('value="stream"');
  });

  it("names the read-only stream on the tab's own sub-bar and empty state", () => {
    const source = readFileSync(
      new URL("../../components/grove/workspace/terminal-tab.tsx", import.meta.url),
      "utf8",
    );
    expect(source).toContain("event stream (read-only)");
    expect(source).toContain("No protocol frames captured yet.");
    expect(source).toContain('data-native={native ? "true" : undefined}');
  });
});
