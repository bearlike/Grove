"use client";

import {
  ChevronDownIcon,
  CircleCheckIcon,
  CircleDotIcon,
  CircleIcon,
  ListChecksIcon,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { TodoItemView, TodoListView } from "@/lib/grove/types";

/**
 * The agent's CURRENT todo/plan list (Claude `TodoWrite` / Codex `update_plan`),
 * drawn as a composed checklist card pinned above the composer — the
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
 * COLLAPSED BY DEFAULT, because this card sits in the composer's fixed
 * chrome, not the scroll: a long plan otherwise eats the transcript's viewport
 * on every turn and pushes the agent's actual reply off-screen. Collapsed, it is
 * one quiet line; the header carries `done/total` in BOTH states, so the plan's
 * progress is glanceable without expanding — the disclosure hides the item text,
 * never the signal. Same grammar as the transcript's `ToolGroup` expander
 * (`ai-elements/tool.tsx`): plain local state + a conditional mount (no radix
 * Collapsible, no new dep) and the `-rotate-90`-when-closed chevron.
 *
 * Test seam: `data-testid="todo-card"` (+ `data-progress`, `data-collapsed`),
 * the header trigger `data-testid="todo-toggle"`, each row
 * `data-testid="todo-item"` (+ `data-status`).
 */
export function TodoListCard({ todo }: { todo: TodoListView }) {
  const [open, setOpen] = useState(false);
  const total = todo.items.length;
  const done = todo.items.filter((i) => i.status === "completed").length;

  return (
    <div
      data-testid="todo-card"
      data-progress={`${done}/${total}`}
      data-collapsed={!open}
      className="not-prose w-full min-w-0 rounded-xl border border-border/50 bg-muted/30"
    >
      <button
        type="button"
        data-testid="todo-toggle"
        aria-expanded={open}
        // The visible count reads "1/3", which a screen reader announces as
        // "one slash three" — spell the progress out instead, keeping "Plan"
        // leading so voice control still targets it by its visible label.
        aria-label={`Plan, ${done} of ${total} complete`}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          // Square off the bottom corners while open: the trigger's hover fill
          // and focus ring otherwise curve inward mid-card, above the list.
          "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/50",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          open ? "rounded-t-xl" : "rounded-xl",
        )}
      >
        <ListChecksIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          Plan
        </span>
        {/* The progress count is the one signal that must survive collapse —
            it stays in the header in both states, never inside the disclosure. */}
        <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
          {done}/{total}
        </span>
        <ChevronDownIcon
          aria-hidden
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            !open && "-rotate-90",
          )}
        />
      </button>
      {open && (
        <ul className="flex flex-col gap-1 px-3 pb-2.5">
          {todo.items.map((item, i) => (
            <TodoRow key={i} item={item} />
          ))}
        </ul>
      )}
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
