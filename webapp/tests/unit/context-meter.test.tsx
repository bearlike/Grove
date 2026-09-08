import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ContextMeter } from "@/components/grove/workspace/context-meter";

/**
 * The context meter draws ONLY what the harness reported.
 *
 * `context` is `null` until the first request completes (Claude Code) or for
 * a rollout too old to carry the window (Codex), and a meter at 0 % there
 * would claim an empty window on a session one turn from compaction. So the
 * negative is the load-bearing case, and the positive pins `aria-valuenow` —
 * the vendored `Progress` never forwards `value` to its Radix root, so a bar
 * that draws right can still read as indeterminate to a screen reader.
 */
describe("ContextMeter", () => {
  it("renders NOTHING when the harness reported no window", () => {
    expect(renderToStaticMarkup(<ContextMeter context={null} />)).toBe("");
    expect(renderToStaticMarkup(<ContextMeter context={undefined} />)).toBe("");
  });

  it("renders the reported fraction, announced as a value, with the exact counts", () => {
    // Verbatim from a Claude Code 2.1.270 statusLine payload after one turn.
    const html = renderToStaticMarkup(
      <ContextMeter context={{ size: 983616, used: 42704, used_fraction: 42704 / 983616 }} />,
    );
    expect(html).toContain('data-testid="context-meter"');
    expect(html).toContain('aria-valuenow="4"');
    expect(html).toContain('aria-valuetext="4% used"');
    expect(html).toContain("42,704 / 983,616 tokens");
    // A fabricated zero must never appear when a value was reported.
    expect(html).not.toContain('aria-valuenow="0"');
  });
});
