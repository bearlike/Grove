import type { AgentQuestionView } from "./types";

/**
 * Pure answer-plan logic for a live pending `AskUserQuestion` — the
 * client-side twin of the daemon's answer-plan validation
 * (`core/contracts/`), kept apart from `question-card.tsx` the same way
 * `chat-turns.ts` is kept apart from the chat panel. A "group" is every
 * question sharing one `group_id` (the tool's `tool_use_id`) — the daemon's
 * `POST .../question-answer` dispatches the WHOLE group atomically in one
 * keystroke sequence, so the wire's `answers` array always carries one item
 * per question, in order, never a partial group.
 */

/** One wire answer item — exactly one key, matching the frozen contract. */
export type QuestionAnswerItem = { selected_indexes: number[] } | { text: string };

/** One question's local, not-yet-submitted answer state. */
export type QuestionSelection =
  | { kind: "index"; index: number }
  | { kind: "indexes"; indexes: number[] }
  | { kind: "text"; text: string };

/**
 * The controls a `ChatItem` of kind "question" needs to render its LIVE
 * pending variant instead of the read-only card — stamped onto the item by
 * the chat panel / turns view (never by the pure `chatItemsFromTurns` mapper,
 * which only ever produces historical, non-interactive items).
 */
export interface QuestionInteraction {
  onSubmit: (answers: QuestionAnswerItem[]) => void;
  submitting: boolean;
  error: string | null;
}

/**
 * Whether the group needs an explicit Submit control rather than
 * auto-submitting on the one tap that completes it. Mirrors the terminal's own
 * review step (research-findings.md): a review tab exists whenever there is
 * more than one question OR any multiSelect member; a lone single-select or
 * free-text question submits immediately on its one tap/Enter, exactly like
 * the terminal's no-review-step case.
 */
export function needsExplicitSubmit(questions: AgentQuestionView[]): boolean {
  return questions.length > 1 || questions.some((q) => q.multiselect);
}

/**
 * One question's local selection → its wire answer item, or `null` while
 * still incomplete (nothing picked / toggled / typed yet) — the signal
 * `buildAnswerPlan` uses to know the group isn't ready to submit.
 */
export function answerItemFor(
  _question: AgentQuestionView,
  selection: QuestionSelection | undefined,
): QuestionAnswerItem | null {
  if (!selection) return null;
  switch (selection.kind) {
    case "text": {
      const text = selection.text.trim();
      return text ? { text } : null;
    }
    case "index":
      return { selected_indexes: [selection.index] };
    case "indexes":
      return selection.indexes.length > 0
        ? { selected_indexes: [...selection.indexes].sort((a, b) => a - b) }
        : null;
  }
}

/**
 * Build the full ordered `answers` array for a pending group, or `null` if
 * any question in it is still unanswered locally. The POST body requires
 * exactly one item per question, in the group's order — this is the one seam
 * that produces it, so the card and its tests share the same rule the daemon
 * validates against.
 */
export function buildAnswerPlan(
  questions: AgentQuestionView[],
  selections: Record<string, QuestionSelection>,
): QuestionAnswerItem[] | null {
  const items: QuestionAnswerItem[] = [];
  for (const q of questions) {
    const item = answerItemFor(q, selections[q.id]);
    if (!item) return null;
    items.push(item);
  }
  return items;
}
