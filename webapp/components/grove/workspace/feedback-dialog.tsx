"use client";

/**
 * A PORT of `components/elements/feedback-dialog.tsx`, kept diffable against it.
 *
 * GROVE DELTA 1 — the header's mark and question come from `tone`. The vendored
 * element is a thumbs-DOWN dialog by construction (`ThumbsDownIcon`, "What went
 * wrong?", no prop reaching either), and a thumbs-up deserves the same chance to
 * say WHY, because a rating with no reason cannot be acted on.
 *
 * GROVE DELTA 2 — the type sits on the rem ramp and the card on the RAISED rung.
 * Upstream sizes its text in literal pixels (13.5px, `mono`'s 11px) and fills
 * with `paper`, whose dark fill is the OVERLAY rung. Inline under a turn, both
 * were wrong. Measured on the built app on 2026-09-30, the question rendered at
 * 13.5px beside 11.2px transcript prose, and the card was lit like a popover
 * floating over the page. It is part of the page's record, so it sits one rung
 * above the page and speaks the transcript's scale.
 *
 * GROVE DELTA 3 — sending MORPHS the card instead of replacing it. The verdict's
 * mark turns into a check, the question gives way to where the feedback went,
 * and the form folds away beneath them. Upstream swaps the whole body for a
 * one-line thank-you whose words ("tune the model") also misstate where the
 * feedback goes: it becomes scores on the turn's trace, which judges and
 * workflow changes read.
 *
 * The chips, the note, the send button and the always-mounted live region are
 * upstream's.
 */

import type { ComponentProps } from "react";
import { CheckIcon, ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  field,
  iconSwap,
  iconSwapIn,
  iconSwapOut,
  inkButton,
  labelSwap,
  labelSwapIn,
  labelSwapOut,
} from "@/components/elements/surfaces";

/** GROVE DELTA 1 — which verdict the reasons explain. */
export type FeedbackTone = "positive" | "negative";

const TONE_HEADER = {
  positive: { Icon: ThumbsUpIcon, question: "What went well?" },
  negative: { Icon: ThumbsDownIcon, question: "What went wrong?" },
} as const;

/** GROVE DELTA 3 — says where the feedback went, not a guess at its use. */
export const FEEDBACK_SENT =
  "Your feedback is saved to this turn's trace to improve the workflow.";

/** GROVE DELTA 2 — the raised rung's tuple: fill, edge, light-only elevation. */
const raisedPaper = "bg-surface-raised border border-surface-edge surface-raised";

export function FeedbackDialog({
  tone,
  reasons,
  selected,
  note,
  sent,
  onToggleReason,
  onNoteChange,
  onSubmit,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  | "children"
  | "tone"
  | "reasons"
  | "selected"
  | "note"
  | "sent"
  | "onToggleReason"
  | "onNoteChange"
  | "onSubmit"
> & {
  tone: FeedbackTone;
  reasons: readonly string[];
  selected: readonly string[];
  note: string;
  sent: boolean;
  onToggleReason?: (reason: string) => void;
  onNoteChange?: (note: string) => void;
  onSubmit?: () => void;
}) {
  const { Icon, question } = TONE_HEADER[tone];
  return (
    <div
      data-slot="feedback-dialog"
      data-tone={tone}
      data-sent={sent || undefined}
      className={cn(
        raisedPaper,
        "flex w-full max-w-sm flex-col rounded-[20px] p-4 text-sm",
        className,
      )}
      {...props}
    >
      <div className="flex items-center gap-2.5">
        <span className="bg-foreground/[0.05] text-foreground/45 grid size-7 shrink-0 place-items-center rounded-lg">
          <Icon className={cn(iconSwap, "size-3.5", sent ? iconSwapOut : iconSwapIn)} />
          <CheckIcon
            className={cn(
              iconSwap,
              "size-3.5 text-emerald-500",
              sent ? iconSwapIn : iconSwapOut,
            )}
          />
        </span>
        <span className="grid min-w-0 flex-1">
          <span
            aria-hidden={sent}
            className={cn(labelSwap, "font-medium", sent ? labelSwapOut : labelSwapIn)}
          >
            {question}
          </span>
          {/*
            Mounted whether or not the feedback has been sent, because a live
            region only announces a change that happens after it is already in
            the tree; a region created together with its text is the case AT is
            free to miss.
          */}
          <span
            role="status"
            className={cn(
              labelSwap,
              "w-auto leading-snug",
              sent ? labelSwapIn : labelSwapOut,
            )}
          >
            {sent && FEEDBACK_SENT}
          </span>
        </span>
        <span
          aria-hidden={sent}
          className={cn(
            "text-foreground/30 font-mono text-xs tracking-tight transition-opacity duration-200",
            sent && "opacity-0",
          )}
        >
          optional
        </span>
      </div>

      {/* Folds to nothing on send; `inert` takes the hidden controls out of the tab order. */}
      <div
        inert={sent}
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)] motion-reduce:transition-none",
          sent ? "grid-rows-[0fr] opacity-0" : "grid-rows-[1fr]",
        )}
      >
        {/* The side bleed keeps the note's focus ring clear of the fold's clip. */}
        <div className="-mx-1 min-h-0 overflow-hidden px-1">
          <div className="flex flex-col gap-3 pt-3">
            <div className="flex flex-wrap gap-1.5">
              {reasons.map((reason) => {
                const active = selected.includes(reason);
                const className = cn(
                  "rounded-full px-2.5 py-1 text-xs transition-[background-color,color,scale] duration-150",
                  onToggleReason && "active:scale-[0.96]",
                  active
                    ? "bg-foreground text-background"
                    : cn(
                        field,
                        "text-foreground/55",
                        onToggleReason && "hover:text-foreground/90",
                      ),
                );
                return onToggleReason ? (
                  <button
                    key={reason}
                    type="button"
                    aria-pressed={active}
                    onClick={() => onToggleReason(reason)}
                    className={className}
                  >
                    {reason}
                  </button>
                ) : (
                  <span
                    key={reason}
                    role="button"
                    aria-disabled="true"
                    aria-pressed={active}
                    className={className}
                  >
                    {reason}
                  </span>
                );
              })}
            </div>

            <textarea
              value={note}
              onChange={(event) => onNoteChange?.(event.target.value)}
              rows={2}
              placeholder="Anything else?"
              aria-label="Anything else?"
              className={cn(
                field,
                "text-foreground/80 placeholder:text-foreground/30 focus-visible:ring-foreground/20 resize-none rounded-xl px-3 py-2 text-xs outline-none focus-visible:ring-1",
              )}
            />

            {onSubmit && (
              <button
                type="button"
                onClick={onSubmit}
                className={cn(
                  inkButton,
                  "flex h-8 items-center justify-center self-end rounded-full px-3.5 text-xs font-medium",
                )}
              >
                Send feedback
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
