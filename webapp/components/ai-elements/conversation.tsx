"use client";

// Vendored from Vercel AI Elements (registry.ai-sdk.dev/conversation), now
// house code — see webapp/CLAUDE.md "AI Elements" lesson. Only dep is
// `use-stick-to-bottom`. Deliberate exception to the house ScrollArea rule:
// StickToBottom's stick mechanics require owning its own scroll viewport
// (it measures + drives scrollTop on that element), so wrapping it in
// `<ScrollArea>` would produce two scroll containers and break the
// follow-the-bottom behavior that is this component's entire job.

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ArrowDownIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { useCallback } from "react";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";

export type ConversationProps = ComponentProps<typeof StickToBottom>;

export const Conversation = ({ className, ...props }: ConversationProps) => (
  <StickToBottom
    // Deviations from upstream: `min-w-0` so a wide transcript child can never
    // widen the page through the flex/grid ancestor chain (it scrolls inside
    // the StickToBottom viewport instead), and `initial="instant"` (upstream
    // "smooth") so a long transcript OPENS at its tail immediately — animating
    // the first scroll from the top reads as a glitch and races tab switches.
    // New content arriving later still animates via `resize`.
    className={cn("relative min-w-0 flex-1 overflow-y-hidden", className)}
    initial="instant"
    resize="smooth"
    role="log"
    {...props}
  />
);

export type ConversationContentProps = ComponentProps<typeof StickToBottom.Content>;

export const ConversationContent = ({ className, ...props }: ConversationContentProps) => (
  // min-w-0 deviation: same page-width containment as Conversation above.
  <StickToBottom.Content className={cn("flex min-w-0 flex-col gap-3 p-3", className)} {...props} />
);

export type ConversationEmptyStateProps = ComponentProps<"div"> & {
  title?: string;
  description?: string;
};

export const ConversationEmptyState = ({
  className,
  title = "No messages yet",
  description,
  children,
  ...props
}: ConversationEmptyStateProps) => (
  <div
    className={cn(
      "flex size-full flex-col items-center justify-center gap-1 p-6 text-center",
      className,
    )}
    {...props}
  >
    {children ?? (
      <>
        <h3 className="text-sm font-medium">{title}</h3>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </>
    )}
  </div>
);

export type ConversationScrollButtonProps = ComponentProps<typeof Button>;

export const ConversationScrollButton = ({
  className,
  ...props
}: ConversationScrollButtonProps) => {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();

  const handleScrollToBottom = useCallback(() => {
    scrollToBottom();
  }, [scrollToBottom]);

  return (
    !isAtBottom && (
      <Button
        aria-label="Scroll to bottom"
        className={cn("absolute bottom-4 left-[50%] translate-x-[-50%] rounded-md", className)}
        onClick={handleScrollToBottom}
        size="icon-sm"
        type="button"
        variant="outline"
        {...props}
      >
        <ArrowDownIcon className="size-4" />
      </Button>
    )
  );
};
