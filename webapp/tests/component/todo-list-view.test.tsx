import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TodoListCard } from "@/components/chat/todo-list-view";
import type { TodoListView } from "@/lib/grove/types";

// Pins the composed todo/plan checklist card (#184) — the agent's current plan
// pinned above the composer. Each item renders its content plus a status glyph;
// the header carries a done/total progress count. An in-progress item prefers
// Claude's present-tense `active_form` phrasing when present.

const list: TodoListView = {
  items: [
    { content: "Read the code", status: "completed", active_form: "Reading the code" },
    { content: "Write the fix", status: "in_progress", active_form: "Writing the fix" },
    { content: "Run the gates", status: "pending", active_form: null },
  ],
};

describe("TodoListCard — pinned checklist", () => {
  it("renders every item with its status and a done/total progress count", () => {
    render(<TodoListCard todo={list} />);
    const card = screen.getByTestId("todo-card");
    expect(card).toHaveAttribute("data-progress", "1/3");

    const rows = screen.getAllByTestId("todo-item");
    expect(rows).toHaveLength(3);
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual([
      "completed",
      "in_progress",
      "pending",
    ]);

    // Completed + pending show their content; the in-progress row prefers the
    // present-tense active_form.
    expect(within(rows[0]).getByText("Read the code")).toBeTruthy();
    expect(within(rows[1]).getByText("Writing the fix")).toBeTruthy();
    expect(within(rows[2]).getByText("Run the gates")).toBeTruthy();
  });

  it("falls back to content for an in-progress item without active_form", () => {
    render(
      <TodoListCard
        todo={{ items: [{ content: "Ship it", status: "in_progress", active_form: null }] }}
      />,
    );
    const [row] = screen.getAllByTestId("todo-item");
    expect(within(row).getByText("Ship it")).toBeTruthy();
  });

  it("renders an all-done list with the full count and no error", () => {
    render(
      <TodoListCard
        todo={{ items: [{ content: "Done", status: "completed", active_form: null }] }}
      />,
    );
    expect(screen.getByTestId("todo-card")).toHaveAttribute("data-progress", "1/1");
  });
});
