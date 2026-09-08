import { describe, expect, it } from "vitest";

import { opensInNewTab } from "@/components/grove/workspace/new-tab-links";

/**
 * Which hrefs in an agent's answer are a NAVIGATION AWAY, and therefore have to
 * leave the reader's session where it is.
 *
 * A Grove workspace page is a streaming transcript, a scroll position twenty
 * thousand pixels down, a terminal attached and a work tab you chose — so a
 * citation that replaces it is a loss rather than a navigation, and the agent
 * writing the link has no way to know what it costs you to follow.
 *
 * **THE RULE IS AN ALLOWLIST, AND THAT IS THE SAFETY ARGUMENT.** A denylist of
 * `javascript:` and `data:` is a list somebody has to keep complete; "http,
 * https, or a path" is a rule that cannot silently admit a scheme nobody
 * thought of. Sanitization still belongs to the renderer above — this only
 * declines to decorate what it does not recognise, so an unsafe href gets no
 * `target` rather than a `target` and a shrug.
 */
describe("opensInNewTab", () => {
  it("sends external links away", () => {
    expect(opensInNewTab("https://example.com/docs")).toBe(true);
    expect(opensInNewTab("http://example.com")).toBe(true);
    expect(opensInNewTab("HTTPS://EXAMPLE.COM")).toBe(true);
    expect(opensInNewTab("//example.com/protocol-relative")).toBe(true);
  });

  it("sends RELATIVE and same-origin links away too, which is the case a naive rule misses", () => {
    // A response citing another Grove page is exactly where the reader loses
    // the session they were reading, and an "external links only" rule would
    // let precisely that one through.
    expect(opensInNewTab("/fleet")).toBe(true);
    expect(opensInNewTab("./sibling")).toBe(true);
    expect(opensInNewTab("../up")).toBe(true);
    expect(opensInNewTab("docs/setup.md")).toBe(true);
  });

  it("leaves an in-page fragment alone, because a scroll is not a navigation", () => {
    expect(opensInNewTab("#section")).toBe(false);
    expect(opensInNewTab("")).toBe(false);
    expect(opensInNewTab(null)).toBe(false);
    expect(opensInNewTab(undefined)).toBe(false);
    expect(opensInNewTab("   ")).toBe(false);
  });

  it("decorates no scheme it does not recognise", () => {
    // `mailto:`/`tel:` hand off to something with no tab to open. The rest are
    // the ones an allowlist exists for — none of them gets a `target`, and none
    // of them had to be named to be excluded.
    for (const href of [
      "mailto:someone@example.com",
      "tel:+15550000",
      "javascript:alert(1)",
      "JavaScript:alert(1)",
      "data:text/html,<script>",
      "vbscript:msgbox",
      "file:///etc/passwd",
      "ftp://example.com",
    ]) {
      expect(opensInNewTab(href), href).toBe(false);
    }
  });
});
