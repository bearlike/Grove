import type { ComponentProps } from "react";

import type { AgentPlan } from "@/components/elements/agent-plan";
import type { TodoItem, TodoList } from "@/components/elements/todo-list";
import type { SessionTurnView, TodoListView } from "@/lib/grove/api";

/**
 * The agent's plan → props for the vendored `TodoList` / `AgentPlan`.
 *
 * A todo write is a full REWRITE of the list, so only the newest one is the
 * agent's current plan — every historical write would otherwise stack a near
 * duplicate card into the transcript. Hence the list is derived here and pinned
 * as a SIBLING of the message stream, never mapped as a message.
 */

/** The data half of `TodoList`'s props — the consumer owns layout and `revision`. */
export type TodoListProps = Pick<ComponentProps<typeof TodoList>, "items">;

/** The data half of `AgentPlan`'s props. */
export type AgentPlanProps = Pick<ComponentProps<typeof AgentPlan>, "steps" | "activeIndex">;

/*
 * THERE IS NO `latestTodoFromTurns` HERE ANY MORE, and that is deliberate.
 *
 * It scanned the loaded turns for the newest todo write, which is correct only
 * while the client holds the WHOLE session from turn 0. The transcript is now
 * windowed to its tail (`useSessionTurns`'s `INITIAL_TURN_WINDOW`), so a plan
 * the agent last wrote hundreds of turns back is simply not in the data — the
 * card would have blanked on open and reappeared only on the next write.
 * `useWorkspaceTodo` reads the daemon's own `latest_todo` seam instead, which
 * is right regardless of how much transcript the client happens to hold.
 */

/** The wire list → `TodoList` items. An in-progress item prefers its
 * present-tense `active_form` ("Wiring the parser" over "Wire the parser"). */
export function todoListProps(todo: TodoListView): TodoListProps {
  const items: TodoItem[] = todo.items.map((item, index) => ({
    // Content is not unique (an agent may repeat a step), so position is the id.
    id: `todo-${index}`,
    text: (item.status === "in_progress" && item.active_form) || item.content,
    status:
      item.status === "completed" ? "done" : item.status === "in_progress" ? "active" : "pending",
  }));
  return { items };
}

/**
 * The wire list → `AgentPlan` props, for the surfaces that want the linear
 * progress read rather than the checklist.
 *
 * `AgentPlan.activeIndex` is a COUNT of finished steps (it feeds `progressOf`),
 * not the index of the active one — so a list whose completed items are not a
 * prefix still reports honestly rather than marking later steps done.
 */
export function agentPlanProps(todo: TodoListView): AgentPlanProps {
  return {
    steps: todo.items.map((item) => item.content),
    activeIndex: todo.items.filter((item) => item.status === "completed").length,
  };
}
