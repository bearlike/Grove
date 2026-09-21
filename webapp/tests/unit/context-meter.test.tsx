import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ContextMeter } from "@/components/grove/workspace/context-meter";
import { contextTone } from "@/lib/grove/adapters/context";

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

  it("announces occupancy from the exact counts with two decimal precision", () => {
    // Verbatim from a Claude Code 2.1.270 statusLine payload after one turn.
    const html = renderToStaticMarkup(
      <ContextMeter context={{ size: 983616, used: 42704, used_fraction: 42704 / 983616 }} />,
    );
    expect(html).toContain('data-testid="context-meter"');
    expect(html).toContain('aria-valuenow="4.34"');
    expect(html).toContain('aria-valuetext="4.34% used"');
    expect(html).toContain("42,704 / 983,616 tokens");
    // A fabricated zero must never appear when a value was reported.
    expect(html).not.toContain('aria-valuenow="0"');
  });
});

describe("the meter says its figure once and colours its bar by headroom", () => {
  it("prints ONE detail line, with the exact count in the tooltip rather than beneath", () => {
    const html = renderToStaticMarkup(
      <ContextMeter context={{ size: 1_000_000, used: 121_083, used_fraction: 0.121 }} />,
    );
    expect(html).toContain('data-testid="context-detail"');
    expect(html).not.toContain('data-testid="context-exact-detail"');
    expect(html).not.toMatch(/>[^<]*1,000,000 tokens</);
    expect(html).toMatch(/title="[^"]*121,083 \/ 1,000,000/);
  });

  it.each([
    [12, "bg-info"],
    [49.9, "bg-info"],
    [50, "bg-success"],
    [79.9, "bg-success"],
    [80, "bg-warning"],
    [99.9, "bg-warning"],
    [100, "bg-destructive"],
    [140, "bg-destructive"],
  ])("tones %s%% as %s, through the indicator selector", (percent, tone) => {
    // Every boundary from both sides: a ramp with a wrong `>=` passes at the
    // midpoints and fails only here. The rule returns a NAME now (the footer
    // paints text with the same thresholds), so assert on the name.
    expect(`bg-${contextTone(percent)}`).toBe(tone);
  });

  it("paints the rendered bar with that tone", () => {
    const html = renderToStaticMarkup(
      <ContextMeter context={{ size: 100, used: 85, used_fraction: 0.85 }} />,
    );
    const root = /<div[^>]*data-slot="progress"[^>]*>/.exec(html)?.[0] ?? "";
    expect(root).toContain("bg-warning");
  });
});
