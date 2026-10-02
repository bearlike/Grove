"use client";

/**
 * GROVE DELTA 17 — the action bar under a turn's ANSWER, and the follow-up that
 * asks WHY.
 *
 * The bar is upstream's `AssistantActionBar` (`components/assistant-ui/thread.tsx`)
 * with Reload and the More → Export menu removed and the two feedback
 * primitives added — the same `TooltipIconButton` markup, classes and icon
 * swap. Reload stays out for the reason delta 5 gives: the daemon has no
 * regenerate verb. The thumbs are assistant-ui's own
 * `ActionBarPrimitive.FeedbackPositive/Negative`, which render only while the
 * runtime carries a feedback adapter, so where Langfuse is not configured
 * (and on a read-only transcript) the bar is Copy alone.
 *
 * EITHER thumb records its verdict at once and then opens `./feedback-dialog`
 * (the vendored dialog ported to take a tone) beneath the bar, for optional
 * reasons and a note; sending it replaces the verdict with the fuller one. A
 * thumbs-up asks what went well because a rating with no reason cannot be acted
 * on in either direction. The dialog is inline rather than a popover because it
 * is part of this turn's record, and a popover over a scrolling transcript
 * detaches from the row it annotates.
 *
 * The footer is spaced away from the answer above it and from the next turn
 * below it: pressed flush against the last line of prose, the bar read as part
 * of the text rather than as controls about it.
 */

import { useState, type FC } from "react";
import { ActionBarPrimitive, AuiIf, useAuiState } from "@assistant-ui/react";
import {
  CheckIcon,
  CopyIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  type Rating,
  turnAnswerOf,
  useTurnFeedbackControls,
} from "@/lib/grove/runtime/feedback";

import { FeedbackDialog } from "./feedback-dialog";
import { TurnFiles } from "./turn-files";

export const TurnActions: FC = () => {
  const startedAt = useAuiState((s) => turnAnswerOf(s.message.metadata.custom));
  // Which verdict's follow-up is open; a flip swaps it, re-clicking keeps it.
  const [followUp, setFollowUp] = useState<Rating | null>(null);
  if (!startedAt) return null;
  return (
    <div data-testid="turn-actions" className="mt-3 mb-2 flex flex-col gap-3">
      <TurnFiles startedAt={startedAt} />
      <div
        data-slot="aui_assistant-message-footer"
        className="ms-2 flex min-h-7.5 items-center"
      >
        <ActionBarPrimitive.Root className="aui-assistant-action-bar-root text-muted-foreground animate-in fade-in -ms-1 flex gap-1 duration-200">
          <ActionBarPrimitive.Copy asChild>
            <TooltipIconButton tooltip="Copy">
              <AuiIf condition={(s) => s.message.isCopied}>
                <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
              </AuiIf>
              <AuiIf condition={(s) => !s.message.isCopied}>
                <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
              </AuiIf>
            </TooltipIconButton>
          </ActionBarPrimitive.Copy>
          <AuiIf condition={(s) => s.thread.capabilities.feedback}>
            <ActionBarPrimitive.FeedbackPositive
              asChild
              onClick={() => setFollowUp("positive")}
            >
              <TooltipIconButton
                tooltip="Good response"
                className="data-[submitted]:text-foreground"
              >
                <ThumbsUpIcon />
              </TooltipIconButton>
            </ActionBarPrimitive.FeedbackPositive>
            <ActionBarPrimitive.FeedbackNegative
              asChild
              onClick={() => setFollowUp("negative")}
            >
              <TooltipIconButton
                tooltip="Bad response"
                className="data-[submitted]:text-foreground"
              >
                <ThumbsDownIcon />
              </TooltipIconButton>
            </ActionBarPrimitive.FeedbackNegative>
          </AuiIf>
        </ActionBarPrimitive.Root>
      </div>
      {followUp && (
        // Keyed by verdict: a flipped vote opens a fresh follow-up, never one
        // still holding the other verdict's ticked reasons.
        <FeedbackFollowUp key={followUp} startedAt={startedAt} tone={followUp} />
      )}
    </div>
  );
};

/** The ported dialog, driven with plain state — it owns no behaviour. */
const FeedbackFollowUp: FC<{ startedAt: string; tone: Rating }> = ({
  startedAt,
  tone,
}) => {
  const controls = useTurnFeedbackControls();
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  if (!controls) return null;
  return (
    <FeedbackDialog
      className="ms-2"
      tone={tone}
      reasons={controls.reasons[tone]}
      selected={selected}
      note={note}
      sent={sent}
      onToggleReason={(reason) =>
        setSelected((current) =>
          current.includes(reason)
            ? current.filter((r) => r !== reason)
            : [...current, reason],
        )
      }
      onNoteChange={setNote}
      onSubmit={() =>
        void controls
          .submit(startedAt, { rating: tone, reasons: [...selected], note })
          .then(
            () => setSent(true),
            () => {},
          )
      }
    />
  );
};
