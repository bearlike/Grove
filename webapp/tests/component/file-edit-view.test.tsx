import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { FileEditCard } from "@/components/chat/file-edit-view";
import { TooltipProvider } from "@/components/ui/tooltip";

// Pins the always-visible inline file-edit diff. Unlike the "Used N tools"
// accordion, a file edit renders unconditionally — no collapse, no toggle —
// showing both removed and added text. The house header carries the worktree-
// RELATIVE path plus a +adds/−dels count (git-ref colours); the body is neutral
// mono (readability by row-tint + gutter, not red/green text) with line numbers.
// The Tooltip primitive needs a provider ancestor, so every render wraps one.

function renderCard(props: React.ComponentProps<typeof FileEditCard>) {
  return render(
    <TooltipProvider>
      <FileEditCard {...props} />
    </TooltipProvider>,
  );
}

describe("FileEditCard — always-visible inline diff", () => {
  it("renders both removed and added lines as a semantic diff, no collapse toggle", () => {
    renderCard({
      path: "/home/u/.worktrees/x/lib/foo.ts",
      displayPath: "lib/foo.ts",
      oldText: "line one\nline two",
      newText: "line one\nline three",
    });

    const card = screen.getByTestId("file-edit-card");

    // Both sides of the change are visible — the whole point of the feature.
    expect(card).toHaveTextContent("line two"); // removed
    expect(card).toHaveTextContent("line three"); // added
    expect(card).toHaveTextContent("line one"); // unchanged context

    // The diff is semantic: a del line for the old, an add for the new.
    const del = card.querySelector('[data-slot="diff-viewer-line"][data-type="del"]');
    const add = card.querySelector('[data-slot="diff-viewer-line"][data-type="add"]');
    expect(del).toHaveTextContent("line two");
    expect(add).toHaveTextContent("line three");

    // Line numbers ride the left gutter (showLineNumbers on).
    expect(card.querySelector('[data-slot="diff-viewer-line-number"]')).not.toBeNull();

    // Always visible: no disclosure/collapse control (the path trigger is a
    // span via asChild, not a button).
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("header shows the worktree-relative path (not the full path) + a ± count", () => {
    renderCard({
      path: "/home/u/.worktrees/x/lib/foo.ts",
      displayPath: "lib/foo.ts",
      oldText: "line one\nline two",
      newText: "line one\nline three",
    });

    const header = screen.getByTestId("file-edit-header");
    // Relative path in the header; the full host path is NOT shown inline (it
    // rides the tooltip only).
    expect(header).toHaveTextContent("lib/foo.ts");
    expect(header).not.toHaveTextContent("/home/u/.worktrees/x");
    // One line removed + one added → +1 / −1, coloured via the ref palette.
    expect(header).toHaveTextContent("+1");
    expect(header).toHaveTextContent("−1"); // U+2212 minus, matching the Diff tab
  });

  it("renders a from-scratch write (empty old text) as all additions", () => {
    renderCard({
      path: "lib/new.ts",
      displayPath: "lib/new.ts",
      oldText: "",
      newText: "export const x = 1;",
    });

    const card = screen.getByTestId("file-edit-card");
    expect(card).toHaveTextContent("export const x = 1;");
    expect(card.querySelector('[data-slot="diff-viewer-line"][data-type="add"]')).not.toBeNull();
    // Nothing was removed.
    expect(card.querySelector('[data-slot="diff-viewer-line"][data-type="del"]')).toBeNull();
    // The count reflects additions only.
    const header = screen.getByTestId("file-edit-header");
    expect(header).toHaveTextContent("+1");
    expect(header).not.toHaveTextContent("−");
  });
});
