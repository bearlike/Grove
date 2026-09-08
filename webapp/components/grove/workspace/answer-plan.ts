import type { AgentQuestionView, QuestionAnswerItem } from "@/lib/grove/api";

/**
 * Pure answer-plan logic for a live question batch — the client-side twin of
 * the daemon's own validation.
 *
 * One `AskUserQuestion` call is one group, and the daemon dispatches the WHOLE
 * group atomically in a single delivery. So the POST body always carries
 * exactly one item per question, in the group's order, never a partial group —
 * and this module is the one seam that builds it.
 */

/**
 * One question's local, not-yet-submitted answer: the options picked AND the
 * sentence typed, never one or the other.
 *
 * This was a union of the two for as long as the answer was driven into the
 * provider's own picker widget, where free text existed only as a single-select
 * row labelled "Type something." — so the shape of the answer was dictated by
 * the shape of that widget. The daemon now dismisses the picker and restates
 * the whole batch as prose, so "option B, and here is why" is a payload the
 * wire carries, and a union cannot express it. Two empty-able fields can, and
 * every reader loses the `kind` branch it used to carry.
 */
export interface Selection {
  indexes: number[];
  text: string;
}

/** The selection to read from for a question nobody has touched yet. */
export function selectionOf(current: Selection | undefined): Selection {
  return current ?? { indexes: [], text: "" };
}

/**
 * The bytes the daemon rejects outright — written as escapes, never as literal
 * characters, which would be invisible in this source file.
 *
 * Tab (0x09) and line feed (0x0a) are absent from the class because prose
 * contains them: an answer is no longer typed into a pane, so a line break is
 * just how people write more than one sentence. Everything else in C0 —
 * carriage return included — is escape-sequence material, and DEL with it.
 * Mirrors `QuestionAnswerItem`'s own validator.
 */
const CONTROL_CHARACTERS = /[\u0000-\u0008\u000b-\u001f\u007f]/;

/**
 * Why this text cannot be sent, or null when it can.
 *
 * Checking here is what turns a 422 into a sentence beside the field. Nobody
 * types a control character on purpose — pasting text carrying one is the
 * reachable case, and it is a normal thing to do.
 */
export function textError(text: string): string | null {
  if (!text.trim()) return null;
  return CONTROL_CHARACTERS.test(text)
    ? "Remove the control characters — line breaks and tabs are fine, the rest are not."
    : null;
}

/**
 * One selection → its wire item, or null while it still says nothing.
 *
 * Both keys may ride together and either may be omitted; only an answer with
 * neither is refused, which is exactly the daemon's own rule. Text the daemon
 * would reject yields null rather than a doomed item: the inline message is the
 * report, and there is nothing to submit until it clears.
 */
export function answerItem(selection: Selection | undefined): QuestionAnswerItem | null {
  if (!selection) return null;
  const text = selection.text.trim();
  if (text && textError(text)) return null;
  const indexes = [...selection.indexes].sort((a, b) => a - b);
  if (indexes.length === 0 && !text) return null;
  return {
    ...(indexes.length > 0 ? { selected_indexes: indexes } : {}),
    ...(text ? { text } : {}),
  };
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

/**
 * The group request to deliver after its individual cards are confirmed.
 *
 * The UI makes one answer decision and confirmation visible per card, but the
 * current endpoint only accepts a complete positional group. This predicate is
 * the seam that prevents a final card's confirmation from posting an earlier
 * card the user has selected but not yet acknowledged.
 */
export function confirmedGroupPlan(
  questions: readonly AgentQuestionView[],
  selections: Readonly<Record<string, Selection>>,
  confirmed: Readonly<Record<string, true>>,
): QuestionAnswerItem[] | null {
  const plan = buildAnswerPlan(questions, selections);
  return plan && questions.every((question) => confirmed[question.id]) ? plan : null;
}

/**
 * Toggle one option in a selection, respecting whether the question takes many.
 *
 * The free text rides through untouched: picking an option no longer discards
 * the qualification somebody typed beside it.
 */
export function toggleOption(
  current: Selection | undefined,
  index: number,
  multiselect: boolean,
): Selection {
  const { indexes, text } = selectionOf(current);
  if (!multiselect) return { indexes: [index], text };
  return {
    indexes: indexes.includes(index) ? indexes.filter((value) => value !== index) : [...indexes, index],
    text,
  };
}

/** Replace a selection's free text, keeping whichever options are chosen. */
export function typeText(current: Selection | undefined, text: string): Selection {
  return { ...selectionOf(current), text };
}
