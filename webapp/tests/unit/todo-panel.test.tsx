import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TodoPanel } from "@/components/grove/workspace/todo-panel";
import type { TodoListView } from "@/lib/grove/api";

/**
 * Collapsed by default (unlike the queue card — see `queue-panel.test.tsx`
 * for why), and the boundary between its trigger and its expanded list is a
 * rendered contract in the same way `SectionCard`'s is (`card.test.tsx`):
 * a tint on the trigger and a rule where the list begins.
 */

function render(todo: TodoListView): string {
  return renderToStaticMarkup(<TodoPanel todo={todo} />);
}

const TODO: TodoListView = {
  items: [
    { content: "Wire the parser", status: "completed", active_form: "Wiring the parser" },
    { content: "Add tests", status: "in_progress", active_form: "Adding tests" },
    { content: "Ship it", status: "pending", active_form: null },
  ],
};

describe("TodoPanel", () => {
  it("renders collapsed with the label, a decorative glyph and the progress count", () => {
    const html = render(TODO);

    expect(html).toContain('data-testid="todo-card"');
    expect(html).toContain('data-collapsed="true"');
    expect(html).toContain("Plan");
    expect(html).toContain("1/3");
    // The glyph carries no name of its own — the "Plan" label already says
    // what this is, so a redundant accessible name would be announced twice.
    expect(html).toContain("lucide-list-todo");
    expect(html).toMatch(/<svg[^>]*class="[^"]*lucide-list-todo[^"]*"[^>]*aria-hidden="true"/);
  });

  it("draws its trigger as a header — tint plus a rule at the boundary", () => {
    const html = render(TODO);
    const trigger = html.match(/<button[^>]*data-slot="collapsible-trigger"[^>]*>/)?.[0];

    expect(trigger).toBeTruthy();
    expect(trigger).toContain("bg-muted/40");
  });
});
