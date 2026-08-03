import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TodoListCard } from "@/components/chat/todo-list-view";
import type { TodoListView } from "@/lib/grove/types";

// Pins the composed todo/plan checklist card, COLLAPSED BY DEFAULT since it
// sits in the composer's fixed chrome, not the scroll. The
// done/total progress count is the signal that must survive collapse — it
// renders in the header in BOTH states — while the item rows are absent from
// the DOM until the `todo-toggle` header button expands the card. Each item
// renders its content plus a status glyph; an in-progress item prefers
// Claude's present-tense `active_form` phrasing when present.

const list: TodoListView = {
  items: [
    { content: "Read the code", status: "completed", active_form: "Reading the code" },
    { content: "Write the fix", status: "in_progress", active_form: "Writing the fix" },
    { content: "Run the gates", status: "pending", active_form: null },
  ],
};

describe("TodoListCard — pinned checklist", () => {
  it("renders collapsed by default with the progress count visible but no items mounted", () => {
    render(<TodoListCard todo={list} />);
    const card = screen.getByTestId("todo-card");
    expect(card).toHaveAttribute("data-collapsed", "true");
    expect(card).toHaveAttribute("data-progress", "1/3");

    const toggle = screen.getByTestId("todo-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(toggle).getByText("1/3")).toBeInTheDocument();

    expect(screen.queryAllByTestId("todo-item")).toHaveLength(0);
  });

  it("expands on toggle click to reveal every item with its status, in order", async () => {
    const user = userEvent.setup();
    render(<TodoListCard todo={list} />);
    const toggle = screen.getByTestId("todo-toggle");

    await user.click(toggle);

    const card = screen.getByTestId("todo-card");
    expect(card).toHaveAttribute("data-collapsed", "false");
    expect(toggle).toHaveAttribute("aria-expanded", "true");

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

    // Clicking again re-collapses and unmounts the rows.
    await user.click(toggle);
    expect(card).toHaveAttribute("data-collapsed", "true");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryAllByTestId("todo-item")).toHaveLength(0);
  });

  it("falls back to content for an in-progress item without active_form", async () => {
    const user = userEvent.setup();
    render(
      <TodoListCard
        todo={{ items: [{ content: "Ship it", status: "in_progress", active_form: null }] }}
      />,
    );
    await user.click(screen.getByTestId("todo-toggle"));
    const [row] = screen.getAllByTestId("todo-item");
    expect(within(row).getByText("Ship it")).toBeTruthy();
  });

  it("renders an all-done list with the full count in the collapsed header", () => {
    render(
      <TodoListCard
        todo={{ items: [{ content: "Done", status: "completed", active_form: null }] }}
      />,
    );
    expect(screen.getByTestId("todo-card")).toHaveAttribute("data-progress", "1/1");
  });
});
