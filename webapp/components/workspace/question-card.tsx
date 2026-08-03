import { useState } from "react";
import { CircleCheckIcon, CircleHelpIcon, CircleIcon, SquareIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type { AgentQuestionView } from "@/lib/grove/types";
import {
  buildAnswerPlan,
  needsExplicitSubmit,
  type QuestionAnswerItem,
  type QuestionSelection,
} from "@/lib/grove/question-plan";

/**
 * One structured agent question rendered as a read-only choice card.
 * The agent paused to ask the human something; the transcript draws the prompt,
 * its options, and whether it has been answered. Options are a STATIC list, not
 * interactive controls — answer-back is a future write-path (the wire's
 * `id`/`group_id` are the addresses it will key on), so nothing here is a
 * button or input. Iconography is the transcript's shared vocabulary: the
 * question concept is one `CircleHelp` glyph, the answered cue the same
 * `CircleCheck` the tool block uses for success; option glyphs mirror the
 * question's shape (multiselect → square, single-select/confirm → circle), so
 * the choice rule is legible at a glance. An answered question reads its
 * `answer` behind the check; an unanswered one reads a quiet "awaiting answer".
 *
 * Disclosure-free by design (a question is short and load-bearing — it stays
 * open), unlike the `Tool`/`ToolGroup` rows it sits beside. The `--ref-info`
 * accent is the agent-identity hue (the agent is speaking), matching `RoleLabel`.
 *
 * Test seam: `data-testid="question-card"` + `data-answered`,
 * `"question-option"`, `"question-answer"` (answered) / `"question-pending"`.
 */
export function QuestionCard({ question }: { question: AgentQuestionView }) {
  const { answered, multiselect } = question;
  const OptionGlyph = multiselect ? SquareIcon : CircleIcon;

  return (
    <div
      data-testid="question-card"
      data-answered={answered}
      className="not-prose w-full rounded-lg bg-muted/30"
    >
      <QuestionHeader question={question} />

      {question.options.length > 0 && (
        <ul className="flex flex-col gap-1 px-2 py-1.5">
          {question.options.map((option, i) => (
            <li
              key={i}
              data-testid="question-option"
              className="flex items-start gap-2 text-sm text-foreground/90"
            >
              <OptionGlyph
                aria-hidden
                className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
              />
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="break-words">{option.label}</span>
                {option.description && (
                  <span className="break-words text-xs text-muted-foreground">
                    {option.description}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="px-2 py-1.5">
        {answered ? (
          <p
            data-testid="question-answer"
            className="flex items-start gap-1.5 break-words text-sm text-foreground"
          >
            <CircleCheckIcon
              aria-hidden
              className="mt-0.5 size-3.5 shrink-0 text-[var(--ref-add)]"
            />
            <span>{question.answer || "answered"}</span>
          </p>
        ) : (
          <p
            data-testid="question-pending"
            className="text-xs italic text-muted-foreground"
          >
            awaiting answer
          </p>
        )}
      </div>
    </div>
  );
}

/** The header block (glyph + header label + prompt) shared by the read-only
 * card and every interactive answer block below — one place for "what does a
 * question's identity look like". */
function QuestionHeader({ question }: { question: AgentQuestionView }) {
  return (
    <div className="flex items-start gap-2 px-2 py-1.5">
      <CircleHelpIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-[var(--ref-info)]" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {question.header && (
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {question.header}
          </span>
        )}
        <span className="break-words text-sm font-medium text-foreground">{question.prompt}</span>
      </div>
    </div>
  );
}

/**
 * The interactive twin of `QuestionCard` for a LIVE pending question.
 * `AskUserQuestion` blocks the agent until answered and never reaches the
 * transcript until it resolves (research-findings.md), so this renders
 * straight off the SSE-sourced payload (`useActivityStream` +
 * `livePendingQuestion`), never `/turns`.
 *
 * `questions` is the pending GROUP sharing one `tool_use_id` — the wire's
 * `AgentActivityView.questions` is a list, so an `AskUserQuestion` batch of
 * several questions streams and answers atomically in one prop. This
 * component's prop IS that whole group; `question-plan.ts`'s pure logic
 * already operates over N questions.
 * A lone single-select/free-text question submits on the one tap/Enter that
 * completes it (no review step, mirroring the terminal); a multiSelect member
 * or more than one question always shows an explicit Submit, mirroring the
 * terminal's tab-bar review step. `confirm` (ExitPlanMode) has no keystroke
 * driver yet (core CLAUDE.md's keystroke builder only covers AskUserQuestion's
 * three kinds) — it degrades to the exact read-only `QuestionCard`, same as
 * an error does.
 *
 * Test seam: `data-testid="pending-question-card"`, `"question-option-button"`,
 * `"question-checkbox"`, `"question-other-input"`, `"question-submit"`,
 * `"question-error"` (falls back to `"question-card"` read-only rendering).
 */
export function PendingQuestionCard({
  questions,
  onSubmit,
  submitting,
  error,
}: {
  questions: AgentQuestionView[];
  onSubmit: (answers: QuestionAnswerItem[]) => void;
  submitting: boolean;
  error: string | null;
}) {
  const [selections, setSelections] = useState<Record<string, QuestionSelection>>({});

  // No keystroke driver for `confirm` (ExitPlanMode) yet, and a failed
  // dispatch (409/422) means the optimistic controls are no longer trustworthy
  // — both degrade to the plain read-only rendering; the SSE stream is the
  // thing that will actually confirm what happens next.
  const confirmOnly = questions.every((q) => q.kind === "confirm");
  if (error || confirmOnly) {
    return (
      <div className="flex flex-col gap-2">
        {error && (
          <p
            data-testid="question-error"
            role="alert"
            className="text-xs text-[var(--status-error)]"
          >
            {error}
          </p>
        )}
        {questions.map((q) => (
          <QuestionCard key={q.id} question={q} />
        ))}
      </div>
    );
  }

  const explicitSubmit = needsExplicitSubmit(questions);

  const setSelection = (id: string, selection: QuestionSelection) => {
    const next = { ...selections, [id]: selection };
    setSelections(next);
    // A lone auto-submit question fires the instant its one answer lands —
    // no review step exists for that case in the terminal grammar either.
    // Kept OUTSIDE the setState updater: React.StrictMode double-invokes a
    // functional updater to surface impurities, and `onSubmit` is a real POST
    // dispatch, not a pure state derivation — inside the updater it fired the
    // request twice in dev.
    if (!explicitSubmit) {
      const plan = buildAnswerPlan(questions, next);
      if (plan) onSubmit(plan);
    }
  };

  const plan = buildAnswerPlan(questions, selections);

  return (
    <div data-testid="pending-question-card" className="flex flex-col gap-2">
      {questions.map((q) => (
        <QuestionAnswerBlock
          key={q.id}
          question={q}
          selection={selections[q.id]}
          disabled={submitting}
          onChange={(selection) => setSelection(q.id, selection)}
        />
      ))}
      {explicitSubmit && (
        <Button
          type="button"
          size="sm"
          data-testid="question-submit"
          disabled={submitting || !plan}
          onClick={() => plan && onSubmit(plan)}
        >
          {submitting ? "Submitting…" : "Submit"}
        </Button>
      )}
    </div>
  );
}

/**
 * One question's interactive controls inside a `PendingQuestionCard` group.
 * Purely a local-selection reporter — it never submits itself; the group
 * decides submit timing (auto vs explicit) from every block's `onChange`.
 */
function QuestionAnswerBlock({
  question,
  selection,
  disabled,
  onChange,
}: {
  question: AgentQuestionView;
  selection: QuestionSelection | undefined;
  disabled: boolean;
  onChange: (selection: QuestionSelection) => void;
}) {
  // Free text is a draft until committed (Enter/blur) — every keystroke must
  // NOT count as a completed answer, unlike an option tap/checkbox toggle
  // which commits immediately.
  const [draft, setDraft] = useState(selection?.kind === "text" ? selection.text : "");
  const selectedIndex = selection?.kind === "index" ? selection.index : null;
  const selectedIndexes = selection?.kind === "indexes" ? selection.indexes : [];

  const commitText = () => {
    const trimmed = draft.trim();
    if (trimmed) onChange({ kind: "text", text: trimmed });
  };

  return (
    <div className="w-full rounded-lg bg-muted/30">
      <QuestionHeader question={question} />

      {question.options.length > 0 && (
        <ul className="flex flex-col gap-1 px-2 py-1.5">
          {question.options.map((option, i) => {
            const checked = question.multiselect
              ? selectedIndexes.includes(i)
              : selectedIndex === i;
            if (question.multiselect) {
              return (
                <li key={i}>
                  <label
                    className={cn(
                      "flex items-start gap-2 rounded-sm p-1 text-sm text-foreground/90",
                      !disabled && "cursor-pointer hover:bg-accent",
                    )}
                  >
                    <input
                      type="checkbox"
                      data-testid="question-checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => {
                        const next = checked
                          ? selectedIndexes.filter((x) => x !== i)
                          : [...selectedIndexes, i];
                        onChange({ kind: "indexes", indexes: next });
                      }}
                      className="mt-0.5 size-3.5 shrink-0 accent-[var(--ref-add)]"
                    />
                    <div className="flex min-w-0 flex-1 flex-col">
                      <span className="break-words">{option.label}</span>
                      {option.description && (
                        <span className="break-words text-xs text-muted-foreground">
                          {option.description}
                        </span>
                      )}
                    </div>
                  </label>
                </li>
              );
            }
            return (
              <li key={i}>
                <button
                  type="button"
                  data-testid="question-option-button"
                  disabled={disabled}
                  onClick={() => onChange({ kind: "index", index: i })}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-sm p-1 text-left text-sm text-foreground/90 transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                    !disabled && "hover:bg-accent",
                    disabled && "opacity-50",
                  )}
                >
                  {checked ? (
                    <CircleCheckIcon
                      aria-hidden
                      className="mt-0.5 size-3.5 shrink-0 text-[var(--ref-add)]"
                    />
                  ) : (
                    <CircleIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="break-words">{option.label}</span>
                    {option.description && (
                      <span className="break-words text-xs text-muted-foreground">
                        {option.description}
                      </span>
                    )}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/* "Other" free text — non-multiSelect questions only (v1 wire rule):
          an optionless free_text-kind question renders ONLY this input. */}
      {!question.multiselect && (
        <div className="px-2 py-1.5">
          <input
            type="text"
            data-testid="question-other-input"
            placeholder={question.options.length > 0 ? "Other…" : "Type your answer…"}
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitText}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitText();
              }
            }}
            className="w-full rounded-sm border border-border bg-background px-2 py-1 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
          />
        </div>
      )}
    </div>
  );
}
