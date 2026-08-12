import type { AgentQuestionView, QuestionAnswerItem } from "@/lib/grove/api";

/**
 * Pure answer-plan logic for a live question batch — the client-side twin of
 * the daemon's own validation.
 *
 * One `AskUserQuestion` call is one group, and the daemon dispatches the WHOLE
 * group atomically in a single keystroke sequence. So the POST body always
 * carries exactly one item per question, in the group's order, never a partial
 * group — and this module is the one seam that builds it.
 */

/** One question's local, not-yet-submitted answer. */
export type Selection =
  | { kind: "indexes"; indexes: number[] }
  | { kind: "text"; text: string };

/**
 * Whether this question will accept an answer the agent did not offer.
 *
 * SINGLE-SELECT ONLY, and that is the daemon's rule rather than a UI
 * preference: the answer is typed into a tmux pane, and the only verified
 * keystroke path for free text is the picker's synthetic "Type something."
 * option, which exists on a single-select question and nowhere else
 * (`ClaudeCodeAdapter._answer_ops` raises for every other kind, and the manager
 * maps that raise to a 422). Offering the box anywhere else would render an
 * affordance the wire refuses.
 */
export function acceptsCustomText(question: AgentQuestionView): boolean {
  return question.kind === "single_select";
}

/**
 * The bytes the daemon rejects outright — written as escapes, never as literal
 * characters, which would be invisible in this source file.
 *
 * The answer is typed into a pane with `send-keys -l`, and the whole grammar
 * rests on a closed key vocabulary: an ESC cancels the question, and a CR acts
 * as an early Enter that desyncs the positional driver. Mirrors
 * `QuestionAnswerItem`'s own validator (`ord < 0x20 or ord == 0x7f`).
 */
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;

/**
 * Why this text cannot be sent, or null when it can.
 *
 * Checking here is what turns a 422 into a sentence beside the field. Nobody
 * can TYPE a control character into a single-line input — pasting a wrapped
 * line is the reachable case, and it is a normal thing to do.
 */
export function textError(text: string): string | null {
  if (!text.trim()) return null;
  return CONTROL_CHARACTERS.test(text)
    ? "Single line only — remove the line breaks or tabs."
    : null;
}

/**
 * Whether the batch needs an explicit Submit rather than sending on the tap
 * that completes it.
 *
 * Mirrors the terminal's own grammar: it shows a review step whenever there is
 * more than one question or any multi-select, and a lone single-select submits
 * on its one tap. Diverging here would make the two surfaces disagree about
 * what a tap means.
 */
export function needsExplicitSubmit(questions: readonly AgentQuestionView[]): boolean {
  return questions.length > 1 || questions.some((question) => question.multiselect);
}

/** One selection → its wire item, or null while still incomplete. */
export function answerItem(selection: Selection | undefined): QuestionAnswerItem | null {
  if (!selection) return null;
  if (selection.kind === "text") {
    const text = selection.text.trim();
    // A plan is never built from text the daemon will refuse: the inline
    // message is the report, and there is nothing to submit until it clears.
    return text && !textError(text) ? { text } : null;
  }
  return selection.indexes.length > 0
    ? { selected_indexes: [...selection.indexes].sort((a, b) => a - b) }
    : null;
}

/**
 * The full ordered `answers` array for a batch, or null while any question in
 * it is still unanswered locally.
 */
export function buildAnswerPlan(
  questions: readonly AgentQuestionView[],
  selections: Readonly<Record<string, Selection>>,
): QuestionAnswerItem[] | null {
  const items: QuestionAnswerItem[] = [];
  for (const question of questions) {
    const item = answerItem(selections[question.id]);
    if (!item) return null;
    items.push(item);
  }
  return items;
}

/** Toggle one option in a selection, respecting whether the question takes many. */
export function toggleOption(
  current: Selection | undefined,
  index: number,
  multiselect: boolean,
): Selection {
  if (!multiselect) return { kind: "indexes", indexes: [index] };
  const indexes = current?.kind === "indexes" ? current.indexes : [];
  return {
    kind: "indexes",
    indexes: indexes.includes(index)
      ? indexes.filter((value) => value !== index)
      : [...indexes, index],
  };
}
