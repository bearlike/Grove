import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ToolGroup } from "@/components/ai-elements/tool";
import type { ToolCall } from "@/lib/grove/chat-turns";

// Pins the modern-chat-native tool surface: a BORDERLESS "Used N tools"
// ghost expander (no card, no hairline), collapsed by default, that
// reveals one digest row per call. The digest is load-bearing — the whole line
// reads inline (no second disclosure, since Grove's wire carries no separate
// args/result), the tool name bold and its target muted, in the code voice.

const CALLS: ToolCall[] = [
  { name: "Edit", detail: "app/page.tsx" },
  { name: "Agent(Explore):", detail: "map the webapp" },
];

describe("ToolGroup — borderless tool expander (#154)", () => {
  it("collapses to one 'Used N tools' ghost trigger, borderless, by default", () => {
    render(<ToolGroup calls={CALLS} data-testid="tool-group" />);

    const group = screen.getByTestId("tool-group");
    const trigger = screen.getByRole("button", { name: /Used 2 tools/ });
    // Ghost trigger: muted until hover, not a filled/outlined bar.
    expect(trigger.className).toContain("text-muted-foreground");
    // Borderless: no card chrome (frame / fill / rounding).
    expect(group.className).not.toContain("border");
    expect(group.className).not.toContain("bg-muted");
    expect(group.className).not.toContain("rounded-lg");
    // Collapsed: no digest rows mounted until asked.
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryAllByTestId("chat-tool")).toHaveLength(0);
  });

  it("uses the singular label for a lone call", () => {
    render(<ToolGroup calls={[CALLS[0]]} data-testid="tool-group" />);
    expect(screen.getByRole("button", { name: /Used 1 tool$/ })).toBeInTheDocument();
  });

  it("expands to a digest row per call — full line inline, name bold, in the code voice", () => {
    render(<ToolGroup calls={CALLS} data-testid="tool-group" />);
    fireEvent.click(screen.getByRole("button", { name: /Used 2 tools/ }));

    const rows = screen.getAllByTestId("chat-tool");
    expect(rows).toHaveLength(2);
    // The whole digest reads without a second click (load-bearing text) …
    expect(rows[0]).toHaveTextContent("Edit app/page.tsx");
    expect(rows[1]).toHaveTextContent("Agent(Explore): map the webapp");
    // … the tool name is the bold segment …
    expect(rows[0].querySelector(".font-medium")?.textContent).toBe("Edit");
    // … and the digest rides the code voice (mono), not sans.
    expect(rows[0].querySelector(".font-mono")).not.toBeNull();
  });
});
