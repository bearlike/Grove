"use client";

import { useState } from "react";
import { ListTodoIcon } from "lucide-react";

import { TodoList } from "@/components/elements/todo-list";
import { CardDisclosure, CardShell } from "@/components/grove/card";
import { Progress } from "@/components/ui/progress";
import { todoListProps } from "@/lib/grove/adapters";
import type { TodoListView } from "@/lib/grove/api";

/**
 * The agent's current plan, as a card that rides with the composer.
 *
 * It sits in the thread's viewport footer rather than at the top of the page
 * because that is where the plan is read from: you glance at what is left while
 * you type the next instruction. Fixed chrome charges its height against the
 * transcript on every turn, which is why it is collapsed by default — and why
 * collapse hides the DETAIL and never the SIGNAL: the count and the bar read in
 * both states, so a glance answers "how far along" without a click.
 *
 * IT NEEDS A PLANE, NOT JUST A BORDER. This floats over a live transcript, and
 * `--card` is the same white as `--background` in light mode — so a hairline was
 * the only thing separating an expanded plan from the text scrolling behind it,
 * and a list of twenty steps read as text on text. `surface-raised` is the
 * shared elevation token, which is also the composer's own shadow: the two sit
 * against each other and have to be lit the same way or they read as two
 * systems stacked by accident.
 */
export function TodoPanel({ todo }: { todo: TodoListView }) {
  const [open, setOpen] = useState(false);
  const { items } = todoListProps(todo);
  const done = items.filter((item) => item.status === "done").length;
  const percent = items.length === 0 ? 0 : Math.round((done / items.length) * 100);

  return (
    <CardShell
      className="surface-raised"
      data-testid="todo-card"
      data-collapsed={!open}
      data-progress={`${done}/${items.length}`}
    >
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        data-testid="todo-toggle"
        header
        summary={
          <>
            <ListTodoIcon aria-hidden className="size-4 shrink-0 text-content-tertiary" />
            <span className="shrink-0 text-sm font-medium">Plan</span>
            {/* Capped, and pushed to the count it belongs to: a bar spanning
                the whole row reads as a rule between two labels, not a meter. */}
            <Progress
              value={percent}
              className="ms-auto h-1.5 w-24 shrink-0"
              aria-label={`Plan progress: ${done} of ${items.length} done`}
            />
            {/* Collapse hides the DETAIL, never the SIGNAL — but expanded, the
                vendored list prints the same count in its own header, so
                showing it twice is noise rather than reassurance. */}
            {!open && (
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {done}/{items.length}
              </span>
            )}
          </>
        }
      >
        {/* Capped rather than free: a 25-step plan is normal, and an expanded
            card that eats the viewport pushes the composer off-screen. */}
        <div className="max-h-56 overflow-y-auto px-3 pt-1 pb-3">
          <TodoList items={items} className="max-w-none" />
        </div>
      </CardDisclosure>
    </CardShell>
  );
}
