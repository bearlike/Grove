"use client";

import { Maximize2Icon } from "lucide-react";
import { useCallback, useRef, useState, type ReactNode, type RefObject } from "react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * Move one composer into a dialog and back — the only composer behaviour
 * assistant-ui does not ship (see `webapp/CLAUDE.md`, "Expanding a surface").
 *
 * Everything a composer LOOKS like is the vendored `elements/composer` anatomy,
 * composed directly by each surface; this component owns only the move. The
 * editor is rendered in exactly one place at a time, so callers must keep their
 * draft ABOVE this boundary — the remount carries nothing.
 */
export function ExpandedComposer({
  children,
  title,
  description,
  testId,
  expandLabel,
}: {
  /** Renders the composer. `expandControl` is `null` while expanded, because
   * the dialog's own close affordance is the way back. */
  readonly children: (
    expanded: boolean,
    inputRef: RefObject<HTMLTextAreaElement | null>,
    expandControl: ReactNode,
  ) => ReactNode;
  /** The dialog's accessible name. It must NOT reuse a name something inside
   * it already has, or one screen reader announces two different things by the
   * same name, nested. */
  readonly title: string;
  readonly description: string;
  /** `<testId>-expand` and `<testId>-expanded` are what the browser suite reads. */
  readonly testId: string;
  readonly expandLabel: string;
}): ReactNode {
  const [expanded, setExpanded] = useState(false);
  const inlineInput = useRef<HTMLTextAreaElement | null>(null);

  // The original trigger unmounted with the inline editor, so restore focus explicitly.
  const restoreFocus = useCallback((event: Event) => {
    event.preventDefault();
    requestAnimationFrame(() => inlineInput.current?.focus());
  }, []);

  const expandControl = expanded ? null : (
    <TooltipIconButton
      tooltip={expandLabel}
      side="top"
      aria-label={expandLabel}
      onClick={() => setExpanded(true)}
      data-testid={`${testId}-expand`}
    >
      <Maximize2Icon />
    </TooltipIconButton>
  );

  const composer = children(expanded, inlineInput, expandControl);

  return (
    <>
      {expanded ? null : composer}
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent
          // Only layout is overridden here; the vendored dialog keeps its own
          // surface, radius and close affordance. `dvh` rather than `vh` so a
          // mobile keyboard shrinks the box instead of pushing send under it.
          className="flex h-[90dvh] max-h-[90dvh] w-[calc(100vw-2rem)] flex-col gap-4 sm:max-w-3xl"
          onCloseAutoFocus={restoreFocus}
          data-testid={`${testId}-expanded`}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {expanded ? composer : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
