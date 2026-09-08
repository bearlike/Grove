import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ComposerBody, ExpandedComposer } from "@/components/grove/composer";

/**
 * The shared composer shell.
 *
 * Two surfaces compose it — the runtime-free landing brief and the
 * runtime-backed workspace reply — and the whole reason it is one module is
 * that they must not each arrive at their own paper or their own expand
 * dialog. What is pinned here is what a call site depends on: the vendored
 * slot that actually renders and the padding the browser suite measures the
 * send button against. The file row both stage is `grove/attachment-file`'s,
 * pinned in `attachment-file.test.tsx`.
 *
 * This suite is SSR-only by design (see `vitest.config.ts`), so it sees the
 * collapsed half of `ExpandedComposer` and not the opened dialog — that half
 * belongs to `tests/e2e/launch-expand.spec.ts`, which is a statement about the
 * live DOM and the accessibility tree.
 */

describe("ComposerBody", () => {
  it("renders the vendored composer bar, which is the slot the theme styles", () => {
    // The theme layer targets `[data-slot=composer-bar]` for typography and
    // focus, so a Grove wrapper drawing its own box would opt both surfaces out
    // of it silently.
    const html = renderToStaticMarkup(<ComposerBody />);
    expect(html).toContain('data-slot="composer-bar"');
  });

  it("holds the 11px inset that places the send edge 12px in", () => {
    // `tests/e2e/launch-expand.spec.ts` measures the send button's corner
    // against this bar: 11px of padding plus its 1px border is the 12px it
    // asserts, inline and expanded alike. Changing it here moves a number a
    // browser test pins.
    const html = renderToStaticMarkup(<ComposerBody />);
    expect(html).toMatch(/padding:\s*11px/);
    expect(html).toContain("border-surface-edge");
  });

  it("takes the semantic raised tuple, never a hand-rolled surface", () => {
    const html = renderToStaticMarkup(<ComposerBody />);
    expect(html).toContain("bg-surface-raised");
    expect(html).toContain("surface-raised");
  });

  it("merges a caller's layout instead of stacking a losing duplicate", () => {
    // The expanded dialog needs the bar to fill its height; raw concatenation
    // would emit both classes and let stylesheet order decide.
    const html = renderToStaticMarkup(<ComposerBody className="min-h-0 flex-1" />);
    expect(html).toContain("flex-1");
    expect(html).toContain("bg-surface-raised");
  });

  it("passes native div props through, so a caller keeps its drag state and ids", () => {
    const html = renderToStaticMarkup(<ComposerBody dragActive data-testid="launch-bar" />);
    expect(html).toContain('data-testid="launch-bar"');
    expect(html).toContain('data-drag-active="true"');
  });
});

describe("ExpandedComposer", () => {
  it("renders its one composer collapsed, with the expand control in the callback", () => {
    const html = renderToStaticMarkup(
      <ExpandedComposer
        title="Write the brief"
        description="Room for the whole thing."
        testId="launch"
        expandLabel="Expand"
      >
        {(expanded, inputRef, expandControl) => (
          <div data-expanded={expanded}>
            <textarea ref={inputRef} aria-label="Task brief" />
            {expandControl}
          </div>
        )}
      </ExpandedComposer>,
    );
    expect(html).toContain('data-expanded="false"');
    expect(html).toContain('data-testid="launch-expand"');
    expect(html).toContain("Expand");
  });

  // MUTATION-TESTED AND DELIBERATELY KEPT WEAK, which is worth stating so it is
  // not mistaken for the real guard. Replacing `{expanded ? null : composer}`
  // with an unconditional `{composer}` — the exact clone bug — leaves this
  // green, because a closed Radix dialog renders nothing on the server, so the
  // second copy cannot exist here to be counted. The live-DOM half is
  // `tests/e2e/launch-expand.spec.ts`'s "exactly one control named X". What
  // this case does pin is that the COLLAPSED render mounts the editor at all.
  it("renders the composer EXACTLY ONCE, so one draft never has two editors", () => {
    const html = renderToStaticMarkup(
      <ExpandedComposer
        title="Write the brief"
        description="Room."
        testId="launch"
        expandLabel="Expand"
      >
        {(_expanded, inputRef) => <textarea ref={inputRef} aria-label="Task brief" />}
      </ExpandedComposer>,
    );
    expect(html.match(/aria-label="Task brief"/g)).toHaveLength(1);
  });

  it("derives both browser-suite test ids from the one it is given", () => {
    // `launch-expand`/`launch-expanded` and
    // `workspace-composer-expand`/`workspace-composer-expanded` are pinned in
    // `tests/e2e/`, and each pair is that route's id plus the same two
    // suffixes.
    const html = renderToStaticMarkup(
      <ExpandedComposer
        title="Expand message"
        description="More room."
        testId="workspace-composer"
        expandLabel="Expand composer"
      >
        {(_expanded, _inputRef, expandControl) => (
          <div>
            <textarea aria-label="Message input" />
            {expandControl}
          </div>
        )}
      </ExpandedComposer>,
    );
    expect(html).toContain('data-testid="workspace-composer-expand"');
    expect(html).toContain("Expand composer");
  });
});
