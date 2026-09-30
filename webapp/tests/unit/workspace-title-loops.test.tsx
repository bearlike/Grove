import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * A workspace's TITLE never rests on an ellipsis — every surface that names a
 * workspace loops its overflow with the rail's circular marquee (#876).
 *
 * Source census, because each surface needs a router, a query client or a
 * live workspace to render. The motion is measured in
 * `tests/e2e/sidebar-sessions.spec.ts`; the shared pause cannot be observed by
 * a static render at all (zustand serves its INITIAL state on the server), so
 * it is pinned here structurally and was verified on the built app.
 */
const header = readFileSync("components/grove/shell/shell-header.tsx", "utf8");
const card = readFileSync("components/grove/fleet/workspace-card.tsx", "utf8");
const overflow = readFileSync("components/grove/overflow-text.tsx", "utf8");

describe("a workspace title loops instead of truncating", () => {
  it("the page header draws its title in a scoped LoopingText, not a truncated span", () => {
    const title = header.slice(header.indexOf("<ScannedTextScope>"), header.indexOf("</ScannedTextScope>"));
    expect(title).toContain("<LoopingText");
    expect(title).toContain("{title ?? sectionFor(pathname).label}");
    expect(header).not.toMatch(/<span className="[^"]*\btruncate\b[^"]*">\s*\{title/);
  });

  it("gives the header title a ZERO flex basis, so looping cannot change its width", () => {
    // An auto basis sizes the title to its content, and a looping track is two
    // copies wide: the title then flips between looping and fitting forever.
    const title = header.slice(header.indexOf("<LoopingText"), header.indexOf("</LoopingText>"));
    expect(title).toMatch(/className="[^"]*\bflex-1\b/);
    expect(title).not.toMatch(/flex-\[[^\]]*auto\]|\bflex-auto\b|\bflex-initial\b/);
  });

  it("the fleet card draws its title in a scoped LoopingText, not the bounded pass", () => {
    const title = card.slice(card.indexOf("title={"), card.indexOf("description={"));
    expect(title).toContain("<ScannedTextScope>");
    expect(title).toContain("<LoopingText>{state.title}</LoopingText>");
    expect(title).not.toContain("<OverflowText>");
  });
});

describe("one pause control governs every scope on the page", () => {
  it("reads the pause from one store, never from state local to a scope", () => {
    const scope = overflow.slice(
      overflow.indexOf("export function ScannedTextScope("),
      overflow.indexOf("export function MarqueePauseButton("),
    );
    expect(overflow).toContain("const useMarqueePause = create<");
    expect(scope).toContain("useMarqueePause((state) => state.paused)");
    expect(scope).toContain("useMarqueePause((state) => state.setPaused)");
    expect(scope).not.toContain("useState(");
  });
});
