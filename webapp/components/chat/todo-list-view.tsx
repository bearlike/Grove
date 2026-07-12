"use client";

import { CircleCheckIcon, CircleDotIcon, CircleIcon, ListChecksIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TodoItemView, TodoListView } from "@/lib/grove/types";

/**
 * The agent's CURRENT todo/plan list (Claude `TodoWrite` / Codex `update_plan`,
 * #184), drawn as a composed checklist card pinned above the composer — the
 * clearest signal of what a running agent is doing. assistant-ui owns the
 * thread/composer chrome, not domain cards, so (like `PendingQuestionCard` /
 * `FileEditCard`) this is a plain composed card on the existing design-system
 * atoms + tokens: no bespoke primitive.
 *
 * Only the LATEST list is shown (the panel derives it via `latestTodoFromTurns`),
 * so historical updates never stack — a `TodoWrite` is a full rewrite of the
 * list, and the card reflects the newest state. Each item's status maps to the
 * transcript's shared glyph vocabulary: `CircleCheck` (done, `--ref-add`, the
 * same success cue the question/tool cards use), a pulsing `CircleDot` (in
 * progress, `--status-active` — the working hue echoing the panel's shimmer, on
 * the same stepped `grove-pulse` cadence, static under reduced motion), and a
 * quiet `Circle` (pending, muted). An in-progress item prefers Claude's
 * present-tense `active_form` phrasing when present, else its `content`.
 *
 * Test seam: `data-testid="todo-card"` (+ `data-progress`), each row
 * `data-testid="todo-item"` (+ `data-status`).
 */
export function TodoListCard({ todo }: { todo: TodoListView }) {
  const total = todo.items.length;
  const done = todo.items.filter((i) => i.status === "completed").length;

  return (
    <div
      data-testid="todo-card"
      data-progress={`${done}/${total}`}
      className="not-prose w-full min-w-0 rounded-xl border border-border/50 bg-muted/30"
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <ListChecksIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          Plan
        </span>
        {total > 0 && (
          <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
            {done}/{total}
          </span>
        )}
      </div>
      <ul className="flex flex-col gap-1 px-3 pb-2.5">
        {todo.items.map((item, i) => (
          <TodoRow key={i} item={item} />
        ))}
      </ul>
    </div>
  );
}

function TodoRow({ item }: { item: TodoItemView }) {
  const label =
    item.status === "in_progress" && item.active_form ? item.active_form : item.content;
  return (
    <li
      data-testid="todo-item"
      data-status={item.status}
      className="flex items-start gap-2 text-sm"
    >
      <StatusGlyph status={item.status} />
      <span
        className={cn(
          "min-w-0 break-words",
          item.status === "completed" && "text-muted-foreground line-through",
          item.status === "in_progress" && "font-medium text-foreground",
          item.status === "pending" && "text-foreground/80",
        )}
      >
        {label}
      </span>
    </li>
  );
}

function StatusGlyph({ status }: { status: TodoItemView["status"] }) {
  if (status === "completed") {
    return (
      <CircleCheckIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-[var(--ref-add)]" />
    );
  }
  if (status === "in_progress") {
    // Pulsing dot in the working hue, on the panel's stepped `grove-pulse`
    // cadence (terminal-cursor blink, not a smooth spinner ease); static under
    // reduced motion — the color alone then carries the "active" signal.
    return (
      <CircleDotIcon
        aria-hidden
        className="mt-0.5 size-3.5 shrink-0 animate-grove-pulse text-[var(--status-active)] motion-reduce:animate-none"
      />
    );
  }
  return <CircleIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />;
}
