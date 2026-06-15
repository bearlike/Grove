import { CircleCheckIcon, CircleHelpIcon, CircleIcon, SquareIcon } from "lucide-react";
import type { AgentQuestionView } from "@/lib/grove/types";

/**
 * One structured agent question rendered as a read-only choice card (epic #74).
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
      className="not-prose w-full rounded-md border border-border bg-muted/40"
    >
      <div className="flex items-start gap-2 border-b border-border p-2">
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

      {question.options.length > 0 && (
        <ul className="flex flex-col gap-1 p-2">
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

      <div className="border-t border-border p-2">
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
