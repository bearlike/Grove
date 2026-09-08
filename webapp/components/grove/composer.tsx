"use client";

import { Maximize2Icon } from "lucide-react";
import {
  useCallback,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
  type RefObject,
} from "react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  Composer,
  ComposerActions,
  ComposerAttachments,
  ComposerBar,
  ComposerSend,
  ComposerToolbar,
} from "@/components/elements/composer";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

// Share presentation, not the create and message submission paths. A staged
// file is `grove/attachment-file`'s row — the same one a sent turn draws.
export {
  Composer as ComposerFrame,
  ComposerActions,
  ComposerAttachments,
  ComposerSend,
  ComposerToolbar,
};

/** Keep the shared send corner 12px inside the bar, including its 1px border. */
export function ComposerBody({
  className,
  style,
  ...props
}: ComponentProps<typeof ComposerBar>): ReactNode {
  return (
    <ComposerBar
      style={{ padding: 11, ...style }}
      className={cn(
        "bg-surface-raised border-surface-edge surface-raised border",
        className,
      )}
      {...props}
    />
  );
}

/** Move one editor into a dialog. Callers keep draft state above this boundary. */
export function ExpandedComposer({
  children,
  title,
  description,
  testId,
  expandLabel,
}: {
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
