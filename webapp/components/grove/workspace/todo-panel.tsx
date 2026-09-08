"use client";

import { useState } from "react";
import { CheckIcon, CircleIcon, ListTodoIcon, Loader2Icon } from "lucide-react";

import { CardDisclosure, CardShell } from "@/components/grove/card";
import { Progress } from "@/components/ui/progress";
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
 *
 * A VENDORED ELEMENT THAT DRAWS ITS OWN SURFACE IS STANDALONE OR ABSENT. TodoList
 * owns its own heading and type scale, so this Grove card composes its rows rather
 * than nesting a second surface inside its disclosure.
 */
export function TodoPanel({ todo }: { todo: TodoListView }) {
  const [open, setOpen] = useState(false);
  const done = todo.items.filter((item) => item.status === "completed").length;
  const percent =
    todo.items.length === 0 ? 0 : Math.round((done / todo.items.length) * 100);

  return (
    <CardShell
      className="surface-raised"
      data-testid="todo-card"
      data-collapsed={!open}
      data-progress={`${done}/${todo.items.length}`}
    >
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        data-testid="todo-toggle"
        header
        summary={
          <>
            <ListTodoIcon
              aria-hidden
              className="size-4 shrink-0 text-content-tertiary"
            />
            <span className="shrink-0 text-sm font-medium">Plan</span>
            {/* Capped, and pushed to the count it belongs to: a bar spanning
                the whole row reads as a rule between two labels, not a meter. */}
            <Progress
              value={percent}
              className="ms-auto h-1.5 w-24 shrink-0"
              aria-label={`Plan progress: ${done} of ${todo.items.length} done`}
            />
            <span className="shrink-0 text-xs text-content-tertiary tabular-nums">
              {done}/{todo.items.length}
            </span>
          </>
        }
      >
        {/* A vendored element that draws its own surface is standalone or absent:
            nesting TodoList put a second heading and pre-density type inside this
            card. These are rows in this card's list instead. */}
        <ul
          className="max-h-56 divide-y divide-border overflow-y-auto"
          data-testid="todo-list"
        >
          {todo.items.map((item, index) => {
            const active = item.status === "in_progress";
            const complete = item.status === "completed";
            const ItemIcon = complete
              ? CheckIcon
              : active
                ? Loader2Icon
                : CircleIcon;
            const label = (active && item.active_form) || item.content;
            return (
              <li
                key={index}
                className="flex min-w-0 items-start gap-2 px-3 py-2 text-sm"
                data-testid="todo-item"
              >
                <ItemIcon
                  aria-hidden
                  className={
                    active
                      ? "mt-0.5 size-4 shrink-0 animate-spin text-content-secondary motion-reduce:animate-none"
                      : "mt-0.5 size-4 shrink-0 text-content-tertiary"
                  }
                />
                <span
                  className={
                    complete
                      ? "min-w-0 break-words text-content-tertiary line-through"
                      : "min-w-0 break-words text-content-secondary"
                  }
                >
                  {label}
                </span>
              </li>
            );
          })}
        </ul>
      </CardDisclosure>
    </CardShell>
  );
}
